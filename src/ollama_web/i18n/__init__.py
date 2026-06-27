"""Internationalization support for ollama-web.

Messages live in JSON files under ``i18n/messages/``. The default language is
read from ``config.settings.language`` and can be overridden per-call via the
``lang`` argument.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ollama_web.config import settings

_DIR = Path(__file__).resolve().parent
_MESSAGES_DIR = _DIR / "messages"


def _load_messages(lang: str) -> dict[str, Any]:
    """Load messages for a language, falling back to an empty dict on error."""
    path = _MESSAGES_DIR / f"{lang}.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_MESSAGES_CACHE: dict[str, dict[str, Any]] = {}


def get_messages(lang: str | None = None) -> dict[str, Any]:
    """Return the full message dictionary for the requested language."""
    lang = resolve_language(lang)
    if lang not in _MESSAGES_CACHE:
        _MESSAGES_CACHE[lang] = _load_messages(lang)
    return _MESSAGES_CACHE[lang]


def _resolve(key: str, data: dict[str, Any]) -> Any:
    """Resolve a dot-separated key in a nested dictionary."""
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_language(lang_source: str | None = None) -> str:
    """Resolve the effective language.

    Priority:
        1. Explicit ``lang_source`` argument if it is a supported filename.
        2. ``settings.language`` if it is supported.
        3. ``"ja"`` as a safe default.

    Supported languages are determined by the JSON files present in
    ``i18n/messages/``.
    """
    supported = {p.stem for p in _MESSAGES_DIR.glob("*.json")}
    candidates = [lang_source, settings.language]
    for candidate in candidates:
        if candidate and candidate in supported:
            return candidate
    return "ja"


def t(key: str, lang: str | None = None, fallback: str | None = None) -> str:
    """Return a localized message string.

    Args:
        key: Dot-separated path, e.g. ``"chat.input_placeholder"``.
        lang: Optional language override. Uses ``settings.language`` when omitted.
        fallback: Value returned when the key is missing. Defaults to the key itself.

    Returns:
        The localized string, or the fallback value if missing.
    """
    value = _resolve(key, get_messages(resolve_language(lang)))
    if isinstance(value, str):
        return value
    return fallback if fallback is not None else key
