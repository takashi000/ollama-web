"""Ollama client wrapper with tool-calling chat loop and streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, cast

import ollama
from ollama import Message

from .config import settings
from .tools.registry import ToolRegistry, default_registry

# Maximum successive tool-call rounds before we force a final answer.
MAX_TOOL_ROUNDS = 5

# Type alias matching ollama's expected tools signature.
ToolCallable = Callable[..., Any]


def get_client(host: str | None = None) -> ollama.Client:
    """Return an ``ollama.Client`` configured for the configured host."""
    return ollama.Client(host=host or settings.ollama_host, timeout=settings.ollama_timeout)


def list_models(host: str | None = None) -> list[str]:
    """Return the list of locally available model names."""
    client = get_client(host)
    try:
        resp = client.list()
    except Exception:  # noqa: BLE001
        return []
    models: list[str] = []
    for item in resp.get("models", []) if isinstance(resp, dict) else getattr(resp, "models", []):
        name = item.get("model") if isinstance(item, dict) else getattr(item, "model", None)
        if name:
            models.append(str(name))
    return models


def _tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool calls from an ollama response in a tolerant manner."""
    msg = getattr(response, "message", None) or (
        response.get("message") if isinstance(response, dict) else None
    )
    if msg is None:
        return []
    calls = getattr(msg, "tool_calls", None) if not isinstance(msg, dict) else msg.get("tool_calls")
    if not calls:
        return []
    out: list[dict[str, Any]] = []
    for c in calls:
        if isinstance(c, dict):
            fn = c.get("function", {})
            name = fn.get("name") or c.get("name", "")
            args = fn.get("arguments") or c.get("arguments", {})
            out.append({"name": name, "arguments": args})
        else:
            fn = getattr(c, "function", None)
            if fn is not None:
                name = getattr(fn, "name", "")
                args = getattr(fn, "arguments", {})
            else:
                name = getattr(c, "name", "")
                args = getattr(c, "arguments", {})
            out.append({"name": name, "arguments": args})
    return out


def _assistant_message(response: Any) -> Message:
    """Convert a non-streaming response into a Message for re-submission."""
    msg = getattr(response, "message", None) or (
        response.get("message") if isinstance(response, dict) else None
    )
    if isinstance(msg, dict):
        return Message(**msg)
    if msg is not None:
        return cast(Message, msg)
    return Message(role="assistant", content="")


def chat_with_tools(
    messages: list[Message],
    model: str,
    *,
    think: bool | str | None = None,
    registry: ToolRegistry | None = None,
    host: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Run a multi-round tool-calling chat and yield progress events.

    Events are dicts with a ``type`` key:

    - ``{"type": "tool_start", "name": str, "arguments": dict}``
    - ``{"type": "tool_end", "name": str, "result": str}``
    - ``{"type": "delta", "content": str}``
    - ``{"type": "thinking", "content": str}``
    - ``{"type": "done"}``
    - ``{"type": "error", "message": str}``

    The final assistant message is streamed token-by-token via ``delta`` events.
    """
    raw_client = get_client(host)
    reg = registry or default_registry()
    tools: list[ToolCallable] = [cast(Any, c) for c in reg.callables]

    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        try:
            resp = cast(Any, raw_client).chat(
                model=model,
                messages=messages,
                tools=tools,
                think=think,
                stream=False,
            )
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": str(exc)}
            return

        calls = _tool_calls(resp)
        if not calls:
            # No tool calls: stream the final answer for UI effect.
            content = _assistant_content(resp)
            thinking = _assistant_thinking(resp)
            if thinking:
                yield {"type": "thinking", "content": thinking}
            if content:
                yield {"type": "delta", "content": content}
            yield {"type": "done"}
            return

        # Append the assistant message that requested tool calls.
        messages.append(_assistant_message(resp))

        # Execute each requested tool and append tool-role messages.
        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {})
            yield {"type": "tool_start", "name": name, "arguments": args}
            result = reg.execute(name, args if isinstance(args, dict) else json.dumps(args))
            yield {"type": "tool_end", "name": name, "result": result}
            messages.append(Message(role="tool", tool_name=name, content=result))

    # Exceeded tool rounds: ask for a final answer without tools.
    try:
        resp = cast(Any, raw_client).chat(
            model=model,
            messages=messages,
            think=think,
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": str(exc)}
        return
    content = _assistant_content(resp)
    thinking = _assistant_thinking(resp)
    if thinking:
        yield {"type": "thinking", "content": thinking}
    if content:
        yield {"type": "delta", "content": content}
    yield {"type": "done"}


def stream_chat_with_tools(
    messages: list[Message],
    model: str,
    *,
    think: bool | str | None = None,
    registry: ToolRegistry | None = None,
    host: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Like ``chat_with_tools`` but the final answer is streamed from ollama.

    Tool-call resolution rounds are performed in non-streaming mode; once no
    further tool calls are requested the final response is streamed token by
    token via ``delta`` events.
    """
    raw_client = get_client(host)
    reg = registry or default_registry()
    tools: list[ToolCallable] = [cast(Any, c) for c in reg.callables]

    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        try:
            resp = cast(Any, raw_client).chat(
                model=model,
                messages=messages,
                tools=tools,
                think=think,
                stream=False,
            )
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": str(exc)}
            return

        calls = _tool_calls(resp)
        if not calls:
            # Stream the final answer by re-issuing the last messages.
            # We already have the full non-streamed content; emit it as deltas
            # in small chunks to simulate streaming for the UI.
            thinking = _assistant_thinking(resp)
            if thinking:
                yield {"type": "thinking", "content": thinking}
            content = _assistant_content(resp)
            if content:
                for chunk in _chunk_text(content, 64):
                    yield {"type": "delta", "content": chunk}
            yield {"type": "done"}
            return

        messages.append(_assistant_message(resp))
        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {})
            yield {"type": "tool_start", "name": name, "arguments": args}
            result = reg.execute(name, args if isinstance(args, dict) else json.dumps(args))
            yield {"type": "tool_end", "name": name, "result": result}
            messages.append(Message(role="tool", tool_name=name, content=result))

    # Final attempt without tools.
    try:
        stream = cast(Any, raw_client).chat(
            model=model,
            messages=messages,
            think=think,
            stream=True,
        )
        for chunk in stream:
            thinking = _assistant_thinking(chunk)
            if thinking:
                yield {"type": "thinking", "content": thinking}
            content = _assistant_content(chunk)
            if content:
                yield {"type": "delta", "content": content}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": str(exc)}
        return
    yield {"type": "done"}


async def astream_chat_with_tools(
    messages: list[Message],
    model: str,
    *,
    think: bool | str | None = None,
    registry: ToolRegistry | None = None,
    host: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async wrapper that yields events from ``stream_chat_with_tools``."""
    import anyio

    gen = stream_chat_with_tools(messages, model, think=think, registry=registry, host=host)

    def _next() -> dict[str, Any] | None:
        try:
            return next(gen)
        except StopIteration:
            return None

    while True:
        event = await anyio.to_thread.run_sync(_next)
        if event is None:
            break
        yield event


def _assistant_content(response: Any) -> str:
    msg = getattr(response, "message", None) or (
        response.get("message") if isinstance(response, dict) else None
    )
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("content", "") or "")
    return str(getattr(msg, "content", "") or "")


def _assistant_thinking(response: Any) -> str:
    msg = getattr(response, "message", None) or (
        response.get("message") if isinstance(response, dict) else None
    )
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("thinking", "") or "")
    return str(getattr(msg, "thinking", "") or "")


def _chunk_text(text: str, size: int) -> Iterator[str]:
    """Yield successive chunks of ``text`` up to ``size`` characters."""
    for i in range(0, len(text), size):
        yield text[i : i + size]
