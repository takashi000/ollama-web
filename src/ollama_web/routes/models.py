"""API routes for model listing and health checks."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from ollama_web import llm


async def get_models(request: Request) -> JSONResponse:
    """Return available Ollama models as JSON."""
    host = request.app.state.settings.ollama_host
    models = llm.list_models(host=host)
    return JSONResponse({"models": models})


async def health(request: Request) -> JSONResponse:
    """Report Ollama server reachability."""
    host = request.app.state.settings.ollama_host
    models = llm.list_models(host=host)
    reachable = models is not None
    return JSONResponse({"reachable": reachable, "models": models})