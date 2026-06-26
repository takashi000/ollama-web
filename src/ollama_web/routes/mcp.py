"""MCP server configuration API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..mcp import (
    load_mcp_config,
    redact_secrets,
    save_mcp_config,
    validate_mcp_server_config,
    validate_mcp_server_name,
)

logger = logging.getLogger("ollama_web.mcp_route")


_ALLOWED_STDIO_FIELDS = {"command", "args", "env", "cwd", "encoding", "encoding_error_handler"}
_ALLOWED_HTTP_FIELDS = {"url", "headers", "timeout", "sse_read_timeout", "terminate_on_close"}


async def get_servers(request: Request) -> JSONResponse:
    """GET /api/mcp/servers: return the current MCP server configuration."""
    data_dir = request.app.state.settings.data_dir
    config = load_mcp_config(data_dir)
    return JSONResponse(redact_secrets(config))


async def put_servers(request: Request) -> JSONResponse:
    """PUT /api/mcp/servers: persist a new MCP server configuration.

    The body must be a JSON object with a ``mcpServers`` key. Each server is
    validated to contain only recognized fields and a single transport.
    """
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalid MCP config JSON: %s", exc)
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    if not isinstance(body, dict) or "mcpServers" not in body:
        return JSONResponse(
            {"error": "Body must contain a 'mcpServers' object"},
            status_code=400,
        )

    raw_servers = body["mcpServers"]
    if not isinstance(raw_servers, dict):
        return JSONResponse(
            {"error": "'mcpServers' must be an object"},
            status_code=400,
        )

    cleaned: dict[str, Any] = {"mcpServers": {}}
    errors: list[str] = []
    data_dir = request.app.state.settings.data_dir
    for raw_name, server in raw_servers.items():
        try:
            name = validate_mcp_server_name(raw_name)
        except ValueError as exc:
            errors.append(f"Server '{raw_name}' invalid name: {exc}")
            continue

        if not isinstance(server, dict):
            errors.append(f"Server '{name}' must be an object")
            continue

        if "command" in server:
            valid = _allowed_fields(server, _ALLOWED_STDIO_FIELDS, name, errors)
            if valid:
                cleaned_server = _clean_stdio_server(server)
                try:
                    validate_mcp_server_config(name, cleaned_server, data_dir)
                except ValueError as exc:
                    errors.append(f"Server '{name}' invalid stdio config: {exc}")
                    continue
                cleaned["mcpServers"][name] = cleaned_server
        elif "url" in server:
            valid = _allowed_fields(server, _ALLOWED_HTTP_FIELDS, name, errors)
            if valid:
                cleaned_server = _clean_http_server(server)
                try:
                    validate_mcp_server_config(name, cleaned_server, data_dir)
                except ValueError as exc:
                    errors.append(f"Server '{name}' invalid http config: {exc}")
                    continue
                cleaned["mcpServers"][name] = cleaned_server
        else:
            errors.append(
                f"Server '{name}' must define either 'command' (stdio) or 'url' (http)"
            )

    if errors:
        return JSONResponse({"error": errors}, status_code=400)

    save_mcp_config(data_dir, cleaned)
    return JSONResponse(cleaned)


def _allowed_fields(
    server: dict[str, Any],
    allowed: set[str],
    name: str,
    errors: list[str],
) -> bool:
    unknown = set(server.keys()) - allowed
    if unknown:
        errors.append(f"Server '{name}' has unknown fields: {', '.join(sorted(unknown))}")
        return False
    return True


def _clean_stdio_server(server: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"command": str(server["command"])}
    if "args" in server:
        out["args"] = [str(a) for a in server["args"]]
    if "env" in server:
        out["env"] = {str(k): str(v) for k, v in server["env"].items()}
    if "cwd" in server and server["cwd"]:
        out["cwd"] = str(server["cwd"])
    for key in ("encoding", "encoding_error_handler"):
        if key in server:
            out[key] = str(server[key])
    return out


def _clean_http_server(server: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"url": str(server["url"])}
    if "headers" in server:
        out["headers"] = {str(k): str(v) for k, v in server["headers"].items()}
    for key in ("timeout", "sse_read_timeout"):
        if key in server:
            try:
                out[key] = float(server[key])
            except (TypeError, ValueError):
                pass
    if "terminate_on_close" in server:
        out["terminate_on_close"] = bool(server["terminate_on_close"])
    return out
