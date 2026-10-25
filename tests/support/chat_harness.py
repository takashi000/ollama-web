"""Real HTTP app plus a controllable external Ollama stand-in."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        threading.Event().wait(0.01)
    raise AssertionError("Condition did not become true before deadline")


def alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reparented zombie has stopped executing and has no open connections.
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rpartition(")")[2].split()[0] != "Z"
    except FileNotFoundError:
        return False


class Harness:
    def __init__(self, directory, mode="llm", mcp=None, real_host=None, model=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.model = model or "fixture"
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requests = []
        self.marker = self.directory / "mcp-events.jsonl"
        harness = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def answer(self, value):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(value).encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                self.answer({"models": [{"model": harness.model, "name": harness.model}]})

            def do_POST(self):
                request_mode = harness.mode
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    if request_mode == "show":
                        harness.entered.set()
                        harness.release.wait(30)
                    self.answer({"model_info": {}, "capabilities": ["completion", "tools"]})
                    return
                harness.requests.append(body)
                harness.entered.set()
                if request_mode in {"llm", "stream"}:
                    if request_mode == "stream":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-ndjson")
                        self.end_headers()
                        self.wfile.write(b'{"message":{"role":"assistant","content":"partial"}}\n')
                        self.wfile.flush()
                    harness.release.wait(30)
                    if request_mode == "stream":
                        try:
                            self.wfile.write(b'{"message":{"content":"LATE"},"done":true}\n')
                        except OSError:
                            pass
                        return
                if request_mode == "mcp-normal" and any(
                    m["role"] == "tool" for m in body["messages"]
                ):
                    self.answer({"message": {"role": "assistant", "content": "done"}, "done": True})
                elif request_mode in {"mcp", "mcp-normal"}:
                    self.answer({"message": {"role": "assistant", "tool_calls": [
                        {"function": {"name": "mcp__fixture__lookup", "arguments": {}}}
                    ]}, "done": True})
                elif request_mode == "empty":
                    self.answer({"message": {"role": "assistant", "content": ""}, "done": True})
                else:
                    self.answer({"message": {"role": "assistant", "content": "done"},
                                 "done": True})

        self.external = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.external.serve_forever, daemon=True)
        self.thread.start()
        self.host = real_host or f"http://127.0.0.1:{self.external.server_port}"
        self.mcp_process = None
        self.mcp_output = None
        if mcp:
            config = {"mcpServers": {"fixture": {
                "command": sys.executable,
                "args": [str(ROOT / "tests/support/mcp_fixture.py"), str(self.marker), mcp],
                "cwd": str(ROOT),
            }}}
            (self.directory / "mcpServers.json").write_text(json.dumps(config), encoding="utf-8")
            if mcp.startswith("http-"):
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    mcp_port = sock.getsockname()[1]
                self.mcp_output = (self.directory / "mcp-output.log").open("w")
                self.mcp_process = subprocess.Popen(
                    [sys.executable, str(ROOT / "tests/support/mcp_fixture.py"),
                     str(self.marker), mcp.removeprefix("http-"), str(mcp_port)],
                    stdout=self.mcp_output, stderr=subprocess.STDOUT,
                )
                wait_for(lambda: any(e["stage"] == "http-ready" for e in self.mcp_events()))
                config = {"mcpServers": {"fixture": {"url": f"http://127.0.0.1:{mcp_port}/mcp"}}}
                (self.directory / "mcpServers.json").write_text(
                    json.dumps(config), encoding="utf-8",
                )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "OLLAMA_HOST": self.host, "OLLAMA_WEB_MODEL": self.model,
               "OLLAMA_WEB_DATA_DIR": str(self.directory), "OLLAMA_WEB_PIN": "123456",
               "OLLAMA_WEB_LANGUAGE": "en", "OLLAMA_WEB_MCP_STDIO_ALLOWLIST": sys.executable,
               "PYTHONPATH": str(ROOT / "src")}
        self.output = (self.directory / "app-output.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "ollama_web.app:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"], cwd=ROOT, env=env,
            stdout=self.output, stderr=subprocess.STDOUT,
        )
        self.client = httpx.Client(base_url=self.url, timeout=10, trust_env=False)

        def available():
            if self.process.poll() is not None:
                raise AssertionError((self.directory / "app-output.log").read_text())
            try:
                return self.client.get("/api/auth/status").status_code == 200
            except httpx.TransportError:
                return False
        wait_for(available)
        login = self.client.post("/api/auth/login", json={"pin": "123456"})
        self.headers = {"X-CSRF-Token": login.headers["x-csrf-token"]}
        self.session = self.client.post("/api/sessions", headers=self.headers).json()["id"]

    def worker_pids(self):
        log = self.directory / "server.log"
        try:
            text = log.read_text(encoding="utf-8") if log.exists() else ""
        except OSError:
            return []
        return [int(pid) for pid in re.findall(r"chat worker started pid=(\d+)", text)]

    def mcp_events(self):
        if not self.marker.exists():
            return []
        try:
            return [json.loads(line) for line in self.marker.read_text().splitlines()]
        except (OSError, json.JSONDecodeError):
            # WSL's mounted Windows filesystem may expose an in-flight append.
            return []

    def close(self):
        self.release.set()
        self.client.close()
        self.process.terminate()
        self.process.wait(timeout=5)
        self.output.close()
        if self.mcp_process:
            self.mcp_process.kill()
            self.mcp_process.wait(timeout=5)
            self.mcp_output.close()
        self.external.shutdown()
        self.external.server_close()
        self.thread.join(timeout=2)


@contextmanager
def running_app(directory, **kwargs):
    harness = Harness(directory, **kwargs)
    try:
        yield harness
    finally:
        harness.close()
