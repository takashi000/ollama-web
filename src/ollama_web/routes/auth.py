"""Authentication routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..auth import clear_auth_cookies, is_authenticated, set_auth_cookies


async def login(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    pin = str(body.get("pin", ""))
    if pin != request.app.state.settings.pin:
        return JSONResponse({"authenticated": False, "error": "Invalid PIN"}, status_code=401)
    response = JSONResponse({"authenticated": True})
    csrf = set_auth_cookies(response, request.app.state.settings.secret_key)
    response.headers["X-CSRF-Token"] = csrf
    return response


async def logout(request: Request) -> JSONResponse:
    response = JSONResponse({"authenticated": False})
    clear_auth_cookies(response)
    return response


async def status(request: Request) -> JSONResponse:
    return JSONResponse({"authenticated": is_authenticated(request)})
