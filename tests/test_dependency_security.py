"""Regression coverage for the dependency security update."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import jwt
import pytest
from mcp.server.fastmcp import FastMCP

from ollama_web.mcp import (
    _call_tool_http,
    _call_tool_stdio,
    _list_tools_http,
    _list_tools_stdio,
)

_KEY = "dependency-regression-test-secret-at-least-32-bytes"


@pytest.mark.parametrize("method", ["decode", "decode_complete"])
@pytest.mark.parametrize(
    ("claims", "error"),
    [
        ({"exp": 1}, jwt.ExpiredSignatureError),
        ({"aud": "wrong"}, jwt.InvalidAudienceError),
        ({"iss": "wrong"}, jwt.InvalidIssuerError),
    ],
)
def test_pyjwt_preserves_options_and_rejects_invalid_claims(method, claims, error):
    """GHSA-gvp8-978c-rx2q: an unverified peek must not disable later checks."""
    decode = getattr(jwt, method)
    payload = {"aud": "expected", "iss": "expected", **claims}
    token = jwt.encode(payload, _KEY, algorithm="HS256")
    options = {"verify_signature": False}

    decode(token, options=options)
    assert options == {"verify_signature": False}

    options["verify_signature"] = True
    with pytest.raises(error):
        decode(
            token,
            _KEY,
            algorithms=["HS256"],
            options=options,
            audience="expected",
            issuer="expected",
        )
    assert options == {"verify_signature": True}


def test_mcp_stdio_lists_calls_and_closes(tmp_path: Path):
    server_script = tmp_path / "mcp_server.py"
    server_script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('dependency-test')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config = {"command": sys.executable, "args": [str(server_script)]}

    async def exercise():
        tools = await _list_tools_stdio("test", config)
        assert any(tool["function"]["name"] == "mcp__test__echo" for tool in tools)
        result = await _call_tool_stdio("test", config, "echo", {"text": "stdio-ok"})
        assert "stdio-ok" in result.text

    # Returning from both SDK context managers includes child-process cleanup.
    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


def test_mcp_http_lists_calls_and_terminates_sessions(monkeypatch):
    sdk = FastMCP("dependency-test", json_response=True)

    @sdk.tool()
    def echo(text: str) -> str:
        return text

    app = sdk.streamable_http_app()
    original_client = httpx.AsyncClient
    methods: list[str] = []
    clients: list[httpx.AsyncClient] = []

    async def exercise():
        asgi_transport = httpx.ASGITransport(app=app)

        async def handle(request):
            methods.append(request.method)
            # A server may disable the optional standalone SSE GET stream.
            if request.method == "GET":
                return httpx.Response(405)
            return await asgi_transport.handle_async_request(request)

        def make_client(**kwargs):
            client = original_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", make_client)
        async with sdk.session_manager.run():
            config = {"url": "http://localhost:8000/mcp"}
            tools = await _list_tools_http("test", config)
            assert any(tool["function"]["name"] == "mcp__test__echo" for tool in tools)
            result = await _call_tool_http("test", config, "echo", {"text": "http-ok"})
            assert "http-ok" in result.text
        assert methods.count("DELETE") == 2
        assert len(clients) == 2
        assert all(client.is_closed for client in clients)

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
