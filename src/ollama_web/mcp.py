"""MCP client integration for ollama-web.

This module loads MCP server configuration from ``data/mcpServers.json`` and
bridges remote MCP tools into the local ``ToolRegistry`` used by the ollama
tool-calling loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import settings

logger = logging.getLogger("ollama_web.mcp")

DEFAULT_TIMEOUT = 30.0

_MCP_TOOL_PREFIX = "mcp__"


def mcp_servers_file(data_dir: str | Path | None = None) -> Path:
    """Return the path to ``mcpServers.json`` under the configured data dir."""
    return Path(data_dir or settings.data_dir).resolve() / "mcpServers.json"


def load_mcp_config(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Load the MCP server configuration from disk.

    Returns:
      A dict with a ``mcpServers`` key, or an empty default config when the
      file does not exist or is malformed.
    """
    path = mcp_servers_file(data_dir)
    if not path.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read %s: %s", path, exc)
        return {"mcpServers": {}}

    if not isinstance(data, dict):
        return {"mcpServers": {}}
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {"mcpServers": {}}
    return {"mcpServers": servers}


def save_mcp_config(
    data_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """Persist the MCP server configuration to disk.

    Args:
      data_dir: Data directory root. Defaults to ``settings.data_dir``.
      config: Configuration dict; must contain a ``mcpServers`` key.
    """
    path = mcp_servers_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = config or {"mcpServers": {}}
    if "mcpServers" not in config:
        config = {"mcpServers": {}}
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_stdio(server: dict[str, Any]) -> bool:
    """Return whether the server definition describes a stdio transport."""
    return "command" in server


def _is_http(server: dict[str, Any]) -> bool:
    """Return whether the server definition describes an HTTP transport."""
    return "url" in server


def _normalized_tool_name(server_name: str, tool_name: str) -> str:
    """Build a unique ollama-visible tool name for an MCP tool."""
    return f"{_MCP_TOOL_PREFIX}{server_name}__{tool_name}"


def _parse_tool_name(full_name: str) -> tuple[str, str] | None:
    """Recover ``(server_name, tool_name)`` from a normalized MCP tool name."""
    if not full_name.startswith(_MCP_TOOL_PREFIX):
        return None
    rest = full_name[len(_MCP_TOOL_PREFIX) :]
    parts = rest.split("__", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _to_ollama_tool(server_name: str, tool: Any) -> dict[str, Any]:
    """Convert an MCP ``Tool`` object into an ollama-compatible tool dict.

    The resulting dict uses the OpenAI-compatible function-calling shape that
    ollama accepts as an element of the ``tools`` argument.
    """
    name = getattr(tool, "name", "")
    description = getattr(tool, "description", "") or name
    schema = getattr(tool, "inputSchema", None) or {}

    return {
        "type": "function",
        "function": {
            "name": _normalized_tool_name(server_name, str(name)),
            "description": str(description),
            "parameters": _clean_schema(schema),
        },
    }


def _clean_schema(schema: Any) -> dict[str, Any]:
    """Return a JSON schema suitable for ollama, removing unsupported keys."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}

    cleaned: dict[str, Any] = {
        "type": schema.get("type", "object"),
        "properties": {},
        "required": [],
    }
    properties = schema.get("properties")
    if isinstance(properties, dict):
        cleaned["properties"] = properties
    required = schema.get("required")
    if isinstance(required, list):
        cleaned["required"] = [str(r) for r in required]

    # Preserve additional metadata ollama understands, if present.
    for key in ("additionalProperties", "anyOf", "oneOf", "enum"):
        if key in schema:
            cleaned[key] = schema[key]
    return cleaned


async def _list_tools_for_server(
    server_name: str,
    server: dict[str, Any],
) -> list[dict[str, Any]]:
    """Connect to a single MCP server and return its tools in ollama form.

    Exceptions are caught and logged so that one misbehaving server does not
    break the whole tool registry.
    """
    tools: list[dict[str, Any]] = []
    if _is_stdio(server):
        tools = await _list_tools_stdio(server_name, server)
    elif _is_http(server):
        tools = await _list_tools_http(server_name, server)
    else:
        logger.warning("MCP server %s has no recognized transport", server_name)
    return tools


async def _list_tools_stdio(
    server_name: str,
    server: dict[str, Any],
) -> list[dict[str, Any]]:
    """List tools from a stdio MCP server."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    command = str(server["command"])
    args = [str(a) for a in server.get("args", [])]
    env = server.get("env")
    cwd = server.get("cwd")

    params = StdioServerParameters(
        command=command,
        args=args,
        env=env,
        cwd=cwd,
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return [_to_ollama_tool(server_name, t) for t in getattr(result, "tools", [])]


async def _list_tools_http(
    server_name: str,
    server: dict[str, Any],
) -> list[dict[str, Any]]:
    """List tools from an HTTP (streamable) MCP server."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = str(server["url"])
    headers: dict[str, str] | None = None
    raw_headers = server.get("headers")
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}

    timeout = server.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    sse_read_timeout = server.get("sse_read_timeout", DEFAULT_TIMEOUT)
    try:
        sse_read_timeout = float(sse_read_timeout)
    except (TypeError, ValueError):
        sse_read_timeout = DEFAULT_TIMEOUT

    client = httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout, read=sse_read_timeout),
    )
    async with client:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return [_to_ollama_tool(server_name, t) for t in getattr(result, "tools", [])]


async def collect_mcp_tools(
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect ollama-compatible tool definitions from all configured MCP servers.

    Args:
      config: MCP configuration dict. If None, loaded from disk.

    Returns:
      A list of ollama tool dicts (``{"type": "function", "function": {...}}``).
    """
    if config is None:
        config = load_mcp_config()

    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return []

    tasks = [
        _list_tools_for_server(name, server)
        for name, server in servers.items()
        if isinstance(server, dict)
    ]
    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_tools: list[dict[str, Any]] = []
    for name, result in zip(servers.keys(), results, strict=True):
        if isinstance(result, Exception):
            logger.warning("failed to list tools from MCP server %s: %s", name, result)
            continue
        all_tools.extend(result)
    return all_tools


def collect_mcp_tools_sync(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Synchronous wrapper around ``collect_mcp_tools``.

    Useful for callers running in a thread without an active event loop.
    """
    return asyncio.run(collect_mcp_tools(config))


async def _call_tool_on_server(
    server_name: str,
    server: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Call a single tool on the specified MCP server and return its result.

    The result content is flattened into a JSON string.
    """
    if _is_stdio(server):
        return await _call_tool_stdio(server_name, server, tool_name, arguments)
    if _is_http(server):
        return await _call_tool_http(server_name, server, tool_name, arguments)
    raise ValueError(f"server {server_name} has no recognized transport")


async def _call_tool_stdio(
    server_name: str,
    server: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    command = str(server["command"])
    args = [str(a) for a in server.get("args", [])]
    env = server.get("env")
    cwd = server.get("cwd")

    params = StdioServerParameters(
        command=command,
        args=args,
        env=env,
        cwd=cwd,
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return _format_tool_result(result)


async def _call_tool_http(
    server_name: str,
    server: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    url = str(server["url"])
    headers: dict[str, str] | None = None
    raw_headers = server.get("headers")
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}

    timeout = server.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    sse_read_timeout = server.get("sse_read_timeout", DEFAULT_TIMEOUT)
    try:
        sse_read_timeout = float(sse_read_timeout)
    except (TypeError, ValueError):
        sse_read_timeout = DEFAULT_TIMEOUT

    client = httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout, read=sse_read_timeout),
    )
    async with client:
        async with streamable_http_client(url, http_client=client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return _format_tool_result(result)


def _format_tool_result(result: Any) -> str:
    """Convert a ``CallToolResult`` into a JSON string for ollama."""
    content: list[Any] = []
    is_error = False
    if isinstance(result, dict):
        content = result.get("content", [])
        is_error = bool(result.get("isError"))
    else:
        content = getattr(result, "content", [])
        is_error = getattr(result, "isError", False)

    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            item_type = item.get("type", "")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif item_type == "image":
                parts.append(f"[image/{item.get('mimeType', 'unknown')}]")
            elif item_type == "resource":
                resource = item.get("resource", {})
                parts.append(_format_resource(resource))
        else:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                data = getattr(item, "data", None)
                mime = getattr(item, "mimeType", None)
                if data is not None:
                    parts.append(f"[image/{mime or 'unknown'}]")
                else:
                    resource = getattr(item, "resource", None)
                    if resource is not None:
                        parts.append(_format_resource(resource))

    body = "\n\n".join(parts)
    if is_error:
        return json.dumps({"error": body or "MCP tool returned an error"}, ensure_ascii=False)
    return json.dumps({"result": body}, ensure_ascii=False)


def _format_resource(resource: Any) -> str:
    """Convert an embedded resource reference into a short text snippet."""
    if isinstance(resource, dict):
        uri = resource.get("uri", "")
        mime = resource.get("mimeType", "")
        text = resource.get("text", "")
        if text:
            return f"[resource {uri} ({mime})]\n{text}"
        return f"[resource {uri} ({mime})]"
    return f"[resource {resource}]"


def call_mcp_tool_sync(
    config: dict[str, Any] | None,
    full_name: str,
    arguments: dict[str, Any] | str = "",
) -> str:
    """Synchronous wrapper for calling an MCP tool by its normalized name.

    This is registered as the executor in ``ToolRegistry`` so that the
    synchronous ollama tool loop can invoke remote MCP tools.

    Args:
      config: MCP configuration dict. If None, loaded from disk.
      full_name: Normalized MCP tool name (``mcp__server__tool``).
      arguments: Tool arguments as a dict or JSON string.

    Returns:
      A JSON-encoded result string, or an error object on failure.
    """
    parsed = _parse_tool_name(full_name)
    if parsed is None:
        return json.dumps({"error": f"Invalid MCP tool name: {full_name}"}, ensure_ascii=False)

    server_name, tool_name = parsed

    if config is None:
        config = load_mcp_config()
    server = config.get("mcpServers", {}).get(server_name)
    if not isinstance(server, dict):
        return json.dumps(
            {"error": f"MCP server not found for tool: {full_name}"}, ensure_ascii=False
        )

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return json.dumps(
                {"error": f"Invalid JSON arguments for {full_name}: {arguments}"},
                ensure_ascii=False,
            )

    try:
        return asyncio.run(_call_tool_on_server(server_name, server, tool_name, arguments))
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP tool %s execution failed: %s", full_name, exc)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def make_mcp_executor(
    config: dict[str, Any] | None = None,
) -> Callable[..., object]:
    """Return a callable that executes an MCP tool by normalized name.

    The returned callable accepts ``full_name`` (injected by the registry) and
    optionally ``arguments`` (ollama may pass extra keyword arguments).
    """
    if config is None:
        config = load_mcp_config()

    def executor(
        full_name: str,
        arguments: dict[str, Any] | str = "",
        **_kwargs: Any,
    ) -> str:
        return call_mcp_tool_sync(config, full_name, arguments)

    return executor