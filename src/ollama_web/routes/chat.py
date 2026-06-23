"""SSE streaming chat endpoint."""

from __future__ import annotations

import functools
import inspect
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from ollama import Message
from starlette.requests import Request
from starlette.responses import StreamingResponse

from ..sessions import SessionStore
from ..tools.fetch import pop_fetched_files
from ..tools.registry import ToolRegistry, default_registry

logger = logging.getLogger("ollama_web.chat")


def _parse_messages(raw: list[dict[str, Any]]) -> list[Message]:
    """Convert raw JSON message dicts into ``ollama.Message`` objects."""
    out: list[Message] = []
    for m in raw:
        role = m.get("role", "user")
        content = m.get("content", "")
        name = m.get("name")
        images = m.get("images")
        kwargs: dict[str, Any] = {"role": role, "content": content}
        if name:
            kwargs["name"] = name
        if images:
            kwargs["images"] = images
        out.append(Message(**kwargs))
    return out


def _session_aware_registry(session_id: str | None) -> ToolRegistry:
    """Return a registry whose search_and_fetch receives the session id."""
    reg = default_registry()

    original = reg.get("search_and_fetch")
    if original is not None and session_id is not None:

        @functools.wraps(original)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            sig = inspect.signature(original)
            bound = sig.bind(*args, **kwargs)
            bound.arguments["session_id"] = session_id
            return original(*bound.args, **bound.kwargs)

        reg._tools["search_and_fetch"] = wrapped  # noqa: SLF001

    return reg


def _attachment_context(store: SessionStore, session_id: str, file_ids: list[str]) -> str:
    parts: list[str] = []
    for file_id in file_ids:
        chat_file = store.get_file(session_id, file_id)
        if chat_file is None:
            continue
        text = chat_file.text or ""
        parts.append(
            f"<attached-file name=\"{chat_file.name}\" id=\"{chat_file.id}\" "
            f"source=\"{chat_file.source}\">\n{text}\n</attached-file>"
        )
    if not parts:
        return ""
    return "\n\n".join(["以下はユーザーが添付したファイルの内容です：", *parts])


async def _event_stream(
    request: Request,
    payload: dict[str, Any],
    session_id: str | None,
) -> AsyncIterator[bytes]:
    """Yield SSE events from the tool-augmented chat loop."""
    model = payload.get("model") or request.app.state.settings.default_model
    think = payload.get("think")

    store: SessionStore = request.app.state.session_store
    file_ids: list[str] = list(payload.get("file_ids", []))

    session_messages: list[Message] = []
    if session_id:
        session = store.get(session_id)
        if session is not None:
            session_messages = _parse_messages(session.get("messages", []))

    user_content = ""
    raw_messages = payload.get("messages", [])
    if raw_messages:
        user_content = str(raw_messages[-1].get("content", "") or "")

    attachment_ctx = ""
    if session_id and file_ids:
        attachment_ctx = _attachment_context(store, session_id, file_ids)

    if attachment_ctx and user_content:
        # Inject attachment context into the latest user message.
        augmented = f"{user_content}\n\n{attachment_ctx}"
        if raw_messages:
            raw_messages[-1]["content"] = augmented
    elif attachment_ctx:
        raw_messages.append({"role": "user", "content": attachment_ctx})

    messages = _parse_messages(raw_messages)
    if session_messages:
        messages = session_messages + messages

    host = request.app.state.settings.ollama_host

    from ollama_web import llm  # local import to avoid circular at module load

    registry = _session_aware_registry(session_id)

    assistant_text = ""
    finished = False
    async for event in llm.astream_chat_with_tools(messages, model, think=think, host=host, registry=registry):
        if event.get("type") == "delta":
            assistant_text += event.get("content", "")
        elif event.get("type") == "done":
            finished = True
        data = json.dumps(event, ensure_ascii=False)
        yield f"data: {data}\n\n".encode()

    # Persist user and assistant messages to the session.
    if session_id and user_content:
        store.add_message(
            session_id=session_id,
            role="user",
            content=user_content,
            attachments=file_ids,
        )
        if assistant_text:
            store.add_message(
                session_id=session_id,
                role="assistant",
                content=assistant_text,
            )

    # Persist fetched files from tools like search_and_fetch.
    if session_id:
        fetched = pop_fetched_files(session_id)
        for f in fetched:
            store.add_file(
                session_id=session_id,
                name=f["name"],
                data=f["data"],
                mime=f["mime"],
                text=f["text"],
                source="fetch",
            )


async def chat(request: Request) -> StreamingResponse:
    """POST /api/chat: stream chat events as Server-Sent Events."""
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.error("failed to parse request json: %s", exc)
        data = json.dumps({"type": "error", "message": f"Invalid JSON: {exc}"}, ensure_ascii=False)
        return StreamingResponse(
            _single_event(data),
            media_type="text/event-stream",
        )

    session_id = payload.get("session_id")
    msg_count = len(payload.get("messages", []))
    logger.info("chat request model=%s session=%s messages=%d", payload.get("model"), session_id, msg_count)
    return StreamingResponse(
        _event_stream(request, payload, session_id),
        media_type="text/event-stream",
    )


async def _single_event(data: str) -> AsyncIterator[bytes]:
    """Yield a single SSE event and finish."""
    yield f"data: {data}\n\n".encode()