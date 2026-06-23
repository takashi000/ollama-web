"""Session management API routes."""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..sessions import SessionStore, _IMAGE_MIMES, _IMAGE_SUFFIXES
from ..tools.pdf import extract_pdf_text

logger = logging.getLogger("ollama_web.sessions")


def _get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def _is_image_file(name: str, mime: str) -> bool:
    mime_lower = mime.lower()
    name_lower = name.lower()
    if mime_lower in _IMAGE_MIMES:
        return True
    for suffix in _IMAGE_SUFFIXES:
        if name_lower.endswith(suffix):
            return True
    return False


async def list_sessions(request: Request) -> JSONResponse:
    store = _get_store(request)
    return JSONResponse({"sessions": store.list_sessions()})


async def create_session(request: Request) -> JSONResponse:
    store = _get_store(request)
    body = await request.json() if await request.body() else {}
    title = body.get("title")
    session = store.create(title=title)
    return JSONResponse(session)


async def get_session(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    session = store.get(session_id)
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse(session)


async def delete_session(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    ok = store.delete(session_id)
    if not ok:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse({"deleted": True})


async def upload_file(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    session = store.get(session_id)
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    max_size = request.app.state.settings.max_upload_mb * 1024 * 1024
    try:
        form = await request.form()
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to parse multipart form: %s", exc)
        return JSONResponse({"error": f"Invalid form data: {exc}"}, status_code=400)

    files = form.getlist("files") if hasattr(form, "getlist") else [form.get("files")]
    results: list[dict[str, Any]] = []
    for upload in files:
        if upload is None:
            continue
        try:
            data = await upload.read()
        except Exception as exc:  # noqa: BLE001
            results.append({"name": getattr(upload, "filename", "unknown"), "error": str(exc)})
            continue
        if len(data) > max_size:
            results.append(
                {
                    "name": getattr(upload, "filename", "unknown"),
                    "error": f"File exceeds {request.app.state.settings.max_upload_mb} MB limit",
                }
            )
            continue

        name = getattr(upload, "filename", "uploaded-file") or "uploaded-file"
        content_type = getattr(upload, "content_type", None) or "application/octet-stream"
        text = ""
        if _is_image_file(name, content_type):
            # Images are passed to ollama via Message.images (base64), not as text.
            text = ""
        elif content_type == "application/pdf" or name.lower().endswith(".pdf"):
            text = extract_pdf_text(data)
        else:
            # Try decode as text for text-like uploads.
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("cp932")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")

        chat_file = store.add_file(
            session_id=session_id,
            name=name,
            data=data,
            mime=content_type,
            text=text,
            source="upload",
        )
        results.append(chat_file.to_dict())

    return JSONResponse({"files": results})


async def delete_file(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    file_id = request.path_params["file_id"]
    ok = store.remove_file(session_id, file_id)
    if not ok:
        return JSONResponse({"error": "File not found"}, status_code=404)
    return JSONResponse({"deleted": True})