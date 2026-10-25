"""Real MCP transport with controllable list/call stalls and owned children."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import AudioContent, TextContent, Tool

marker = Path(sys.argv[1])
mode = sys.argv[2]
server = Server("lifecycle-fixture")


def mark(stage: str, **extra: object) -> None:
    with marker.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"stage": stage, "pid": os.getpid(), **extra}) + "\n")


@server.list_tools()
async def tools() -> list[Tool]:
    mark("list")
    if mode == "list":
        if len(sys.argv) > 3:
            await asyncio.Event().wait()
        else:
            time.sleep(3600)
    return [Tool(name="lookup", description="Look up the requested information.",
                 inputSchema={"type": "object", "properties": {}})]


@server.call_tool()
async def call(name: str, arguments: dict[str, object]) -> list[TextContent | AudioContent]:
    if len(sys.argv) > 3 and mode == "call":
        mark("call")
        await asyncio.Event().wait()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    mark("call", child=child.pid)
    if mode == "call":
        time.sleep(3600)
    child.terminate()
    child.wait()
    if mode == "media":
        return [TextContent(type="text", text="fixture result"), AudioContent(
            type="audio", data=base64.b64encode(b"fixture audio").decode(), mimeType="audio/mpeg",
        )]
    return [TextContent(type="text", text="fixture result")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    if len(sys.argv) > 3:
        import uvicorn
        from mcp.server.fastmcp.server import StreamableHTTPASGIApp
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route

        manager = StreamableHTTPSessionManager(server, json_response=True, stateless=True)

        @asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                mark("http-ready")
                yield

        app = Starlette(routes=[Route("/mcp", StreamableHTTPASGIApp(manager))], lifespan=lifespan)
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[3]), log_level="warning")
    else:
        asyncio.run(main())
