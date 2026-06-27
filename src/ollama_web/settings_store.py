"""Persistent application settings stored under ``OLLAMA_WEB_DATA_DIR``."""

from __future__ import annotations

import json
import math
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

NUM_CTX_VALUES = (4096, 8192, 16384, 32768, 65536, 131072, 262144)
INTEGER_OPTIONS = {
    "num_ctx",
    "num_keep",
    "seed",
    "num_predict",
    "top_k",
    "repeat_last_n",
    "mirostat",
}
FLOAT_OPTIONS = {
    "temperature",
    "top_p",
    "min_p",
    "typical_p",
    "tfs_z",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
    "mirostat_tau",
    "mirostat_eta",
}
OPTION_KEYS = INTEGER_OPTIONS | FLOAT_OPTIONS | {"stop"}
SUPPORTED_LANGUAGES = {"ja", "en"}

_LOCK = threading.RLock()


class SettingsValidationError(ValueError):
    """Raised when persisted settings do not match the public schema."""


def default_app_settings(language: str = "ja") -> dict[str, Any]:
    """Return a fresh settings object with required slider defaults."""
    if language not in SUPPORTED_LANGUAGES:
        language = "ja"
    return {
        "ui": {"language": language},
        "ollama": {
            "system_prompt": "",
            "options": {"temperature": 0.8, "num_ctx": 8192},
        },
    }


def settings_file(data_dir: str | Path) -> Path:
    return Path(data_dir).resolve() / "settings.json"


def _require_keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise SettingsValidationError(
            f"Unknown {where} field(s): {', '.join(sorted(unknown))}"
        )


def _validate_options(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SettingsValidationError("ollama.options must be an object")
    _require_keys(raw, OPTION_KEYS, "option")

    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key in INTEGER_OPTIONS:
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingsValidationError(f"ollama.options.{key} must be an integer")
            cleaned[key] = value
        elif key in FLOAT_OPTIONS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsValidationError(f"ollama.options.{key} must be a number")
            number = float(value)
            if not math.isfinite(number):
                raise SettingsValidationError(f"ollama.options.{key} must be finite")
            cleaned[key] = number
        elif key == "stop":
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise SettingsValidationError(
                    "ollama.options.stop must be an array of non-empty strings"
                )
            cleaned[key] = list(value)

    if "temperature" not in cleaned or not 0 <= cleaned["temperature"] <= 2:
        raise SettingsValidationError("ollama.options.temperature must be between 0 and 2")
    if cleaned.get("num_ctx") not in NUM_CTX_VALUES:
        raise SettingsValidationError("ollama.options.num_ctx is not an allowed context size")
    return cleaned


def validate_app_settings(raw: Any) -> dict[str, Any]:
    """Validate and normalize the settings API payload."""
    if not isinstance(raw, dict):
        raise SettingsValidationError("Settings must be an object")
    _require_keys(raw, {"ui", "ollama"}, "settings")
    if set(raw) != {"ui", "ollama"}:
        raise SettingsValidationError("Settings must contain ui and ollama")

    ui = raw["ui"]
    if not isinstance(ui, dict):
        raise SettingsValidationError("ui must be an object")
    _require_keys(ui, {"language"}, "ui")
    language = ui.get("language")
    if language not in SUPPORTED_LANGUAGES:
        raise SettingsValidationError("ui.language must be ja or en")

    ollama = raw["ollama"]
    if not isinstance(ollama, dict):
        raise SettingsValidationError("ollama must be an object")
    _require_keys(ollama, {"system_prompt", "options"}, "ollama")
    if set(ollama) != {"system_prompt", "options"}:
        raise SettingsValidationError("ollama must contain system_prompt and options")
    system_prompt = ollama["system_prompt"]
    if not isinstance(system_prompt, str):
        raise SettingsValidationError("ollama.system_prompt must be a string")
    if len(system_prompt) > 20_000:
        raise SettingsValidationError("ollama.system_prompt is too long")

    return {
        "ui": {"language": language},
        "ollama": {
            "system_prompt": system_prompt,
            "options": _validate_options(ollama["options"]),
        },
    }


def load_app_settings(data_dir: str | Path, default_language: str = "ja") -> dict[str, Any]:
    """Load validated settings, falling back safely when absent or malformed."""
    default = default_app_settings(default_language)
    path = settings_file(data_dir)
    with _LOCK:
        if not path.exists():
            return default
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return validate_app_settings(raw)
        except (OSError, json.JSONDecodeError, SettingsValidationError):
            return default


def save_app_settings(data_dir: str | Path, raw: Any) -> dict[str, Any]:
    """Validate and atomically persist settings, returning the normalized value."""
    cleaned = validate_app_settings(raw)
    path = settings_file(data_dir)
    temp_path = path.with_suffix(".json.tmp")
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    return deepcopy(cleaned)
