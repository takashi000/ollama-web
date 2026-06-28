"""Tests that page rendering respects the configured language consistently."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.i18n import get_messages, t
from ollama_web.settings_store import save_app_settings


@pytest.fixture
def reset_language(tmp_path):
    """Reset language and data_dir to their initial values after each test."""
    original_language = settings.language
    original_data_dir = settings.data_dir
    settings.data_dir = str(tmp_path)
    yield tmp_path
    settings.language = original_language
    settings.data_dir = original_data_dir
    if hasattr(get_messages, "cache_clear"):
        get_messages.cache_clear()


def _write_settings(data_dir: Path, language: str) -> None:
    """Persist a minimal settings file with the requested UI language."""
    save_app_settings(
        data_dir,
        {
            "ui": {"language": language},
            "ollama": {
                "system_prompt": "",
                "options": {"temperature": 0.8, "num_ctx": 8192},
            },
        },
    )


def _render_index(data_dir: Path, language: str) -> str:
    """Render index.html and return the HTML body."""
    _write_settings(data_dir, language)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"pin": settings.pin})
        assert response.status_code == 200
        response = client.get("/")
        return response.text


def _render_pages(data_dir: Path, language: str):
    """Render login and authenticated index pages with their response headers."""
    _write_settings(data_dir, language)
    app = create_app()
    with TestClient(app) as client:
        login_page = client.get("/login")
        response = client.post("/api/auth/login", json={"pin": settings.pin})
        assert response.status_code == 200
        index_page = client.get("/")
    return login_page, index_page


def test_english_language_consistency(reset_language):
    """When persisted language='en', all UI text including MCP must be English."""
    data_dir = reset_language
    html = _render_index(data_dir, "en")
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
    """When persisted language='ja', all UI text including MCP must be Japanese."""
    data_dir = reset_language
    html = _render_index(data_dir, "ja")
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
    data_dir = reset_language
    login_page, index_page = _render_pages(data_dir, lang)

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
    data_dir = reset_language
    login_page, index_page = _render_pages(data_dir, settings.language)

    for response in (login_page, index_page):
        csp = response.headers["content-security-policy"]
        script_directive = next(
            directive.strip()
            for directive in csp.split(";")
            if directive.strip().startswith("script-src")
        )
        assert script_directive == "script-src 'self'"
