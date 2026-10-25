"""Ollama client wrapper with tool-calling chat loop and streaming."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import ollama
from ollama import Message
from ollama._types import Image as OllamaImage
from ollama._utils import convert_function_to_tool

from .config import settings
from .context_budget import (
    IMAGE_TOKENS,
    ContextBudget,
    ModelContext,
    ResultBudget,
    positive_int,
    validate_tool_settings,
)
from .i18n import t
from .mcp import redact_secrets
from .prompts import get_prompt
from .tools.helper.image import resize_image
from .tools.helper.pagination import (
    PageSizeError,
    format_paged_result,
    make_token,
    parse_paged_result,
    parse_token,
)
from .tools.registry import ToolExecutionResult, ToolRegistry, default_registry

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


def get_model_info(model: str, host: str | None = None, *, client: Any = None) -> dict[str, Any]:
    """Fetch model metadata once; unavailable/retired models have no metadata."""
    try:
        active_client = client or get_client(host)
        if isinstance(active_client, ollama.Client):
            # Older SDK ShowResponse schemas discard remote_host/remote_model.
            # Use the same SDK transport/auth/timeout, preserving the raw fields.
            response = active_client._request(dict, "POST", "/api/show", json={"model": model})
        else:
            response = active_client.show(model)
        if isinstance(response, dict):
            return response
        return dict(response.model_dump(by_alias=True, exclude_none=True))
    except Exception:  # noqa: BLE001
        return {}


def get_model_capabilities(model: str, host: str | None = None) -> set[str]:
    return {str(c).lower() for c in (get_model_info(model, host).get("capabilities") or [])}


def _is_image_message(message: Message) -> bool:
    """Check whether a message has image attachments."""
    images = getattr(message, "images", None)
    return bool(images)


def _remove_images_with_notice(message: Message, language: str | None = None) -> Message:
    """Remove unsupported image bytes and add a localized fixed notice."""
    content = str(getattr(message, "content", "") or "")
    notice = get_prompt("vision_unsupported_attachment", lang=language)
    content = f"{content}\n\n{notice}".strip()

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


def _trim_tool_result(result: str, budget: ResultBudget, notice: str = "") -> str:
    """Keep paged results atomic; trim other results to their token estimate."""
    suffix = "\n\n" + notice if notice else ""
    if _PAGE_INFO_MARKER in result and not budget.fits(result + suffix):
        result = "Paged result metadata exceeds the available budget; page not consumed."
    if not suffix:
        return budget.trim(result)
    if not budget.fits(suffix):
        return budget.trim(notice)
    body_budget = ResultBudget(budget.tokens - budget.estimate(suffix), budget.factor)
    return body_budget.trim(result) + suffix


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


def _page_metadata_fits(name: str, args: Any, budget: ResultBudget) -> bool:
    """Reject impossible continuation envelopes before starting a built-in tool."""
    if not isinstance(args, dict):
        return True  # Argument validation remains the registry's responsibility.
    try:
        source = _paged_source_key(name, args)[1]
        token = args.get("continue_from") or make_token(name, source, 1)
        envelope = format_paged_result("x", has_more=True, next_token=token)
        return budget.fits(envelope)
    except (PageSizeError, ValueError):
        return True  # Invalid input must be reported as an argument error, not skipped.


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
    model_info: dict[str, Any] | None = None,
    tool_settings: dict[str, float] | None = None,
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
    tools = [
        convert_function_to_tool(t).model_dump(exclude_none=True) if callable(t) else t
        for t in reg.ollama_tools
    ]
    effective_tools = tools if "tools" in caps else []
    effective_think = think if "thinking" in caps else None

    # Non-vision models receive a localized notice, never base64 image text.
    if "vision" not in caps:
        messages = [
            _remove_images_with_notice(m, language) if _is_image_message(m) else m for m in messages
        ]

    metadata = (
        model_info if model_info is not None else get_model_info(model, host, client=raw_client)
    )
    budget = ContextBudget(
        ModelContext.resolve(model, metadata, options or {}),
        num_predict=positive_int((options or {}).get("num_predict")),
    )
    if tool_settings is not None:
        budget.context_usage_limit = validate_tool_settings(tool_settings)["context_usage_limit"]
    budget.notification_text = (
        get_prompt("tool_context_instruction", lang=language)
        + "\n\n"
        + get_prompt("pagination_incomplete_instruction", lang=language)
    )

    def budget_event() -> dict[str, Any]:
        snapshot = budget.snapshot(messages, effective_tools)
        logger.info("context budget %s", snapshot)
        return {"type": "context_budget", **snapshot}

    def done_event(response: Any) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "done",
            "num_ctx": budget.context.capacity,
            "requested_num_ctx": budget.context.requested,
            "context_budget": budget.snapshot(messages, effective_tools),
        }
        event.update(_token_counts(response) or {})
        return event

    runtime_checked = False
    outstanding_paged_sources: set[tuple[str, str]] = set()

    MAX_EMPTY_RETRIES = 2
    empty_retries = 0

    rounds = 0
    budget_exhausted = False
    while not budget_exhausted:
        rounds += 1
        full_message: dict[str, Any] = {"role": "assistant", "content": "", "thinking": ""}
        tool_calls: list[dict[str, Any]] = []
        yield budget_event()
        sent_estimate = budget.input_tokens(messages, effective_tools)
        if budget.headroom(messages, effective_tools) < 0:
            yield {"type": "error", "message": t("status.context_headroom", lang=language)}
            return
        logger.info("tool round %d start messages=%d model=%s", rounds, len(messages), model)

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
            # Retry the same round; empty responses retain their own retry limit.
            rounds -= 1
            # Small pause to avoid hammering the upstream.
            time.sleep(0.5)
            continue
        # Reset retry budget on any non-empty round so isolated hiccups are fine.
        if not is_empty:
            empty_retries = 0

        budget.observe(_token_counts(last_chunk), sent_estimate)
        logger.info(
            "context measurement round=%d sent_estimate=%d measured=%s correction_factor=%.4f",
            rounds,
            sent_estimate,
            _token_counts(last_chunk),
            budget.factor,
        )
        if not runtime_checked:
            runtime_checked = True
            if not budget.context.cloud:
                try:
                    budget.update_runtime(raw_client.ps(), model)
                except Exception:  # noqa: BLE001
                    logger.info("runtime context unavailable; using %s", budget.context.source)

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
                # Exhausted the empty-response retry budget.
                yield {
                    "type": "warning",
                    "message": t("status.empty_response", lang=language),
                }
            yield budget_event()
            yield done_event(last_chunk)
            return

        # Reserve all matching response envelopes and skip notices before executing a batch.
        skip_text = (
            "Tool not executed: insufficient context or page metadata. Unread content remains."
        )
        notice_costs = [
            budget.message_tokens(
                Message(role="tool", tool_name=c.get("name", ""), content=skip_text)
            )
            for c in tool_calls
        ]
        batch_reserve = sum(notice_costs)
        for call, notice_cost in zip(tool_calls, notice_costs, strict=True):
            name = call.get("name", "")
            args = call.get("arguments", {})
            paged_source = _paged_source_key(name, args)
            is_builtin_paged = name in {"scrape_url", "pdf_to_text", "search_and_fetch"}
            batch_reserve -= notice_cost
            envelope_cost = (
                budget.message_tokens(
                    Message(role="tool", tool_name=name, content=""),
                )
                + 1
            )
            result_budget = budget.available(
                messages, effective_tools, batch_reserve + envelope_cost
            )
            skipped = budget_exhausted or result_budget.tokens <= 0
            metadata_skipped = (
                not skipped
                and is_builtin_paged
                and not _page_metadata_fits(name, args, result_budget)
            )
            if skipped:
                budget_exhausted = True
                detailed_result = ToolExecutionResult(skip_text)
                budget.stop_reason = budget.stop_reason or "context_headroom"
                if is_builtin_paged:
                    outstanding_paged_sources.add(paged_source)
                logger.info("tool skipped name=%s reason=%s", name, budget.stop_reason)
            elif metadata_skipped:
                skipped = True
                detailed_result = ToolExecutionResult(skip_text)
                budget.stop_reason = "result_metadata_too_large"
                outstanding_paged_sources.add(paged_source)
                logger.info("tool skipped name=%s reason=%s", name, budget.stop_reason)
            else:
                yield {"type": "tool_start", "name": name, "arguments": _safe_tool_arguments(args)}
                try:
                    detailed_result = reg.execute_detailed(
                        name,
                        args if isinstance(args, dict) else json.dumps(args),
                        result_budget=result_budget,
                    )
                except Exception as exc:  # noqa: BLE001
                    detailed_result = ToolExecutionResult(f"Tool execution failed: {exc}")

            notices: list[str] = []
            image_values: list[OllamaImage] = []
            for artifact in detailed_result.artifacts:
                if not artifact.for_assistant:
                    continue
                if artifact.kind == "image":
                    image_cost = math.ceil(IMAGE_TOKENS * budget.factor)
                    if "vision" in caps and result_budget.tokens > image_cost:
                        processed = resize_image(artifact.data, name=artifact.name)
                        image_values.append(OllamaImage(value=processed))
                        result_budget = ResultBudget(
                            result_budget.tokens - image_cost,
                            budget.factor,
                        )
                    else:
                        key = (
                            "mcp_image_artifact_notice"
                            if "vision" not in caps
                            else "image_budget_notice"
                        )
                        notices.append(get_prompt(key, lang=language))
                elif artifact.kind == "audio":
                    notices.append(get_prompt("mcp_audio_artifact_notice", lang=language))
            text = _safe_tool_result(detailed_result.text)
            notice_text = "\n\n".join(notices)
            raw_is_paged = _PAGE_INFO_MARKER in text
            if raw_is_paged:
                _, _, next_token = parse_paged_result(text)
                if next_token and isinstance(args, dict):
                    paged_source = _paged_source_key(name, {**args, "continue_from": next_token})
                outstanding_paged_sources.add(paged_source)
            elif is_builtin_paged and not skipped:
                outstanding_paged_sources.discard(paged_source)
            combined_text = text + ("\n\n" + notice_text if notice_text else "")
            if raw_is_paged and not result_budget.fits(combined_text):
                text = "Paged result metadata exceeds the available budget; page not consumed."
                budget.stop_reason = "result_metadata_too_large"
            elif "pagination failed:" in text and "metadata" in text.lower():
                budget.stop_reason = "result_metadata_too_large"
                outstanding_paged_sources.add(paged_source)
            # Skip notices use reserved context rather than the exhausted result quota.
            result = text if skipped else _trim_tool_result(text, result_budget, notice_text)
            kwargs: dict[str, Any] = {"role": "tool", "tool_name": name, "content": result}
            if image_values:
                kwargs["images"] = image_values
            tool_message = Message(**kwargs)
            messages.append(tool_message)
            if skipped:
                budget.observed_tail += budget.message_tokens(tool_message)
            else:
                budget.add_result(tool_message)
                media = [
                    a.public_dict()
                    for a in detailed_result.artifacts
                    if a.for_user and a.file_id is not None
                ]
                event: dict[str, Any] = {"type": "tool_end", "name": name, "result": result}
                if media:
                    event["media"] = media
                yield event
            yield budget_event()
        # A metadata-only failure must not trigger an endless identical retry loop.
        if budget.stop_reason == "result_metadata_too_large":
            budget_exhausted = True
        # Finish before another model round could consume the remaining room with
        # a tool request and its matching skip response. The final instruction was reserved.
        followup_reserve = max(notice_costs, default=0)
        if msg is not None:
            followup_reserve += budget.message_tokens(msg)
        if budget.available(messages, effective_tools, followup_reserve).tokens <= 0:
            budget_exhausted = True

    logger.info("tool execution stopped reason=%s", budget.stop_reason)
    yield {
        "type": "status",
        "message": t("status.tool_context_exhausted", lang=language),
    }
    limit_instruction = get_prompt("tool_context_instruction", lang=language)
    if outstanding_paged_sources:
        limit_instruction += "\n\n" + get_prompt("pagination_incomplete_instruction", lang=language)
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
    effective_tools = []
    yield budget_event()
    if budget.headroom(messages, []) < 0:
        budget.stop_reason = "context_headroom"
        yield {"type": "error", "message": t("status.context_headroom", lang=language)}
        return
    sent_estimate = budget.input_tokens(messages, [])
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
    budget.observe(_token_counts(last_chunk), sent_estimate)
    logger.info(
        "final context measurement sent_estimate=%d measured=%s correction_factor=%.4f",
        sent_estimate,
        _token_counts(last_chunk),
        budget.factor,
    )
    yield budget_event()
    yield done_event(last_chunk)


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
    model_info: dict[str, Any] | None = None,
    tool_settings: dict[str, float] | None = None,
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
        model_info=model_info,
        tool_settings=tool_settings,
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
