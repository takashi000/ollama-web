"""Cookie authentication and CSRF protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable
from http import HTTPStatus

from starlette.datastructures import URL
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

SESSION_COOKIE = "ollama_web_session"
CSRF_COOKIE = "ollama_web_csrf"
AUTH_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUTH_EXEMPT_PREFIXES = ("/static/",)
AUTH_EXEMPT_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/auth/status",
}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign(secret_key: str, value: str) -> str:
    sig = hmac.new(secret_key.encode(), value.encode(), hashlib.sha256).digest()
    return _b64(sig)


def make_session_cookie(secret_key: str) -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    payload = f"{issued_at}.{nonce}"
    return f"{payload}.{_sign(secret_key, payload)}"


def verify_session_cookie(secret_key: str, value: str | None) -> bool:
    if not value:
        return False
    parts = value.split(".")
    if len(parts) != 3:
        return False
    payload = ".".join(parts[:2])
    if not hmac.compare_digest(parts[2], _sign(secret_key, payload)):
        return False
    try:
        issued_at = int(parts[0])
    except ValueError:
        return False
    return 0 <= time.time() - issued_at <= AUTH_MAX_AGE_SECONDS


def set_auth_cookies(response: Response, secret_key: str) -> str:
    csrf = secrets.token_urlsafe(24)
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(secret_key),
        httponly=True,
        samesite="lax",
        max_age=AUTH_MAX_AGE_SECONDS,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        samesite="lax",
        max_age=AUTH_MAX_AGE_SECONDS,
    )
    return csrf


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


def is_authenticated(request: Request) -> bool:
    return verify_session_cookie(
        request.app.state.settings.secret_key,
        request.cookies.get(SESSION_COOKIE),
    )


def _is_exempt(path: str) -> bool:
    return path in AUTH_EXEMPT_PATHS or any(
        path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES
    )


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _login_redirect(request: Request) -> RedirectResponse:
    url = URL("/login").include_query_params(next=request.url.path)
    return RedirectResponse(str(url), status_code=303)


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect app routes with a signed cookie and CSRF token."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not _is_exempt(path) and not is_authenticated(request):
            if _wants_json(request):
                return JSONResponse(
                    {"error": "Authentication required"},
                    status_code=HTTPStatus.UNAUTHORIZED,
                )
            return _login_redirect(request)

        if (
            path.startswith("/api/")
            and path not in AUTH_EXEMPT_PATHS
            and request.method not in SAFE_METHODS
        ):
            token_cookie = request.cookies.get(CSRF_COOKIE)
            token_header = request.headers.get("x-csrf-token")
            if (
                not token_cookie
                or not token_header
                or not hmac.compare_digest(token_cookie, token_header)
            ):
                return JSONResponse(
                    {"error": "Invalid CSRF token"},
                    status_code=HTTPStatus.FORBIDDEN,
                )

        return await call_next(request)
