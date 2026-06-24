"""Main entry point for the ollama-web application."""

from __future__ import annotations

import sys

import uvicorn

from .config import settings


def main() -> int:
    """Run the application."""
    print(f"ollama-web starting on http://{settings.host}:{settings.port}")
    print(f"Ollama host: {settings.ollama_host}")
    if settings.pin_generated:
        print(f"Generated ollama-web PIN: {settings.pin}")
        print("Set OLLAMA_WEB_PIN to use a stable PIN.")
    uvicorn.run(
        "ollama_web.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
