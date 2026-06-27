"""Tests that page rendering respects the configured language consistently."""

from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup
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


def _render_pages():
    """Render login and authenticated index pages with their response headers."""
    app = create_app()
    with TestClient(app) as client:
        login_page = client.get("/login")
        response = client.post("/api/auth/login", json={"pin": settings.pin})
        assert response.status_code == 200
        index_page = client.get("/")
    return login_page, index_page


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


@pytest.mark.parametrize(
    ("lang", "delete_text", "server_settings", "login_title"),
    [
        ("en", "Delete", "MCP Server Settings", "Login"),
        ("ja", "削除", "MCPサーバー設定", "ログイン"),
    ],
)
def test_browser_messages_are_embedded_as_json_data(
    reset_language,
    lang: str,
    delete_text: str,
    server_settings: str,
    login_title: str,
):
    """Both pages expose parseable localized data without executable inline scripts."""
    settings.language = lang
    login_page, index_page = _render_pages()

    for response in (login_page, index_page):
        soup = BeautifulSoup(response.text, "html.parser")
        data_element = soup.select_one('meta[name="ollama-web-i18n"]')
        assert data_element is not None

        messages = json.loads(data_element["content"])
        assert messages["html"]["lang"] == lang
        assert messages["common"]["delete"] == delete_text
        assert messages["mcp"]["server_settings"] == server_settings
        assert messages["login"]["title"] == login_title

        inline_scripts = [script for script in soup.find_all("script") if not script.get("src")]
        assert not inline_scripts


def test_i18n_pages_keep_strict_script_csp(reset_language):
    """The localization transport must not require weakening script-src."""
    login_page, index_page = _render_pages()

    for response in (login_page, index_page):
        csp = response.headers["content-security-policy"]
        script_directive = next(
            directive.strip()
            for directive in csp.split(";")
            if directive.strip().startswith("script-src")
        )
        assert script_directive == "script-src 'self'"
