"""SSE streaming chat endpoint."""

from __future__ import annotations

import functools
import html
import inspect
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from ollama import Message
from ollama._types import Image as OllamaImage
from starlette.requests import Request
from starlette.responses import StreamingResponse

from ..config import settings
from ..i18n import t
from ..mcp import collect_mcp_tools, make_mcp_executor
from ..prompts import get_prompt
from ..sessions import SessionStore, is_valid_id
from ..tools.fetch import pop_fetched_files
from ..tools.helper.image import resize_image
from ..tools.registry import ToolRegistry, default_registry

logger = logging.getLogger("ollama_web.chat")


# System prompt injected when tools are enabled. Encourages the model to use
# tools iteratively rather than firing many searches at once.
_TOOL_SYSTEM_PROMPT = get_prompt("tool_system")


def _limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def _parse_messages(raw: list[dict[str, Any]]) -> list[Message]:
    """Convert raw JSON message dicts into ``ollama.Message`` objects."""
    out: list[Message] = []
    for m in raw:
        role = m.get("role", "user")
        content = _limit_text(str(m.get("content", "") or ""), 20000)
        name = m.get("name")
        images = m.get("images")
        kwargs: dict[str, Any] = {"role": role, "content": content}
        if name:
            kwargs["name"] = name
        if images:
            # ollama expects a sequence of ollama._types.Image objects.
            kwargs["images"] = [OllamaImage(value=img) for img in images]
        out.append(Message(**kwargs))
    return out


async def _build_registry(session_id: str | None) -> ToolRegistry:
    """Return a registry with built-in tools and any configured MCP tools."""
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

    try:
        mcp_tools = await collect_mcp_tools()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to collect MCP tools: %s", exc)
        mcp_tools = []

    tool_names = [t.get("function", {}).get("name") for t in mcp_tools]
    logger.info("collected %d MCP tool(s): %s", len(mcp_tools), tool_names)

    if mcp_tools:
        executor = make_mcp_executor()
        for tool_def in mcp_tools:
            fn = tool_def.get("function", {})
            name = fn.get("name")
            if not name:
                continue
            try:
                reg.register_mcp_tool(name, tool_def, executor)
            except ValueError as exc:
                logger.warning("skipping duplicate MCP tool %s: %s", name, exc)

    return reg


def _attachment_context(store: SessionStore, session_id: str, file_ids: list[str]) -> str:
    parts: list[str] = []
    for file_id in file_ids:
        chat_file = store.get_file(session_id, file_id)
        if chat_file is None or chat_file.is_image:
            continue
        text = _limit_text(chat_file.text or "", settings.max_attachment_text_chars)
        parts.append(
            f"<attached-file name=\"{html.escape(chat_file.name)}\" id=\"{chat_file.id}\" "
            f"source=\"{chat_file.source}\">\n{text}\n</attached-file>"
        )
    if not parts:
        return ""
    return "\n\n".join([get_prompt("attachment_prefix"), *parts])


def _collect_images(store: SessionStore, session_id: str, file_ids: list[str]) -> list[bytes]:
    images: list[bytes] = []
    for file_id in file_ids:
        chat_file = store.get_file(session_id, file_id)
        if chat_file is None or not chat_file.is_image:
            continue
        data = store.get_file_data(session_id, file_id)
        if data is None:
            continue
        try:
            resized = resize_image(data, name=chat_file.name, max_dimension=1024, quality=85)
            images.append(resized)
        except Exception:  # noqa: BLE001
            logger.warning(
                "failed to resize image %s, falling back to original bytes", chat_file.name
            )
            images.append(data)
    return images


async def _event_stream(
    request: Request,
    payload: dict[str, Any],
    session_id: str | None,
) -> AsyncIterator[bytes]:
    """Yield SSE events from the tool-augmented chat loop."""
    # Send a keepalive comment immediately so the browser knows the connection is alive.
    yield b":\n\n"
    async for chunk in _chat_event_stream(request, payload, session_id):
        data = json.dumps(chunk, ensure_ascii=False)
        yield f"data: {data}\n\n".encode()


async def _chat_event_stream(
    request: Request,
    payload: dict[str, Any],
    session_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Core chat loop yielding events. Exceptions are converted to SSE error events."""
    model = payload.get("model") or request.app.state.settings.default_model
    think = payload.get("think")

    store: SessionStore = request.app.state.session_store
    file_ids: list[str] = [
        str(file_id) for file_id in payload.get("file_ids", []) if is_valid_id(str(file_id))
    ]
    user_content = ""
    assistant_text = ""

    try:
        session_messages: list[Message] = []
        if session_id:
            session = store.get(session_id)
            if session is not None:
                session_messages = _parse_messages(session.get("messages", []))

        raw_messages = payload.get("messages", [])
        if raw_messages:
            user_content = _limit_text(
                str(raw_messages[-1].get("content", "") or ""),
                request.app.state.settings.max_message_chars,
            )
            raw_messages[-1]["content"] = user_content

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

        # Attach image bytes to the latest user message per ollama spec.
        if session_id and file_ids and raw_messages:
            images = _collect_images(store, session_id, file_ids)
            if images:
                raw_messages[-1]["images"] = images

        messages = _parse_messages(raw_messages)
        if session_messages:
            messages = session_messages + messages

        # Prepend a system prompt to encourage efficient tool usage when tools
        # are available. Avoid duplicate injection if the session already has
        # a system message.
        if not any(getattr(m, "role", None) == "system" for m in messages):
            messages.insert(0, Message(role="system", content=_TOOL_SYSTEM_PROMPT))

        host = request.app.state.settings.ollama_host

        from ollama_web import llm  # local import to avoid circular at module load

        registry = await _build_registry(session_id)

        total_image_bytes = sum(
            len(img.value) for img in (getattr(messages[-1], "images", None) or [])
        )
        logger.info(
            "chat stream model=%s session=%s messages=%d images=%d image_bytes=%d",
            model,
            session_id,
            len(messages),
            len(getattr(messages[-1], "images", []) or []),
            total_image_bytes,
        )

        capabilities = llm.get_model_capabilities(model, host=host)
        logger.info(
            "chat stream model=%s capabilities=%s",
            model,
            sorted(capabilities),
        )

        # Fallback: some cloud/proxy models support tools even though ollama
        # does not advertise the capability. Enable tools when the model name
        # hints at a cloud endpoint and tools are not already reported.
        if "tools" not in capabilities and ":cloud" in model:
            capabilities.add("tools")
            logger.info("enabled tools capability fallback for cloud model %s", model)

        async for event in llm.astream_chat_with_tools(
            messages, model, think=think, host=host, registry=registry,
            capabilities=capabilities,
        ):
            if event.get("type") == "delta":
                assistant_text = _limit_text(
                    assistant_text + event.get("content", ""),
                    request.app.state.settings.max_message_chars,
                )
            yield event

    except Exception as exc:  # noqa: BLE001
        logger.exception("chat stream failed")
        error_msg = f"Stream failed: {exc}"
        yield {"type": "error", "message": error_msg}
        # Persist the error as the assistant message so it survives reloads
        # and re-renders in the chat pane.
        assistant_text = (
            f"[ERROR] {error_msg}\n\n"
            f"{t('errors.vision_model_required')}"
        )

    finally:
        # Persist user and assistant messages to the session even when the
        # upstream call failed, so the UI keeps the user message.
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

        # Always emit a terminal done event so the client closes cleanly.
        yield {"type": "done"}


async def chat(request: Request) -> StreamingResponse:
    """POST /api/chat: stream chat events as Server-Sent Events."""
    try:
        body = await request.body()
        if len(body) > request.app.state.settings.max_message_chars * 4:
            data = json.dumps(
                {"type": "error", "message": "Request body too large"},
                ensure_ascii=False,
            )
            return StreamingResponse(
                _single_event(data),
                media_type="text/event-stream",
                status_code=413,
            )
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
    logger.info(
        "chat request model=%s session=%s messages=%d",
        payload.get("model"),
        session_id,
        msg_count,
    )
    return StreamingResponse(
        _event_stream(request, payload, session_id),
        media_type="text/event-stream",
    )


async def _single_event(data: str) -> AsyncIterator[bytes]:
    """Yield a single SSE event and finish."""
    yield f"data: {data}\n\n".encode()
