"""SSE streaming chat endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ollama import Message
from starlette.requests import Request
from starlette.responses import StreamingResponse


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


async def _event_stream(request: Request, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """Yield SSE events from the tool-augmented chat loop."""
    model = payload.get("model") or request.app.state.settings.default_model
    raw_messages = payload.get("messages", [])
    think = payload.get("think")

    messages = _parse_messages(raw_messages)
    host = request.app.state.settings.ollama_host

    from ollama_web import llm  # local import to avoid circular at module load

    async for event in llm.astream_chat_with_tools(messages, model, think=think, host=host):
        data = json.dumps(event, ensure_ascii=False)
        yield f"data: {data}\n\n".encode()


async def chat(request: Request) -> StreamingResponse:
    """POST /api/chat: stream chat events as Server-Sent Events."""
    import logging

    logger = logging.getLogger("ollama_web.chat")
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
    msg_count = len(payload.get("messages", []))
    logger.info("chat request model=%s messages=%d", payload.get("model"), msg_count)
    return StreamingResponse(
        _event_stream(request, payload),
        media_type="text/event-stream",
    )


async def _single_event(data: str) -> AsyncIterator[bytes]:
    """Yield a single SSE event and finish."""
    yield f"data: {data}\n\n".encode()
