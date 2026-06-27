"""Page routes rendering Jinja2 templates."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from ollama_web import llm
from ollama_web.i18n import get_messages, t
from ollama_web.settings_store import load_app_settings

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["t"] = t


def _page_i18n(request: Request) -> tuple[str, dict[str, object]]:
    runtime = request.app.state.settings
    app_settings = load_app_settings(runtime.data_dir, runtime.language)
    language = str(app_settings["ui"]["language"])
    return language, get_messages(language)


async def index(request: Request) -> Response:
    """Render the chat page with the available model list."""
    host = request.app.state.settings.ollama_host
    models = llm.list_models(host=host)
    default_model = request.app.state.settings.default_model
    language, messages = _page_i18n(request)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "models": models,
            "default_model": default_model,
            "messages": messages,
            "t": lambda key, fallback=None: t(key, lang=language, fallback=fallback),
        },
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


async def login(request: Request) -> Response:
    """Render the PIN login page."""
    language, messages = _page_i18n(request)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "messages": messages,
            "t": lambda key, fallback=None: t(key, lang=language, fallback=fallback),
        },
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
