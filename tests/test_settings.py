"""Tests for persisted general settings and Ollama integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from ollama import Message
from starlette.testclient import TestClient

from ollama_web import llm
from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.prompts import get_prompt
from ollama_web.routes.chat import _compose_system_prompt
from ollama_web.settings_store import (
    SettingsValidationError,
    load_app_settings,
    save_app_settings,
    validate_app_settings,
)
from ollama_web.tools.registry import ToolRegistry


def test_missing_settings_uses_slider_defaults_and_environment_language(tmp_path: Path) -> None:
    data = load_app_settings(tmp_path, "en")
    assert data == {
        "ui": {"language": "en"},
        "ollama": {
            "system_prompt": "",
            "options": {"temperature": 0.8, "num_ctx": 8192},
        },
    }


def test_settings_round_trip_preserves_valid_options(tmp_path: Path) -> None:
    value = {
        "ui": {"language": "ja"},
        "ollama": {
            "system_prompt": "Be concise.",
            "options": {
                "temperature": 0.4,
                "num_ctx": 16384,
                "seed": 42,
                "num_predict": 512,
                "top_p": 0.9,
                "stop": ["END", "<|eot_id|>"],
            },
        },
    }
    assert save_app_settings(tmp_path, value) == value
    assert load_app_settings(tmp_path, "en") == value
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk == value


def test_malformed_settings_file_falls_back_safely(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text("{not-json", encoding="utf-8")
    assert load_app_settings(tmp_path, "ja")["ollama"]["options"] == {
        "temperature": 0.8,
        "num_ctx": 8192,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update({"unknown": True}),
        lambda data: data["ollama"]["options"].update({"num_ctx": 12345}),
        lambda data: data["ollama"]["options"].update({"temperature": 2.1}),
        lambda data: data["ollama"]["options"].update({"seed": 1.5}),
        lambda data: data["ollama"]["options"].update({"top_p": "0.9"}),
        lambda data: data["ollama"]["options"].update({"stop": [""]}),
    ],
)
def test_invalid_settings_are_rejected(mutate: Any) -> None:
    data = {
        "ui": {"language": "ja"},
        "ollama": {
            "system_prompt": "",
            "options": {"temperature": 0.8, "num_ctx": 8192},
        },
    }
    mutate(data)
    with pytest.raises(SettingsValidationError):
        validate_app_settings(data)


def test_settings_api_and_page_elements(tmp_path: Path) -> None:
    original_data_dir = settings.data_dir
    settings.data_dir = str(tmp_path)
    try:
        with TestClient(create_app()) as client:
            login = client.post("/api/auth/login", json={"pin": settings.pin})
            csrf = login.headers["x-csrf-token"]
            response = client.get("/api/settings")
            assert response.status_code == 200
            payload = response.json()
            payload["ui"]["language"] = "en"
            payload["ollama"]["options"]["stop"] = ["END"]
            saved = client.put(
                "/api/settings",
                json=payload,
                headers={"X-CSRF-Token": csrf},
            )
            assert saved.status_code == 200
            assert client.get("/api/settings").json() == payload

            bad = dict(payload)
            bad["extra"] = True
            rejected = client.put(
                "/api/settings",
                json=bad,
                headers={"X-CSRF-Token": csrf},
            )
            assert rejected.status_code == 400

            html = client.get("/").text
            assert 'id="general-settings-btn"' in html
            assert 'data-settings-screen="general"' in html
            assert 'data-settings-screen="ui"' in html
            assert 'data-settings-screen="ollama"' in html
            num_keep = (
                'data-option="num_keep" data-type="int" type="number" '
                'step="1" placeholder="24"'
            )
            assert num_keep in html
            assert "&lt;|eot_id|&gt;" in html
            assert html.index('id="general-settings-btn"') < html.index('id="mcp-settings-btn"')
    finally:
        settings.data_dir = original_data_dir


def test_system_prompt_is_localized_and_appended() -> None:
    custom = "Always answer briefly."
    assert _compose_system_prompt("en", "") == get_prompt("tool_system", lang="en")
    assert _compose_system_prompt("en", custom) == (
        f"{get_prompt('tool_system', lang='en')}\n\n{custom}"
    )


def test_options_are_forwarded_to_tool_and_final_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "missing_tool", "arguments": {}}}
                        ],
                    }
                }
            return {"message": {"role": "assistant", "content": "done"}}

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)
    options = {"temperature": 0.8, "num_ctx": 8192, "top_p": 0.9}
    events = list(
        llm.chat_with_tools(
            [Message(role="user", content="hello")],
            "test-model",
            capabilities={"tools"},
            registry=ToolRegistry(),
            options=options,
            language="en",
        )
    )
    assert events[-1] == {"type": "done"}
    assert len(client.calls) == 2
    assert all(call["options"] == options for call in client.calls)
