"""Starlette application factory for ollama-web."""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import settings
from .routes import chat as chat_route
from .routes import models as models_route
from .routes import pages as pages_route

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> Starlette:
    """Build and return the configured Starlette application."""
    routes = [
        Route("/", pages_route.index, name="index"),
        Route("/api/chat", chat_route.chat, methods=["POST"], name="chat"),
        Route("/api/models", models_route.get_models, methods=["GET"], name="models"),
        Route("/api/health", models_route.health, methods=["GET"], name="health"),
        Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    app = Starlette(routes=routes, middleware=middleware)
    app.state.settings = settings
    app.state.tool_names = ["web_search", "scrape_url"]
    return app


app = create_app()