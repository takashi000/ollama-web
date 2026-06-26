"""Tests for MCP client integration and settings API."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.mcp import (
    _clean_schema,
    _normalized_tool_name,
    _parse_tool_name,
    load_mcp_config,
    save_mcp_config,
)
from ollama_web.tools.registry import ToolRegistry


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "pin", "123456")
    monkeypatch.setattr(settings, "secret_key", "test-secret")
    monkeypatch.setattr(settings, "allowed_origins", [])
    monkeypatch.setattr(settings, "mcp_stdio_allowlist", [sys.executable])
    monkeypatch.setattr(settings, "mcp_https_allowlist", [])
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(settings, "data_dir", tmp)
        client = TestClient(create_app())
        login = client.post("/api/auth/login", json={"pin": "123456"})
        assert login.status_code == 200
        client.csrf = login.headers["x-csrf-token"]
        yield client


def test_load_save_mcp_config():
    with tempfile.TemporaryDirectory() as tmp:
        config = {
            "mcpServers": {
                "fs": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                },
                "remote": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer x"},
                },
            }
        }
        save_mcp_config(tmp, config)
        loaded = load_mcp_config(tmp)
        assert loaded == config
        assert (Path(tmp) / "mcpServers.json").exists()


def test_load_mcp_config_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        loaded = load_mcp_config(tmp)
        assert loaded == {"mcpServers": {}}


def test_mcp_config_api(client):
    res = client.get("/api/mcp/servers")
    assert res.status_code == 200
    assert res.json() == {"mcpServers": {}}

    payload = {
        "mcpServers": {
            "fs": {
                "command": sys.executable,
                "args": ["mcp_servers/calc_server.py"],
            },
            "remote": {"url": "http://127.0.0.1:9000/mcp", "timeout": 60},
        }
    }
    res = client.put(
        "/api/mcp/servers",
        json=payload,
        headers={"X-CSRF-Token": client.csrf},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["mcpServers"]["fs"]["command"] == sys.executable
    assert data["mcpServers"]["remote"]["timeout"] == 60.0

    res = client.get("/api/mcp/servers")
    assert res.json() == data


def test_mcp_config_api_rejects_unknown_fields(client):
    payload = {"mcpServers": {"fs": {"command": "npx", "invalid": 1}}}
    res = client.put(
        "/api/mcp/servers",
        json=payload,
        headers={"X-CSRF-Token": client.csrf},
    )
    assert res.status_code == 400


def test_mcp_config_api_requires_transport(client):
    payload = {"mcpServers": {"fs": {}}}
    res = client.put(
        "/api/mcp/servers",
        json=payload,
        headers={"X-CSRF-Token": client.csrf},
    )
    assert res.status_code == 400


def test_normalized_tool_name():
    assert _normalized_tool_name("server1", "tool_a") == "mcp__server1__tool_a"


def test_parse_tool_name():
    assert _parse_tool_name("mcp__server1__tool_a") == ("server1", "tool_a")
    assert _parse_tool_name("web_search") is None


def test_clean_schema():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    cleaned = _clean_schema(schema)
    assert cleaned["type"] == "object"
    assert cleaned["required"] == ["path"]


def test_tool_registry_mcp_tool():
    reg = ToolRegistry()

    def executor(full_name, arguments=""):
        return "ok"

    reg.register_mcp_tool(
        "mcp__s__t", {"type": "function", "function": {"name": "mcp__s__t"}}, executor
    )
    assert "mcp__s__t" in reg.names
    # Executor is registered internally for execution but must not be exposed
    # to ollama as a callable (ollama needs the dict definition only).
    assert len(reg.ollama_tools) == 1
    result = reg.execute("mcp__s__t", {})
    assert result == "ok"
