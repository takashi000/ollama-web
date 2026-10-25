"""Regression coverage for the dependency security update."""

from __future__ import annotations

import asyncio
import os
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


def _assert_child_exited(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            try:
                # TerminateProcess is asynchronous on Windows: wait for the
                # process object to signal instead of racing kernel teardown.
                assert kernel.WaitForSingleObject(handle, 5000) == 0, f"MCP child {pid} still runs"
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no process
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


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
    pid_log = tmp_path / "children.txt"
    server_script.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "with Path(sys.argv[1]).open('a', encoding='utf-8') as log:\n"
        "    log.write(str(os.getpid()) + '\\n')\n"
        "server = FastMCP('dependency-test')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config = {"command": sys.executable, "args": [str(server_script), str(pid_log)]}

    async def exercise():
        tools = await _list_tools_stdio("test", config)
        assert any(tool["function"]["name"] == "mcp__test__echo" for tool in tools)
        result = await _call_tool_stdio("test", config, "echo", {"text": "stdio-ok-日本語"})
        assert "stdio-ok-日本語" in result.text

    # Returning from both SDK context managers includes child-process cleanup.
    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
    pids = pid_log.read_text(encoding="utf-8").splitlines()
    assert len(pids) == 2
    for pid in pids:
        _assert_child_exited(int(pid))


def test_mcp_stdio_cancellation_terminates_child(tmp_path: Path):
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "waiting_server.py"
    script.write_text(
        "import os, sys, time\nfrom pathlib import Path\n"
        "ready = Path(sys.argv[1])\n"
        "ready.with_suffix('.tmp').write_text(str(os.getpid()), encoding='utf-8')\n"
        "ready.with_suffix('.tmp').replace(ready)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    async def exercise():
        config = {"command": sys.executable, "args": [str(script), str(pid_file)]}
        task = asyncio.create_task(
            _list_tools_stdio("test", config)
        )
        try:
            while not pid_file.exists():
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
    _assert_child_exited(int(pid_file.read_text(encoding="utf-8")))


def test_mcp_http_cancellation_closes_client(monkeypatch):
    original_client = httpx.AsyncClient
    clients: list[httpx.AsyncClient] = []
    started = asyncio.Event()

    async def handle(request):
        started.set()
        await asyncio.Event().wait()

    def make_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    async def exercise():
        monkeypatch.setattr(httpx, "AsyncClient", make_client)
        task = asyncio.create_task(_list_tools_http("test", {"url": "http://localhost/mcp"}))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert clients and all(client.is_closed for client in clients)

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))


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
