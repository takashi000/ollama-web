"""Application settings API endpoints."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..settings_store import (
    SettingsValidationError,
    load_app_settings,
    save_app_settings,
)


async def get_settings(request: Request) -> JSONResponse:
    """GET /api/settings: return effective persisted application settings."""
    runtime = request.app.state.settings
    return JSONResponse(load_app_settings(runtime.data_dir, runtime.language))


async def put_settings(request: Request) -> JSONResponse:
    """PUT /api/settings: validate and persist application settings."""
    try:
        body = await request.json()
        cleaned = save_app_settings(request.app.state.settings.data_dir, body)
    except SettingsValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    return JSONResponse(cleaned)
