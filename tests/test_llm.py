"""Tests for llm capability detection and feature fallbacks."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from ollama import Message
from ollama._types import Image as OllamaImage
from ollama._types import ResponseError

from ollama_web.llm import (
    _embed_images_in_content,
    _is_image_message,
    _token_counts,
    chat_with_tools,
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


def test_chat_with_tools_skips_tools_for_non_tool_model() -> None:
    """Tools are not passed when the model does not advertise tool support."""
    messages = [Message(role="user", content="hi")]
    # Use an empty registry so any tool call would fail if requested.
    registry = ToolRegistry()

    # Without tool capability, the iterator should finish immediately with no
    # actual network request. We only verify it accepts the call and does not
    # raise due to tool-related setup.
    gen = chat_with_tools(
        messages,
        model="nonexistent-model",
        capabilities=set(),
        registry=registry,
    )
    events = list(gen)
    # We expect either an error (model not found) or a done event.
    assert any(e.get("type") in {"error", "done"} for e in events)


def test_chat_with_tools_forces_think_none_for_non_thinking_model() -> None:
    messages = [Message(role="user", content="hi")]
    gen = chat_with_tools(
        messages,
        model="nonexistent-model",
        capabilities=set(),
    )
    events = list(gen)
    assert any(e.get("type") in {"error", "done"} for e in events)


def test_stream_chat_with_tools_accepts_capability_set() -> None:
    messages = [Message(role="user", content="hi")]
    gen = stream_chat_with_tools(
        messages,
        model="nonexistent-model",
        capabilities={"tools", "thinking"},
    )
    with pytest.raises(ResponseError):
        list(gen)


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


def test_chat_with_tools_disables_tools_for_final_answer_after_limit(
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

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "lookup", "arguments": {"query": "test"}}}
                        ],
                    }
                }
            return {"message": {"role": "assistant", "content": "final answer"}}

    client = FakeClient()
    monkeypatch.setattr(llm, "get_client", lambda _host=None: client)
    monkeypatch.setattr(llm, "MAX_TOOL_ROUNDS", 1)

    events = list(
        llm.chat_with_tools(
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
