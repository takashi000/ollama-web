"""Application configuration for ollama-web."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Runtime settings resolved from environment variables."""

    ollama_host: str = field(default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    default_model: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_WEB_MODEL", "llama3.2")
    )
    host: str = field(default_factory=lambda: os.environ.get("OLLAMA_WEB_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("OLLAMA_WEB_PORT", "8000")))
    ollama_timeout: float = 120.0
    scrape_timeout: float = 15.0
    scrape_max_chars: int = 4000
    search_max_results: int = 5


settings = Settings()
"""Process-wide settings instance."""