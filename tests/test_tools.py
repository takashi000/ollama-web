"""Tests for tool registry and built-in tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ollama_web.tools.registry import ToolRegistry, default_registry


def test_registry_register_and_execute():
    reg = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two numbers.

        Args:
          a: First number.
          b: Second number.

        Returns:
          The sum.
        """
        return a + b

    reg.register(add)
    assert reg.get("add") is add
    result = reg.execute("add", {"a": 2, "b": 3})
    assert result == "5"


def test_registry_execute_unknown():
    reg = ToolRegistry()
    out = reg.execute("nonexistent", {})
    assert "error" in json.loads(out)


def test_registry_execute_invalid_json_args():
    reg = ToolRegistry()

    def echo(x: str) -> str:
        return x

    reg.register(echo)
    out = reg.execute("echo", "{not json")
    assert "error" in json.loads(out)


def test_registry_execute_handles_exceptions():
    reg = ToolRegistry()

    def boom() -> None:
        raise ValueError("boom")

    reg.register(boom)
    out = reg.execute("boom", {})
    assert "error" in json.loads(out)


def test_default_registry_has_tools():
    reg = default_registry()
    assert "web_search" in reg.names
    assert "scrape_url" in reg.names


def test_web_search_empty_query():
    from ollama_web.tools.search import web_search

    assert web_search("") == []


def test_web_search_mocked():
    from ollama_web.tools import search as search_mod

    fake = [{"title": "T", "href": "http://x", "body": "B"}]

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def text(self, q, max_results=5):
            return fake

    monkey = MagicMock()
    monkey.DDGS = FakeDDGS
    with patch.object(search_mod, "DDGS", FakeDDGS):
        from ollama_web.tools.search import web_search

        result = web_search("test", max_results=1)
    assert len(result) == 1
    assert result[0]["title"] == "T"


def test_scrape_url_empty():
    from ollama_web.tools.scrape import scrape_url

    assert scrape_url("") == "Invalid URL."