"""Tests for llm capability detection and feature fallbacks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from ollama import Message
from ollama._types import Image as OllamaImage

from ollama_web.llm import (
    _is_image_message,
    _remove_images_with_notice,
    _token_counts,
    stream_chat_with_tools,
)
from ollama_web.tools.registry import ToolArtifact, ToolExecutionResult, ToolRegistry


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


def test_remove_images_adds_localized_notice_without_base64() -> None:
    converted = _remove_images_with_notice(_image_message(), "ja")
    assert "画像入力に対応していません" in str(converted.content)
    assert "ZmFrZS1pbWFnZS1ieXRlcw" not in str(converted.content)
    assert not getattr(converted, "images", None)


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
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize(
    ("capabilities", "expect_images", "notice", "num_ctx"),
    [
        ({"tools", "vision"}, True, None, 131072),
        ({"tools"}, False, "画像の内容はモデルへ提供されていません", 8192),
        ({"tools", "vision"}, False, "コンテキスト予算に収まらない", 8192),
    ],
)
def test_mcp_image_is_only_sent_to_vision_models(
    monkeypatch: pytest.MonkeyPatch,
    capabilities: set[str],
    expect_images: bool,
    notice: str | None,
    num_ctx: int,
) -> None:
    from ollama_web import llm

    registry = ToolRegistry()

    def executor(full_name: str, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(
            text="tool text",
            artifacts=[
                ToolArtifact(
                    kind="image",
                    mime="image/png",
                    name="generated.png",
                    data=b"fake-image-bytes",
                    file_id="a" * 32,
                )
            ],
        )

    registry.register_mcp_tool(
        "mcp__test__image",
        {"type": "function", "function": {"name": "mcp__test__image"}},
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
                                        "name": "mcp__test__image",
                                        "arguments": {},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "done"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "resize_image", lambda data, name="": data)

    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="generate")],
            model="test-model",
            registry=registry,
            capabilities=capabilities,
            options={"num_ctx": num_ctx},
            language="ja",
        )
    )

    tool_message = next(
        message
        for message in reversed(client.calls[1]["messages"])
        if getattr(message, "role", None) == "tool"
    )
    assert bool(getattr(tool_message, "images", None)) is expect_images
    if notice is not None:
        assert notice in str(tool_message.content)
    assert any(
        event.get("media", [{}])[0].get("id") == "a" * 32 for event in events if event.get("media")
    )


def test_mcp_audio_is_never_sent_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ollama_web import llm

    registry = ToolRegistry()

    def executor(full_name: str, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(
            text="music generated",
            artifacts=[
                ToolArtifact(
                    kind="audio",
                    mime="audio/mpeg",
                    name="generated.mp3",
                    data=b"audio-bytes",
                    file_id="b" * 32,
                )
            ],
        )

    registry.register_mcp_tool(
        "mcp__test__audio",
        {"type": "function", "function": {"name": "mcp__test__audio"}},
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
                                        "name": "mcp__test__audio",
                                        "arguments": {},
                                    }
                                }
                            ],
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "done"})]

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)

    list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="generate")],
            model="test-model",
            registry=registry,
            capabilities={"tools", "vision"},
            language="ja",
        )
    )

    tool_message = next(
        message
        for message in reversed(client.calls[1]["messages"])
        if getattr(message, "role", None) == "tool"
    )
    assert not getattr(tool_message, "images", None)
    assert "音声データそのものはモデルへ提供されていません" in str(tool_message.content)
    assert "YXVkaW8tYnl0ZXM" not in str(tool_message.content)


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


@pytest.mark.parametrize("kind", ["builtin", "mcp", "mixed"])
def test_more_than_five_tool_rounds(monkeypatch, kind):
    from ollama_web import llm

    executed = []
    registry = ToolRegistry()

    def lookup(query: int) -> str:
        executed.append(query)
        return "short result"

    registry.register(lookup)
    remote = "mcp__test__lookup"
    registry.register_mcp_tool(
        remote,
        {"type": "function", "function": {"name": remote}},
        lambda name, arguments: lookup(**arguments),
    )

    class Client:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls > 8:
                return [SimpleNamespace(message={"content": "complete"})]
            assert kwargs["tools"]
            name = remote if kind == "mcp" or (kind == "mixed" and self.calls % 2) else "lookup"
            return [
                SimpleNamespace(
                    message={
                        "tool_calls": [
                            {
                                "function": {
                                    "name": name,
                                    "arguments": {"query": self.calls},
                                }
                            }
                        ]
                    }
                )
            ]

    client = Client()
    monkeypatch.setattr(llm, "get_client", lambda host=None: client)
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research")],
            "test",
            registry=registry,
            capabilities={"tools"},
        )
    )
    assert executed == list(range(1, 9))
    assert client.calls == 9
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize("name", ["lookup", "web_search", "mcp__test__lookup"])
@pytest.mark.parametrize("capacity", [16384, 65536, 131072])
def test_result_budget_is_shared_and_stops_rest_of_batch(monkeypatch, name, capacity):
    from ollama_web import llm
    from ollama_web.context_budget import ResultBudget

    executed = []
    registry = ToolRegistry()

    def lookup(query: int) -> str:
        executed.append(query)
        return "x" * 20000

    if name.startswith("mcp__"):
        registry.register_mcp_tool(
            name,
            {"type": "function", "function": {"name": name}},
            lambda full_name, arguments: lookup(**arguments),
        )
    else:
        lookup.__name__ = name
        registry.register(lookup)

    class Client:
        def __init__(self):
            self.calls = []

        def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [
                    SimpleNamespace(
                        message={
                            "tool_calls": [
                                {"function": {"name": name, "arguments": {"query": n}}}
                                for n in range(6)
                            ]
                        }
                    )
                ]
            assert kwargs["tools"] == []
            results = [m.content for m in kwargs["messages"] if m.role == "tool"]
            assert len(results) == 6
            return [SimpleNamespace(message={"content": "partial answer"})]

    client = Client()
    monkeypatch.setattr(llm, "get_client", lambda host=None: client)
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research")],
            "test",
            registry=registry,
            capabilities={"tools"},
            language="en",
            options={"num_ctx": capacity},
            tool_settings={"context_usage_limit": 0.2},
        )
    )
    executed_events = [e for e in events if e["type"] == "tool_end"]
    assert 1 <= len(executed) <= 6
    assert len(executed_events) == len(executed)
    assert all(ResultBudget(int(capacity * 0.05)).fits(e["result"]) for e in executed_events)
    assert events[-1]["context_budget"]["tool_tokens"] <= int(capacity * 0.2)
    assert events[-1]["context_budget"]["stop_reason"] == "context_headroom"
    assert len(client.calls) == 2


def test_paged_source_normal_finish_emits_incomplete_warning(monkeypatch):
    from ollama_web import llm

    registry = ToolRegistry()

    def scrape_url(url: str) -> str:
        return "body\n---page-info---\nhas_more: true\nnext_token: token"

    registry.register(scrape_url)

    class Client:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return [
                    SimpleNamespace(
                        message={
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "scrape_url",
                                        "arguments": {"url": "https://example.com"},
                                    }
                                }
                            ]
                        }
                    )
                ]
            return [SimpleNamespace(message={"content": "partial answer"})]

    client = Client()
    monkeypatch.setattr(llm, "get_client", lambda host=None: client)
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="read")],
            "test",
            registry=registry,
            capabilities={"tools"},
            language="en",
        )
    )
    assert any(e["type"] == "warning" for e in events)
    assert client.calls == 2


def test_empty_retry_budget_is_preserved(monkeypatch):
    from ollama_web import llm

    class Client:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            return [SimpleNamespace(message={"content": ""})]

    client = Client()
    monkeypatch.setattr(llm, "get_client", lambda host=None: client)
    monkeypatch.setattr("time.sleep", lambda duration: None)
    events = list(llm.stream_chat_with_tools([Message(role="user", content="test")], "test"))
    assert client.calls == 3
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "warning" for e in events)
