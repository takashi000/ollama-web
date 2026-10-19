"""Tests for application file logging."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from ollama_web.app import create_app
from ollama_web.config import settings
from ollama_web.tools.registry import default_registry


def test_server_log_is_scoped_to_data_dir_and_app_lifespan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    app_logger = logging.getLogger("ollama_web")
    original_handlers = tuple(app_logger.handlers)
    original_level = app_logger.level

    app = create_app()
    log_path = data_dir / "server.log"
    assert not log_path.exists()

    with TestClient(app):
        assert len(app_logger.handlers) == len(original_handlers) + 1
        logging.getLogger("ollama_web.test").info("lifespan log marker")

    assert tuple(app_logger.handlers) == original_handlers
    assert app_logger.level == original_level
    assert log_path.read_text(encoding="utf-8").count("lifespan log marker") == 1

    # Closing the lifespan must release the file on Windows as well.
    log_path.unlink()
    assert not log_path.exists()


def test_repeated_app_lifespans_do_not_duplicate_log_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    with TestClient(create_app()):
        logging.getLogger("ollama_web.test").info("first app marker")
    with TestClient(create_app()):
        logging.getLogger("ollama_web.test").info("second app marker")

    content = (data_dir / "server.log").read_text(encoding="utf-8")
    assert content.count("first app marker") == 1
    assert content.count("second app marker") == 1


def test_web_search_provider_error_is_written_to_server_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    class FailingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def text(self, query, max_results=5, *, region, backend):
            raise RuntimeError("403 Forbidden")

    with patch("ollama_web.tools.search.DDGS", FailingDDGS), TestClient(create_app()):
        result = json.loads(
            default_registry().execute("web_search", {"query": "private search terms"})
        )

    assert result == {"error": "RuntimeError: 403 Forbidden"}
    content = (data_dir / "server.log").read_text(encoding="utf-8")
    assert "ddgs web search failed" in content
    assert "RuntimeError: 403 Forbidden" in content
    assert "private search terms" not in content
