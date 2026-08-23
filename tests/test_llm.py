"""Tests for llm capability detection and feature fallbacks."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from ollama import Message
from ollama._types import Image as OllamaImage

from ollama_web.llm import (
    _embed_images_in_content,
    _is_image_message,
    _token_counts,
    stream_chat_with_tools,
)
from ollama_web.tools.registry import ToolRegistry


def _image_message() -> Message:
    return Message(
        role="user",
        content="hello",
        images=[OllamaImage(value=b"fake-image-bytes")],
    )


def test_is_image_message_detects_images() -> None:
    assert _is_image_message(_image_message()) is True


def test_is_image_message_without_images() -> None:
    assert _is_image_message(Message(role="user", content="hello")) is False


def test_embed_images_in_content_adds_base64_markdown() -> None:
    embedded = _embed_images_in_content(_image_message())
    encoded = base64.b64encode(b"fake-image-bytes").decode("ascii")
    assert f"![image](data:image/png;base64,{encoded})" in embedded.content
    assert not getattr(embedded, "images", None)


def test_stream_chat_with_tools_disables_unsupported_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported tools and thinking options are not sent to Ollama."""
    from ollama_web import llm

    registry = ToolRegistry()

    def lookup(query: str) -> str:
        return query

    registry.register(lookup)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            self.calls.append(kwargs)
            return [SimpleNamespace(message={"content": "done"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)

    events = list(
        stream_chat_with_tools(
            [Message(role="user", content="hi")],
            model="test-model",
            think=True,
            capabilities=set(),
            registry=registry,
        )
    )

    assert client.calls[0]["tools"] == []
    assert client.calls[0]["think"] is None
    assert client.calls[0]["stream"] is True
    assert events[-1] == {"type": "done"}


def test_token_counts_extracts_from_dict() -> None:
    assert _token_counts({"prompt_eval_count": 100, "eval_count": 50}) == {
        "prompt_eval_count": 100,
        "eval_count": 50,
    }


def test_token_counts_extracts_from_object() -> None:
    class FakeResp:
        prompt_eval_count = 200
        eval_count = 30

    assert _token_counts(FakeResp()) == {
        "prompt_eval_count": 200,
        "eval_count": 30,
    }


def test_token_counts_returns_none_when_missing() -> None:
    assert _token_counts({}) is None
    assert _token_counts(None) is None


def test_stream_chat_with_tools_disables_tools_for_final_answer_after_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ollama_web import llm

    executed: list[str] = []

    def lookup(query: str) -> str:
        """Return a test lookup result."""
        executed.append(query)
        return "lookup result"

    registry = ToolRegistry()
    registry.register(lookup)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [
                    SimpleNamespace(
                        message={
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "lookup",
                                        "arguments": {"query": "test"},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "final answer"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)

    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research this")],
            model="test-model",
            registry=registry,
            capabilities={"tools"},
            language="en",
        )
    )

    assert executed == ["test"]
    assert len(client.calls) == 2
    assert client.calls[0]["tools"]
    assert client.calls[1]["tools"] == []
    assert client.calls[0]["stream"] is True
    assert client.calls[1]["stream"] is True
    assert any(event.get("type") == "status" for event in events)
    assert any(event == {"type": "delta", "content": "final answer"} for event in events)
    assert events[-1] == {"type": "done"}


def test_stream_chat_with_tools_disables_mcp_tools_for_final_answer_after_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ollama_web import llm

    tool_name = "mcp__test__lookup"
    executed: list[tuple[str, dict[str, object] | str]] = []
    registry = ToolRegistry()

    def executor(full_name: str, arguments: dict[str, object] | str = "") -> str:
        executed.append((full_name, arguments))
        return "mcp lookup result"

    registry.register_mcp_tool(
        tool_name,
        {"type": "function", "function": {"name": tool_name}},
        executor,
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [
                    SimpleNamespace(
                        message={
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": tool_name,
                                        "arguments": {"query": "test"},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "final answer"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)

    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research this")],
            model="test-model",
            registry=registry,
            capabilities={"tools"},
            language="en",
        )
    )

    assert executed == [(tool_name, {"query": "test"})]
    assert len(client.calls) == 2
    assert client.calls[0]["tools"]
    assert client.calls[1]["tools"] == []
    assert client.calls[0]["stream"] is True
    assert client.calls[1]["stream"] is True
    assert any(event.get("type") == "status" for event in events)
    assert any(event == {"type": "delta", "content": "final answer"} for event in events)
    assert events[-1] == {"type": "done"}


def test_paged_source_limit_emits_incomplete_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ollama_web import llm

    registry = ToolRegistry()

    def scrape_url(url: str) -> str:
        """Return a paged result."""
        return "body\n---page-info---\nhas_more: true\nnext_token: token"

    registry.register(scrape_url)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [
                    SimpleNamespace(
                        message={
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "scrape_url",
                                        "arguments": {"url": "https://example.com"},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "partial answer"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="read all")],
            model="test-model",
            registry=registry,
            capabilities={"tools"},
            language="en",
        )
    )
    assert any(event.get("type") == "warning" for event in events)
    final_instruction = str(client.calls[-1]["messages"][-1].content)
    assert "unread content" in final_instruction


def test_paged_tool_is_not_executed_when_context_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ollama_web import llm

    executed: list[str] = []
    registry = ToolRegistry()

    def pdf_to_text(url_or_path: str) -> str:
        """Read a PDF."""
        executed.append(url_or_path)
        return "should not execute"

    registry.register(pdf_to_text)

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            self.calls += 1
            if self.calls == 1:
                return [
                    SimpleNamespace(
                        message={
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "pdf_to_text",
                                        "arguments": {"url_or_path": "https://example.com/a.pdf"},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "partial"})]

    monkeypatch.setattr(llm, "get_client", lambda _host=None: FakeClient())
    monkeypatch.setattr(llm, "MAX_TOOL_CONTEXT_CHARS", 1000)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="read all")],
            model="test-model",
            registry=registry,
            capabilities={"tools"},
            language="en",
        )
    )
    assert executed == []
    assert any("budget exhausted" in event.get("result", "") for event in events)
