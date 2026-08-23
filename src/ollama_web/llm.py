"""Ollama client wrapper with tool-calling chat loop and streaming."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, cast

import ollama
from ollama import Message

from .config import settings
from .i18n import t
from .mcp import redact_secrets
from .prompts import get_prompt
from .tools.helper.pagination import parse_paged_result, parse_token
from .tools.registry import ToolRegistry, default_registry

# Maximum successive tool-call rounds before we force a final answer.
# Some models request many tools per round; we keep this bounded but large
# enough to let iterative research complete (e.g. search, then fetch).
MAX_TOOL_ROUNDS = 5

# Upper bound on the cumulative size of tool results sent back to ollama.

# Larger values can cause ollama to spend a very long time processing the
# context between rounds.
MAX_TOOL_CONTEXT_CHARS = 24000

# Type alias matching ollama's expected tools signature.
ToolCallable = Callable[..., Any]

# Marker that separates a paged tool result's body from its page-info footer.
# Must match ``ollama_web.tools.helper.pagination.PAGE_INFO_MARKER``.
_PAGE_INFO_MARKER = "\n---page-info---"


def _safe_tool_arguments(arguments: Any) -> Any:
    return redact_secrets(arguments)


def _safe_tool_result(result: str) -> str:
    redacted = redact_secrets(result)
    return str(redacted)


def get_client(host: str | None = None) -> ollama.Client:
    """Return an ``ollama.Client`` configured for the configured host."""
    import httpx

    timeout = httpx.Timeout(
        settings.ollama_timeout,
        connect=10.0,
        read=settings.ollama_timeout,
        write=10.0,
        pool=10.0,
    )
    return ollama.Client(host=host or settings.ollama_host, timeout=timeout)


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


def get_model_capabilities(model: str, host: str | None = None) -> set[str]:
    """Return the capability set for a single model via ``ollama.show()``.

    Unknown or unreachable models are treated as having no special capabilities,
    which keeps the UI conservative and avoids sending unsupported parameters.
    """
    try:
        client = get_client(host)
        show_response = client.show(model)
    except Exception:  # noqa: BLE001
        return set()

    caps = getattr(show_response, "capabilities", None)
    if caps is None and isinstance(show_response, dict):
        caps = show_response.get("capabilities")
    if not caps:
        return set()
    return {str(c).lower() for c in caps}


def _is_image_message(message: Message) -> bool:
    """Check whether a message has image attachments."""
    images = getattr(message, "images", None)
    return bool(images)


def _embed_images_in_content(message: Message) -> Message:
    """Convert image bytes attached to a message into base64 markdown links.

    Non-vision models cannot consume the ``images`` field, so we fall back to
    embedding the image as data URIs inside the text content.
    """
    images = getattr(message, "images", None) or []
    if not images:
        return message

    parts: list[str] = []
    for img in images:
        raw = getattr(img, "value", None) or (img if isinstance(img, (bytes, bytearray)) else None)
        if raw is None:
            continue
        encoded = base64.b64encode(bytes(raw)).decode("ascii")
        # Use a generic data URI since ollama already processed the image.
        parts.append(f"![image](data:image/png;base64,{encoded})")

    content = str(getattr(message, "content", "") or "")
    if parts:
        content = content + "\n\n" + "\n\n".join(parts)

    kwargs: dict[str, Any] = {"role": getattr(message, "role", "user"), "content": content}
    for attr in ("name", "tool_calls", "tool_name"):
        value = getattr(message, attr, None)
        if value is not None:
            kwargs[attr] = value
    if getattr(message, "thinking", None):
        kwargs["thinking"] = message.thinking
    return Message(**kwargs)


def _tool_calls_from_message(msg: Any) -> list[dict[str, Any]]:
    """Extract tool calls from a message object/dict."""
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


def _token_counts(response: Any) -> dict[str, int] | None:
    """Extract prompt_eval_count and eval_count from an ollama response."""
    if response is None:
        return None
    if isinstance(response, dict):
        prompt_eval = response.get("prompt_eval_count")
        eval_count = response.get("eval_count")
    else:
        prompt_eval = getattr(response, "prompt_eval_count", None)
        eval_count = getattr(response, "eval_count", None)
    if prompt_eval is None or eval_count is None:
        return None
    return {"prompt_eval_count": int(prompt_eval), "eval_count": int(eval_count)}


def _trim_tool_result(result: str, limit: int) -> str:
    """Truncate ``result`` to ``limit`` chars while keeping it readable.

    Paged tools must fit their own complete envelope before advancing a cursor.
    An oversized paged result is therefore rejected atomically instead of
    being truncated into an unrecoverable content gap.
    """
    if len(result) <= limit:
        return result

    # A paged tool must size its own body before advancing its cursor. Trimming
    # it here would create an unrecoverable gap between visible text and the
    # next token, so fail the call atomically and let the model repeat it.
    if _PAGE_INFO_MARKER in result:
        return (
            "Paged tool result exceeded the context-safe result limit. "
            "Repeat the same tool call with a smaller max_chars value; no part "
            "of this page was consumed."
        )

    # No footer: legacy truncation behaviour.
    cutoff = result[:limit].rsplit("\n\n", 1)[0]
    if len(cutoff) < limit * 0.5:
        # No good paragraph boundary; cut at a space instead.
        cutoff = result[:limit].rsplit(" ", 1)[0]
    return cutoff + "\n\n…[tool result truncated]"


def _paged_source_key(name: str, arguments: Any) -> tuple[str, str]:
    """Identify one paged source without exposing its content."""
    if not isinstance(arguments, dict):
        return name, "unknown"
    if name == "search_and_fetch":
        return name, f"{arguments.get('query', '')}|{arguments.get('file_type', '')}"
    token = arguments.get("continue_from")
    if isinstance(token, str) and token:
        try:
            payload = parse_token(token, name)
            return name, str(payload.get("url", "unknown"))
        except ValueError:
            pass
    source = arguments.get("url_or_path", arguments.get("url", "unknown"))
    return name, str(source)


def stream_chat_with_tools(
    messages: list[Message],
    model: str,
    *,
    think: bool | str | None = None,
    registry: ToolRegistry | None = None,
    host: str | None = None,
    capabilities: set[str] | None = None,
    options: dict[str, Any] | None = None,
    language: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Run a tool-calling chat loop and yield real streaming events from ollama.

    The final assistant answer is streamed token-by-token. Tool and thinking
    events are emitted inline so the UI can render them inside the assistant
    message.
    """
    import logging
    import time

    logger = logging.getLogger("ollama_web.llm")

    caps = capabilities or set()
    raw_client = get_client(host)
    reg = registry or default_registry()
    tools: list[ToolCallable] = [cast(Any, t) for t in reg.ollama_tools]
    effective_tools = tools if "tools" in caps else []
    effective_think = think if "thinking" in caps else None

    # Non-vision models cannot use the ``images`` field; embed them as base64.
    if "vision" not in caps:
        messages = [_embed_images_in_content(m) if _is_image_message(m) else m for m in messages]

    cumulative_tool_chars = 0
    outstanding_paged_sources: set[tuple[str, str]] = set()

    MAX_EMPTY_RETRIES = 2
    empty_retries = 0

    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        full_message: dict[str, Any] = {"role": "assistant", "content": "", "thinking": ""}
        tool_calls: list[dict[str, Any]] = []
        logger.info(
            "tool round %d/%d start messages=%d model=%s cumulative_tool_chars=%d",
            rounds,
            MAX_TOOL_ROUNDS,
            len(messages),
            model,
            cumulative_tool_chars,
        )

        t0 = time.time()
        try:
            stream = cast(Any, raw_client).chat(
                model=model,
                messages=messages,
                tools=effective_tools,
                think=effective_think,
                options=options or None,
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ollama chat call failed in %.2fs: %s", time.time() - t0, exc)
            yield {"type": "error", "message": str(exc)}
            return

        chunk_count = 0
        last_chunk = None
        for chunk in stream:
            chunk_count += 1
            last_chunk = chunk
            thinking = _assistant_thinking(chunk)
            if thinking:
                full_message.setdefault("thinking", "")
                full_message["thinking"] += thinking
                yield {"type": "thinking", "content": thinking}
            content = _assistant_content(chunk)
            if content:
                full_message["content"] += content
                yield {"type": "delta", "content": content}

            calls = _tool_calls_from_message(getattr(chunk, "message", None))
            for c in calls:
                # Avoid duplicates from repeated chunks containing the same call.
                if c not in tool_calls:
                    tool_calls.append(c)

        logger.info(
            "ollama stream finished round=%d chunks=%d elapsed=%.2fs",
            rounds,
            chunk_count,
            time.time() - t0,
        )

        # Detect empty responses. Some models / proxies occasionally return a
        # stream with almost no meaningful content. Log it and retry a few
        # times before giving up.
        is_empty = (
            not full_message.get("content") and not full_message.get("thinking") and not tool_calls
        )
        if is_empty and empty_retries < MAX_EMPTY_RETRIES:
            empty_retries += 1
            logger.warning(
                "ollama returned an empty stream (chunks=%d); retry %d/%d",
                chunk_count,
                empty_retries,
                MAX_EMPTY_RETRIES,
            )
            yield {
                "type": "status",
                "message": t("status.empty_response_retry", lang=language),
            }
            # Retry the same round without consuming the tool round budget.
            rounds -= 1
            # Small pause to avoid hammering the upstream.
            time.sleep(0.5)
            continue
        # Reset retry budget on any non-empty round so isolated hiccups are fine.
        if not is_empty:
            empty_retries = 0

        # Reconstruct the assistant message including any tool_calls so the
        # next round can see them.
        if tool_calls:
            full_message["tool_calls"] = tool_calls
        msg = _assistant_message_chunk(full_message)
        if msg is not None:
            messages.append(msg)

        if not tool_calls:
            if outstanding_paged_sources:
                yield {
                    "type": "warning",
                    "message": t("status.pagination_incomplete", lang=language),
                }
            if is_empty:
                # Ran out of tool rounds while still empty; surface a warning.
                yield {
                    "type": "warning",
                    "message": t("status.empty_response", lang=language),
                }
            done_event: dict[str, Any] = {"type": "done"}
            tokens = _token_counts(last_chunk)
            if tokens:
                done_event.update(tokens)
            yield done_event
            return

        logger.info("executing %d tool call(s) round=%d", len(tool_calls), rounds)
        tool_results: list[tuple[str, str]] = []
        total_result_len = 0
        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {})
            paged_source = _paged_source_key(name, args)

            yield {"type": "tool_start", "name": name, "arguments": _safe_tool_arguments(args)}
            t1 = time.time()
            remaining_context = MAX_TOOL_CONTEXT_CHARS - (
                cumulative_tool_chars + total_result_len
            )
            is_builtin_paged = name in {"scrape_url", "pdf_to_text", "search_and_fetch"}
            if is_builtin_paged and remaining_context < settings.max_tool_result_chars:
                result = (
                    "Tool context budget exhausted before this paged call was executed. "
                    "Unread content remains; do not claim that the source was read in full."
                )
                outstanding_paged_sources.add(paged_source)
                logger.info("paged tool skipped name=%s reason=context_budget", name)
            else:
                try:
                    result = reg.execute(
                        name, args if isinstance(args, dict) else json.dumps(args)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "tool %s execution failed in %.2fs: %s", name, time.time() - t1, exc
                    )
                    result = f"Tool execution failed: {exc}"
                raw_is_paged = _PAGE_INFO_MARKER in result
                if raw_is_paged:
                    _, _, next_token = parse_paged_result(result)
                    if next_token and isinstance(args, dict):
                        paged_source = _paged_source_key(
                            name, {**args, "continue_from": next_token}
                        )
                    outstanding_paged_sources.add(paged_source)
                elif is_builtin_paged:
                    outstanding_paged_sources.discard(paged_source)
            # Always truncate per-tool result immediately so it does not bloat
            # the context even before we reach the cumulative limit.
            result = _safe_tool_result(_trim_tool_result(result, settings.max_tool_result_chars))
            logger.info(
                "tool %s executed len=%d elapsed=%.2fs",
                name,
                len(result),
                time.time() - t1,
            )
            yield {"type": "tool_end", "name": name, "result": result}
            tool_results.append((name, result))
            total_result_len += len(result)

        cumulative_tool_chars += sum(len(r) for _, r in tool_results)
        for name, result in tool_results:
            messages.append(Message(role="tool", tool_name=name, content=result))

    # Exceeded tool rounds: ask for a final answer without tools.
    logger.info(
        (
            "max tool rounds exceeded, requesting final answer without tools "
            "(cumulative_tool_chars=%d)"
        ),
        cumulative_tool_chars,
    )
    yield {
        "type": "status",
        "message": t("status.tool_limit_reached", lang=language),
    }
    limit_instruction = get_prompt("tool_limit_instruction", lang=language)
    if outstanding_paged_sources:
        limit_instruction += "\n\n" + get_prompt(
            "pagination_incomplete_instruction", lang=language
        )
        yield {
            "type": "warning",
            "message": t("status.pagination_incomplete", lang=language),
        }
    messages.append(
        Message(
            role="user",
            content=limit_instruction,
        )
    )
    t0 = time.time()
    try:
        stream = cast(Any, raw_client).chat(
            model=model,
            messages=messages,
            tools=[],
            think=effective_think,
            options=options or None,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("final ollama chat call failed in %.2fs: %s", time.time() - t0, exc)
        yield {"type": "error", "message": str(exc)}
        return

    logger.info("streaming final answer")
    last_chunk = None
    for chunk in stream:
        last_chunk = chunk
        thinking = _assistant_thinking(chunk)
        if thinking:
            yield {"type": "thinking", "content": thinking}
        content = _assistant_content(chunk)
        if content:
            yield {"type": "delta", "content": content}
    logger.info("final answer stream finished elapsed=%.2fs", time.time() - t0)
    done_event_final: dict[str, Any] = {"type": "done"}
    tokens = _token_counts(last_chunk)
    if tokens:
        done_event_final.update(tokens)
    yield done_event_final


async def astream_chat_with_tools(
    messages: list[Message],
    model: str,
    *,
    think: bool | str | None = None,
    registry: ToolRegistry | None = None,
    host: str | None = None,
    capabilities: set[str] | None = None,
    options: dict[str, Any] | None = None,
    language: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async wrapper that yields events from ``stream_chat_with_tools``."""
    import anyio

    gen = stream_chat_with_tools(
        messages,
        model,
        think=think,
        registry=registry,
        host=host,
        capabilities=capabilities,
        options=options,
        language=language,
    )

    def _next() -> dict[str, Any] | None:
        try:
            return next(gen)
        except StopIteration:
            return None

    while True:
        event = await anyio.to_thread.run_sync(_next, abandon_on_cancel=True)
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


def _assistant_message_chunk(full_message: dict[str, Any]) -> Message | None:
    """Build an assistant ``Message`` from the accumulated streamed message.

    ``tool_calls`` are expected to be already accumulated into ``full_message``.
    If the message is empty, ``None`` is returned.
    """
    content = full_message.get("content", "")
    thinking = full_message.get("thinking", "")
    calls = full_message.get("tool_calls", [])

    if not content and not thinking and not calls:
        return None

    kwargs: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking:
        kwargs["thinking"] = thinking
    if calls:
        # Convert plain dicts into ollama ToolCall objects.
        tc_list: list[Message.ToolCall] = []
        for c in calls:
            name = c.get("name", "")
            args = c.get("arguments", {})
            tc_list.append(
                Message.ToolCall(function=Message.ToolCall.Function(name=name, arguments=args))
            )
        kwargs["tool_calls"] = tc_list
    return Message(**kwargs)
