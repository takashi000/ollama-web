"""Session management API routes."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..sessions import _IMAGE_MIMES, _IMAGE_SUFFIXES, SessionStore, is_valid_id
from ..tools.helper.pdf import extract_pdf_text

logger = logging.getLogger("ollama_web.sessions")

_UPLOAD_CHUNK_SIZE = 1024 * 1024


def _get_store(request: Request) -> SessionStore:
    return cast(SessionStore, request.app.state.session_store)


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
    raw = await request.body()
    body = json.loads(raw.decode("utf-8")) if raw else {}
    title = body.get("title")
    session = store.create(title=title)
    return JSONResponse(session)


async def get_session(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    if not is_valid_id(session_id):
        return JSONResponse({"error": "Invalid session id"}, status_code=400)
    session = store.get(session_id)
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse(session)


async def delete_session(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    if not is_valid_id(session_id):
        return JSONResponse({"error": "Invalid session id"}, status_code=400)
    ok = store.delete(session_id)
    if not ok:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse({"deleted": True})


async def upload_file(request: Request) -> JSONResponse:
    store = _get_store(request)
    session_id = request.path_params["session_id"]
    if not is_valid_id(session_id):
        return JSONResponse({"error": "Invalid session id"}, status_code=400)
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
        if not hasattr(upload, "read"):
            results.append({"name": "unknown", "error": "Invalid upload"})
            continue
        upload_file = cast(UploadFile, upload)
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await upload_file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise ValueError(
                        f"File exceeds {request.app.state.settings.max_upload_mb} MB limit"
                    )
                chunks.append(chunk)
            data = b"".join(chunks)
        except Exception as exc:  # noqa: BLE001
            results.append({"name": getattr(upload_file, "filename", "unknown"), "error": str(exc)})
            continue
        if len(data) > max_size:
            results.append(
                {
                    "name": getattr(upload_file, "filename", "unknown"),
                    "error": f"File exceeds {request.app.state.settings.max_upload_mb} MB limit",
                }
            )
            continue

        name = getattr(upload_file, "filename", "uploaded-file") or "uploaded-file"
        content_type = getattr(upload_file, "content_type", None) or "application/octet-stream"
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
    if not is_valid_id(session_id) or not is_valid_id(file_id):
        return JSONResponse({"error": "Invalid id"}, status_code=400)
    ok = store.remove_file(session_id, file_id)
    if not ok:
        return JSONResponse({"error": "File not found"}, status_code=404)
    return JSONResponse({"deleted": True})
