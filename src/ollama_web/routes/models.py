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


async def get_model_capabilities(request: Request) -> JSONResponse:
    """Return capabilities for a single model via ``ollama.show()``.

    The response contains the raw capability strings reported by ollama, e.g.
    ``{"capabilities": ["vision", "tools", "thinking"]}``. Unknown models or
    connection failures return an empty list.
    """
    model = request.path_params["model"]
    host = request.app.state.settings.ollama_host
    caps = sorted(llm.get_model_capabilities(model, host=host))
    return JSONResponse({"capabilities": caps})


async def health(request: Request) -> JSONResponse:
    """Report Ollama server reachability."""
    host = request.app.state.settings.ollama_host
    models = llm.list_models(host=host)
    reachable = models is not None
    return JSONResponse({"reachable": reachable, "models": models})