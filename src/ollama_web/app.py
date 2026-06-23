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
from .routes import sessions as sessions_route
from .sessions import SessionStore

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> Starlette:
    """Build and return the configured Starlette application."""
    routes = [
        Route("/", pages_route.index, name="index"),
        Route("/api/chat", chat_route.chat, methods=["POST"], name="chat"),
        Route("/api/models", models_route.get_models, methods=["GET"], name="models"),
        Route("/api/health", models_route.health, methods=["GET"], name="health"),
        Route("/api/sessions", sessions_route.list_sessions, methods=["GET"], name="list_sessions"),
        Route(
            "/api/sessions",
            sessions_route.create_session,
            methods=["POST"],
            name="create_session",
        ),
        Route(
            "/api/sessions/{session_id}",
            sessions_route.get_session,
            methods=["GET"],
            name="get_session",
        ),
        Route(
            "/api/sessions/{session_id}",
            sessions_route.delete_session,
            methods=["DELETE"],
            name="delete_session",
        ),
        Route(
            "/api/sessions/{session_id}/files",
            sessions_route.upload_file,
            methods=["POST"],
            name="upload_file",
        ),
        Route(
            "/api/sessions/{session_id}/files/{file_id}",
            sessions_route.delete_file,
            methods=["DELETE"],
            name="delete_file",
        ),
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
    app.state.session_store = SessionStore(settings.data_dir)
    app.state.tool_names = ["web_search", "scrape_url", "search_and_fetch", "pdf_to_text"]
    return app


app = create_app()