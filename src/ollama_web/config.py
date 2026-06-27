"""Application configuration for ollama-web."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class Settings:
    """Runtime settings resolved from environment variables."""

    ollama_host: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    default_model: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_MODEL", "llama3.2")
    )
    host: str = field(default_factory=lambda: os.environ.get("OLLAMA_WEB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("OLLAMA_WEB_PORT", "8000")))
    data_dir: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_DATA_DIR", "data")
    )
    max_upload_mb: int = field(
        default_factory=lambda: int(os.environ.get("OLLAMA_WEB_MAX_UPLOAD_MB", "20"))
    )
    pin: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_PIN") or secrets.token_urlsafe(6)
    )
    pin_generated: bool = field(default_factory=lambda: "OLLAMA_WEB_PIN" not in os.environ)
    secret_key: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_SECRET_KEY") or secrets.token_urlsafe(32)
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: _csv_env("OLLAMA_WEB_ALLOWED_ORIGINS")
    )
    mcp_stdio_allowlist: list[str] = field(
        default_factory=lambda: _csv_env("OLLAMA_WEB_MCP_STDIO_ALLOWLIST")
    )
    mcp_https_allowlist: list[str] = field(
        default_factory=lambda: _csv_env("OLLAMA_WEB_MCP_HTTPS_ALLOWLIST")
    )
    ollama_timeout: float = 120.0
    scrape_timeout: float = 15.0
    scrape_max_chars: int = 4000
    search_max_results: int = 5
    fetch_max_results: int = 3
    fetch_max_chars: int = 8000
    max_fetch_mb: int = 10
    max_pdf_pages: int = 25
    max_message_chars: int = 20000
    max_attachment_text_chars: int = 20000
    max_image_pixels: int = 16_000_000
    max_tool_result_chars: int = 3000
    language: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_LANGUAGE", "ja")
    )


settings = Settings()
"""Process-wide settings instance."""
