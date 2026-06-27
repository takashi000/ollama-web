"""Localized prompt management for LLM prompts.

Prompts live in JSON files under ``prompts/``. The default language is read from
``config.settings.language`` and can be overridden per-call via the ``lang``
argument.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ollama_web.config import settings

_DIR = Path(__file__).resolve().parent

_PROMPTS_CACHE: dict[str, dict[str, Any]] = {}


def _load_prompts(lang: str) -> dict[str, Any]:
    """Load prompts for a language, falling back to an empty dict on error."""
    path = _DIR / f"{lang}.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_prompts(lang: str | None = None) -> dict[str, Any]:
    """Return the full prompt dictionary for the requested language."""
    lang = lang or settings.language or "ja"
    if lang not in _PROMPTS_CACHE:
        _PROMPTS_CACHE[lang] = _load_prompts(lang)
    return _PROMPTS_CACHE[lang]


def get_prompt(key: str, lang: str | None = None, fallback: str | None = None) -> str:
    """Return a localized LLM prompt string.

    Args:
        key: Dot-separated path, e.g. ``"tool_system"``.
        lang: Optional language override. Uses ``settings.language`` when omitted.
        fallback: Value returned when the key is missing. Defaults to the key itself.

    Returns:
        The localized prompt string, or the fallback value if missing.
    """
    current: Any = get_prompts(lang)
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return fallback if fallback is not None else key
        current = current[part]
    if isinstance(current, str):
        return current
    return fallback if fallback is not None else key
