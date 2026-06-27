"""Tests that i18n keys referenced in code and templates exist in JSON files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ollama_web import i18n, prompts


_SRC = Path(__file__).resolve().parent.parent / "src" / "ollama_web"


def _json_keys(data: dict, prefix: str = "") -> set[str]:
    """Return all dot-separated keys in a nested dict."""
    keys: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        keys.add(full)
        if isinstance(value, dict):
            keys.update(_json_keys(value, full))
    return keys


def _extract_template_keys(path: Path) -> set[str]:
    """Find t('...') calls in Jinja2 templates."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"{{\s*t\(['\"]([^'\"]+)['\"]", text))


def _extract_js_keys(path: Path) -> set[str]:
    """Find t("...") / t('...') calls in JavaScript files."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\bt\(['\"]([^'\"]+)['\"]", text))


def _extract_python_t_keys(path: Path) -> set[str]:
    """Find t(...) calls in Python source."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\bt\(['\"]([^'\"]+)['\"]", text))


def _extract_python_prompt_keys(path: Path) -> set[str]:
    """Find get_prompt(...) calls in Python source."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\bget_prompt\(['\"]([^'\"]+)['\"]", text))


def _referenced_message_keys() -> set[str]:
    """Collect every message key referenced from templates, JS and Python."""
    referenced: set[str] = set()
    for path in _SRC.rglob("*.html"):
        referenced.update(_extract_template_keys(path))
    for path in _SRC.rglob("*.js"):
        referenced.update(_extract_js_keys(path))
    for path in _SRC.rglob("*.py"):
        referenced.update(_extract_python_t_keys(path))
    return referenced


def _referenced_prompt_keys() -> set[str]:
    """Collect every prompt key referenced from Python."""
    referenced: set[str] = set()
    for path in _SRC.rglob("*.py"):
        referenced.update(_extract_python_prompt_keys(path))
    return referenced


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_all_message_keys_exist(lang: str):
    """Every referenced message key must exist for each supported language."""
    messages = i18n.get_messages(lang)
    available = _json_keys(messages)
    missing = _referenced_message_keys() - available
    assert not missing, f"Missing keys in i18n/messages/{lang}.json: {sorted(missing)}"


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_all_prompt_keys_exist(lang: str):
    """Every referenced prompt key must exist for each supported language."""
    prompt_data = prompts.get_prompts(lang)
    available = _json_keys(prompt_data)
    missing = _referenced_prompt_keys() - available
    assert not missing, f"Missing keys in prompts/{lang}.json: {sorted(missing)}"


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_json_files_are_valid(lang: str):
    """Both localization JSON files must be valid and parseable."""
    assert isinstance(i18n.get_messages(lang), dict)
    assert isinstance(prompts.get_prompts(lang), dict)


def test_t_returns_expected_strings():
    """Basic sanity checks for the t() helper."""
    assert i18n.t("common.send") == "送信"
    assert i18n.t("common.send", lang="en") == "Send"
    assert i18n.t("missing.key", fallback="fallback") == "fallback"
    assert i18n.t("missing.key") == "missing.key"


def test_get_prompt_returns_expected_strings():
    """Basic sanity checks for the get_prompt() helper."""
    assert prompts.get_prompt("tool_system") == prompts.get_prompts("ja")["tool_system"]
    assert prompts.get_prompt("tool_system", lang="en") == prompts.get_prompts("en")["tool_system"]
    assert prompts.get_prompt("missing.prompt", fallback="fallback") == "fallback"
    assert prompts.get_prompt("missing.prompt") == "missing.prompt"
