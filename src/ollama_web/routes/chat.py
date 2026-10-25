"""SSE streaming chat endpoint."""

from __future__ import annotations

import functools
import html
import inspect
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime
from typing import Any, cast

import anyio
from ollama import Message
from ollama._types import Image as OllamaImage
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from ..chat_worker import worker_events
from ..mcp import collect_mcp_tools, make_mcp_executor
from ..prompts import get_prompt
from ..sessions import SessionStore, is_valid_id
from ..settings_store import load_app_settings
from ..tools.fetch import pop_fetched_files
from ..tools.helper.image import resize_image
from ..tools.pdf import make_session_pdf_tool
from ..tools.registry import ToolArtifact, ToolRegistry, default_registry

logger = logging.getLogger("ollama_web.chat")


def _limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def _compose_system_prompt(language: str, custom_prompt: str) -> str:
    """Combine the localized built-in tool prompt, current time, and user prompt."""
    tool_prompt = get_prompt("tool_system", lang=language)
    time_line = get_prompt("current_time", lang=language).format(
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    parts = [tool_prompt, time_line]
    if custom_prompt:
        parts.append(custom_prompt)
    return "\n\n".join(parts)


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


async def _build_registry(
    session_id: str | None, store: SessionStore | None = None
) -> ToolRegistry:
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

    if session_id is not None and store is not None:

        def load_pdf_attachment(file_id: str) -> bytes | None:
            chat_file = store.get_file(session_id, file_id)
            if chat_file is None or chat_file.is_image:
                return None
            if chat_file.mime.lower() != "application/pdf" and not chat_file.name.lower().endswith(
                ".pdf"
            ):
                return None
            return store.get_file_data(session_id, file_id)

        reg._tools["pdf_to_text"] = make_session_pdf_tool(load_pdf_attachment)  # noqa: SLF001

    try:
        mcp_tools = await collect_mcp_tools()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to collect MCP tools: %s", exc)
        mcp_tools = []

    tool_names = [t.get("function", {}).get("name") for t in mcp_tools]
    logger.info("collected %d MCP tool(s): %s", len(mcp_tools), tool_names)

    if mcp_tools:
        artifact_handler = None
        if session_id is not None and store is not None:

            def persist_artifact(full_name: str, artifact: ToolArtifact) -> None:
                if not artifact.for_user:
                    return
                chat_file = store.add_file(
                    session_id=session_id,
                    name=artifact.name,
                    data=artifact.data,
                    mime=artifact.mime,
                    text="",
                    source=full_name,
                )
                artifact.file_id = chat_file.id

            artifact_handler = persist_artifact

        executor = make_mcp_executor(artifact_handler=artifact_handler)
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


def _attachment_context(
    store: SessionStore,
    session_id: str,
    file_ids: list[str],
    *,
    total_limit: int,
    language: str | None = None,
) -> str:
    if total_limit <= 0:
        return ""
    chat_files = []
    for file_id in file_ids:
        chat_file = store.get_file(session_id, file_id)
        if chat_file is not None and not chat_file.is_image and not chat_file.is_audio:
            chat_files.append(chat_file)
    if not chat_files:
        return ""

    parts: list[str] = []
    prefix = get_prompt("attachment_prefix", lang=language)
    paging_hint = get_prompt(
        "attachment_paging",
        lang=language,
        fallback=(
            "PDF previews may have unread content. Use pdf_to_text with the attachment URI "
            "and next_token until has_more is absent."
        ),
    )
    used = len(prefix) + len(paging_hint) + 4
    per_file = max(256, min(4000, (total_limit - used) // max(1, len(chat_files))))
    pdf_tool = make_session_pdf_tool(
        lambda file_id: (
            store.get_file_data(session_id, file_id)
            if store.get_file(session_id, file_id) is not None
            else None
        )
    )

    for chat_file in chat_files:
        source_uri = f"attachment:{chat_file.id}"
        if chat_file.mime.lower() == "application/pdf" or chat_file.name.lower().endswith(".pdf"):
            text = pdf_tool(source_uri, max_chars=per_file)
        else:
            text = _limit_text(chat_file.text or "", per_file)
        block = (
            f'<attached-file name="{html.escape(chat_file.name)}" id="{chat_file.id}" '
            f'source="{chat_file.source}" uri="{source_uri}">\n{text}\n</attached-file>'
        )
        if used + len(block) + 2 > total_limit:
            break
        parts.append(block)
        used += len(block) + 2
    if not parts:
        return ""
    return "\n\n".join([prefix, paging_hint, *parts])


def _has_image_attachments(store: SessionStore, session_id: str, file_ids: list[str]) -> bool:
    return any(
        (chat_file := store.get_file(session_id, file_id)) is not None and chat_file.is_image
        for file_id in file_ids
    )


def _has_audio_attachments(store: SessionStore, session_id: str, file_ids: list[str]) -> bool:
    return any(
        (chat_file := store.get_file(session_id, file_id)) is not None and chat_file.is_audio
        for file_id in file_ids
    )


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


def _token_count_from_done(
    done_event: dict[str, Any] | None, num_ctx: int
) -> dict[str, int] | None:
    if not done_event:
        return None
    prompt_eval_count = done_event.get("prompt_eval_count")
    eval_count = done_event.get("eval_count")
    if (prompt_eval_count is None or eval_count is None) and "context_budget" not in done_event:
        return None
    result: dict[str, Any] = {"num_ctx": num_ctx}
    if prompt_eval_count is not None and eval_count is not None:
        result.update(
            prompt_eval_count=int(prompt_eval_count),
            eval_count=int(eval_count),
            total_count=int(prompt_eval_count) + int(eval_count),
        )
    for key in ("requested_num_ctx", "context_budget"):
        if key in done_event:
            result[key] = done_event[key]
    return result


async def _event_stream(
    request: Request,
    payload: dict[str, Any],
    session_id: str | None,
) -> AsyncIterator[bytes]:
    """Yield SSE events from the tool-augmented chat loop."""
    runtime = request.app.state.settings
    store = request.app.state.session_store
    raw_messages = payload.get("messages", [])
    user_content = _limit_text(
        str(raw_messages[-1].get("content", "") or "") if raw_messages else "",
        runtime.max_message_chars,
    )
    file_ids = [str(f) for f in payload.get("file_ids", []) if is_valid_id(str(f))]
    assistant_text = ""
    assistant_files: list[str] = []
    done: dict[str, Any] | None = None
    saved = False

    def save_accepted() -> None:
        nonlocal saved
        if saved:
            return
        saved = True
        if session_id and (user_content or file_ids) and store.get(session_id) is not None:
            store.add_message(session_id, "user", user_content, attachments=file_ids)
            if assistant_text or assistant_files:
                store.add_message(
                    session_id,
                    "assistant",
                    assistant_text,
                    attachments=assistant_files,
                    token_count=_token_count_from_done(done, int((done or {}).get("num_ctx", 0))),
                )

    try:
        yield b":\n\n"
        async with aclosing(
            worker_events(runtime, store, payload, session_id, before_close=save_accepted)
        ) as events:
            async for event in events:
                if event.get("type") == "delta":
                    assistant_text = _limit_text(
                        assistant_text + event.get("content", ""), runtime.max_message_chars
                    )
                elif event.get("type") == "error":
                    assistant_text = f"[ERROR] {event.get('message', '')}"
                elif event.get("type") == "done":
                    done = event
                elif event.get("type") == "tool_end":
                    for media in event.get("media", []):
                        file_id = str(media.get("id", ""))
                        if is_valid_id(file_id) and file_id not in assistant_files:
                            assistant_files.append(file_id)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
    except Exception as exc:
        logger.exception("chat worker failed")
        assistant_text = f"[ERROR] {exc}"
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n".encode()
    finally:
        # Only accepted events are saved, once, even on disconnect.
        save_accepted()


class ChatStreamingResponse(StreamingResponse):
    """Listen for disconnect even while a worker produces no response bytes."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as group:

                async def stream() -> None:
                    try:
                        await self.stream_response(send)
                    finally:
                        group.cancel_scope.cancel()

                group.start_soon(stream)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await cast(Any, self.body_iterator).aclose()


async def _chat_event_stream(
    request: Request,
    payload: dict[str, Any],
    session_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Core chat loop yielding events. Exceptions are converted to SSE error events."""
    model = payload.get("model") or request.app.state.settings.default_model
    think = payload.get("think")
    runtime_settings = request.app.state.settings
    app_settings = load_app_settings(runtime_settings.data_dir, runtime_settings.language)
    language = str(app_settings["ui"]["language"])
    ollama_settings = app_settings["ollama"]
    ollama_options = dict(ollama_settings["options"])
    custom_prompt = str(ollama_settings["system_prompt"])
    system_prompt = _compose_system_prompt(language, custom_prompt)
    num_ctx_value = ollama_options.get("num_ctx")
    try:
        num_ctx = int(num_ctx_value) if num_ctx_value is not None else 0
    except (TypeError, ValueError):
        num_ctx = 0

    store: SessionStore = request.app.state.session_store
    file_ids: list[str] = [
        str(file_id) for file_id in payload.get("file_ids", []) if is_valid_id(str(file_id))
    ]
    user_content = ""
    sent_done = False

    try:
        session_messages: list[Message] = []
        if session_id:
            session = store.get(session_id)
            if session is not None:
                session_messages = _parse_messages(session.get("messages", []))

        raw_messages = list(payload.get("messages", []))
        if raw_messages:
            user_content = _limit_text(
                str(raw_messages[-1].get("content", "") or ""),
                request.app.state.settings.max_message_chars,
            )
            raw_messages[-1]["content"] = user_content

        # An attachment-only submission still needs a user message to carry
        # either image bytes or the localized unsupported-vision notice.
        if file_ids and not raw_messages:
            raw_messages.append({"role": "user", "content": ""})

        host = request.app.state.settings.ollama_host

        from ollama_web import llm  # local import to avoid circular at module load

        model_info = llm.get_model_info(model, host=host)
        capabilities = {str(c).lower() for c in (model_info.get("capabilities") or [])}
        logger.info(
            "chat stream model=%s capabilities=%s",
            model,
            sorted(capabilities),
        )

        # Fallback: some cloud/proxy models support tools even though ollama
        # does not advertise the capability. Enable only tools; vision remains
        # opt-in because sending image bytes to an unknown model is not safe.
        if "tools" not in capabilities and ":cloud" in model:
            capabilities.add("tools")
            logger.info("enabled tools capability fallback for cloud model %s", model)

        attachment_ctx = ""
        if session_id and file_ids:
            attachment_budget = max(
                0,
                request.app.state.settings.max_message_chars - len(user_content) - 256,
            )
            attachment_ctx = _attachment_context(
                store,
                session_id,
                file_ids,
                total_limit=attachment_budget,
                language=language,
            )

        if attachment_ctx and user_content:
            # Inject attachment context into the latest user message.
            augmented = f"{user_content}\n\n{attachment_ctx}"
            if raw_messages:
                raw_messages[-1]["content"] = augmented
        elif attachment_ctx:
            raw_messages.append({"role": "user", "content": attachment_ctx})

        # Attach image bytes only when Ollama explicitly advertises vision.
        # Other models receive a localized fixed notice, never base64 text.
        if session_id and file_ids and raw_messages:
            notices: list[str] = []
            if _has_image_attachments(store, session_id, file_ids):
                if "vision" in capabilities:
                    images = _collect_images(store, session_id, file_ids)
                    if images:
                        raw_messages[-1]["images"] = images
                else:
                    notices.append(get_prompt("vision_unsupported_attachment", lang=language))
            if _has_audio_attachments(store, session_id, file_ids):
                notices.append(get_prompt("audio_unsupported_attachment", lang=language))
            if notices:
                current = str(raw_messages[-1].get("content", "") or "")
                raw_messages[-1]["content"] = f"{current}\n\n" + "\n\n".join(notices)

        messages = _parse_messages(raw_messages)
        if session_messages:
            messages = session_messages + messages

        # Prepend a system prompt to encourage efficient tool usage when tools
        # are available. Avoid duplicate injection if the session already has
        # a system message.
        if not any(getattr(m, "role", None) == "system" for m in messages):
            messages.insert(0, Message(role="system", content=system_prompt))

        registry = await _build_registry(session_id, store)

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

        async for event in llm.astream_chat_with_tools(
            messages,
            model,
            think=think,
            host=host,
            registry=registry,
            capabilities=capabilities,
            options=ollama_options,
            language=language,
            model_info=model_info,
            tool_settings=app_settings["tools"],
        ):
            if event.get("type") == "done":
                event.setdefault("num_ctx", num_ctx)
                sent_done = True
            if event.get("type") == "tool_end":
                # A worker's fetched-file cache is request-local. Transfer each
                # completed tool's files before continuing to another round.
                if session_id:
                    for fetched_file in pop_fetched_files(session_id):
                        store.add_file(
                            session_id,
                            fetched_file["name"],
                            fetched_file["data"],
                            fetched_file["mime"],
                            fetched_file["text"],
                            source="fetch",
                        )
            yield event

    except Exception as exc:  # noqa: BLE001
        logger.exception("chat stream failed")
        error_msg = f"Stream failed: {exc}"
        yield {"type": "error", "message": error_msg}
    if not sent_done:
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
    return ChatStreamingResponse(
        _event_stream(request, payload, session_id),
        media_type="text/event-stream",
    )


async def _single_event(data: str) -> AsyncIterator[bytes]:
    """Yield a single SSE event and finish."""
    yield f"data: {data}\n\n".encode()
