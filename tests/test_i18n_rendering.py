"""Tests that page rendering respects the configured language consistently."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.i18n import get_messages, t


@pytest.fixture
def reset_language():
    """Reset language to the initial value after each test."""
    original = settings.language
    yield
    settings.language = original
    if hasattr(get_messages, "cache_clear"):
        get_messages.cache_clear()


def _render_index() -> str:
    """Render index.html and return the HTML body."""
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"pin": settings.pin})
        assert response.status_code == 200
        response = client.get("/")
        return response.text


def test_english_language_consistency(reset_language):
    """When settings.language='en', all UI text including MCP must be English."""
    settings.language = "en"
    html = _render_index()
    # HTML-side labels
    assert 'lang="en"' in html
    assert "MCP Settings" in html
    assert "MCP Server Settings" in html
    assert "＋ Add server" in html
    # Japanese labels must not appear anywhere
    assert "MCP設定" not in html
    assert "MCPサーバー設定" not in html
    assert "＋ サーバー追加" not in html
    assert "セッション" not in html
    assert "モデル:" not in html


def test_japanese_language_consistency(reset_language):
    """When settings.language='ja', all UI text including MCP must be Japanese."""
    settings.language = "ja"
    html = _render_index()
    # HTML-side labels
    assert 'lang="ja"' in html
    assert "MCP設定" in html
    assert "MCPサーバー設定" in html
    assert "＋ サーバー追加" in html
    # English labels must not appear anywhere
    assert "MCP Settings" not in html
    assert "MCP Server Settings" not in html
    assert "＋ Add server" not in html
    assert "Sessions" not in html
    assert "Model:" not in html


def test_t_helper_respects_language_override():
    """The t() helper must return the requested language string."""
    assert t("common.send", lang="en") == "Send"
    assert t("common.send", lang="ja") == "送信"
    assert t("mcp.server_settings", lang="en") == "MCP Server Settings"
    assert t("mcp.server_settings", lang="ja") == "MCPサーバー設定"
