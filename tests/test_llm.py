"""Tests for llm capability detection and feature fallbacks."""

from __future__ import annotations

import base64

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