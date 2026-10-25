"""Behavioral coverage for dynamic context budgets and lossless tool pagination."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from ollama import Image, Message, ShowResponse

from ollama_web.context_budget import (
    DEFAULT_TOOL_SETTINGS,
    ContextBudget,
    ModelContext,
    ResultBudget,
    current_result_budget,
    validate_tool_settings,
)
from ollama_web.settings_store import load_app_settings, save_app_settings
from ollama_web.tools.helper.pagination import (
    PageSizeError,
    format_page_to_limit,
    make_token,
    parse_paged_result,
    parse_token,
    text_page,
)
from ollama_web.tools.registry import ToolRegistry


def metadata(capacity=131072):
    return {"model_info": {"general.architecture": "test", "test.context_length": capacity}}


@pytest.mark.parametrize(
    ("name", "info", "options", "expected", "source"),
    [
        ("test", metadata(), {"num_ctx": 8192}, 8192, "requested"),
        ("test", metadata(4096), {"num_ctx": 8192}, 4096, "model"),
        ("test", metadata(), {}, 131072, "model"),
        ("test:cloud", metadata(), {"num_ctx": 8192}, 131072, "model"),
        ("test:27b-cloud", metadata(), {"num_ctx": 8192}, 131072, "model"),
        ("test", {**metadata(), "remote_host": "https://example.com"}, {}, 131072, "model"),
        ("test:cloud", {}, {"num_ctx": 32768}, 32768, "requested"),
        ("test", {}, {}, 8192, "fallback"),
        ("test", metadata(-1), {"num_ctx": True}, 8192, "fallback"),
    ],
)
def test_capacity_resolution(name, info, options, expected, source):
    context = ModelContext.resolve(name, info, options)
    assert (context.capacity, context.source) == (expected, source)


def test_sdk_alias_and_runtime_capacity_only_shrink():
    budget = ContextBudget(ModelContext.resolve("test", ShowResponse(**metadata()), {}))
    assert budget.context.capacity == 131072
    budget.update_runtime({"models": [{"name": "other", "context_length": 4096}]}, "test")
    assert budget.context.capacity == 131072
    budget.update_runtime({"models": [{"name": "test:latest", "context_length": 65536}]}, "test")
    assert budget.context.capacity == 65536
    assert budget.context.source == "runtime"
    assert budget.context.estimated is False
    budget.update_runtime({"models": [{"name": "test", "context_length": 262144}]}, "test")
    assert budget.context.capacity == 65536


def test_cloud_does_not_use_local_runtime():
    budget = ContextBudget(ModelContext.resolve("test:cloud", metadata(), {}))
    budget.update_runtime(
        {"models": [{"name": "test:cloud", "context_length": 4096}]}, "test:cloud"
    )
    assert budget.context.capacity == 131072


@pytest.mark.parametrize("value", [0, -1, 1, 1.1, True, "0.8", None, float("nan"), float("inf")])
def test_invalid_usage_limit(value):
    with pytest.raises(ValueError):
        validate_tool_settings({"context_usage_limit": value})


def test_remaining_budget_is_used_without_double_counting():
    budget = ContextBudget(ModelContext.resolve("test", {}, {"num_ctx": 10000}))
    messages = [Message(role="user", content="read")]
    initial = budget.available(messages, []).tokens
    assert initial == 500
    # Leave 100 tokens in the total quota: execution remains possible below the per-call cap.
    message = Message(role="tool", content="x" * 9800)
    messages.append(message)
    budget.add_result(message)
    assert budget.used == 4900
    budget.observed_floor = budget.input_limit - 132
    budget.observed_tail = 0
    assert budget.available(messages, []).tokens == 100
    assert budget.headroom(messages, []) == 10000 - budget.input_tokens(messages, []) - 2000


def test_single_limit_applies_to_history_and_results_together():
    budget = ContextBudget(ModelContext.resolve("test", {}, {"num_ctx": 10000}))
    history = [Message(role="user", content="x" * 11000)]
    assert budget.input_limit == 8000
    assert budget.available(history, []).tokens > 0
    assert budget.used == 0
    budget.context_usage_limit = 0.5
    assert budget.input_limit == 5000
    assert budget.available(history, []).tokens == 0
    assert budget.stop_reason == "context_headroom"
    snapshot = budget.snapshot(history, [])
    assert snapshot["context_usage_limit"] == 0.5
    assert "ratios" not in snapshot
    assert "safety_margin" not in snapshot


@pytest.mark.parametrize(
    "text",
    [
        "hello" * 100,
        "調査結果" * 100,
        "def f(x):\n return x + 1\n" * 100,
        '{"items": [1, 2, 3]}' * 100,
    ],
)
def test_unicode_trim_and_measurement_correction(text):
    budget = ContextBudget(ModelContext.resolve("test", {}, {"num_ctx": 131072}))
    messages = [Message(role="user", content=text)]
    before = budget.input_tokens(messages, [])
    budget.observe({"prompt_eval_count": before * 2, "eval_count": 100}, before)
    assert budget.factor == 2
    assert budget.input_tokens(messages, []) >= before * 2 + 100
    correction = budget.factor
    budget.observe({"prompt_eval_count": 10, "eval_count": 1}, before)
    assert budget.factor == correction
    result_budget = ResultBudget(123, budget.factor)
    assert result_budget.fits(result_budget.trim(text))
    assert len(result_budget.trim(text)) < len(text)


def test_image_and_tool_definition_accounting():
    budget = ContextBudget(ModelContext.resolve("test", {}, {"num_ctx": 131072}))
    image = Message(role="user", content="describe", images=[Image(value=b"fake")])
    plain = Message(role="user", content="describe")
    assert budget.message_tokens(image) - budget.message_tokens(plain) == 4096
    schemas = [{"type": "function", "function": {"name": "lookup", "description": "x" * 500}}]
    assert budget.input_tokens([plain], schemas) > budget.input_tokens([plain], [])


def test_positive_generation_limit_is_reserved_without_overriding_it():
    budget = ContextBudget(ModelContext.resolve("test", {}, {"num_ctx": 8192}), num_predict=6000)
    assert budget.reserve == 6000
    budget.num_predict = None
    assert budget.reserve == 1639


def test_varying_page_budgets_preserve_all_source_characters():
    source = "日本語とEnglish\n" * 200
    registry = ToolRegistry()

    def read(offset: int) -> str:
        return format_page_to_limit(
            lambda size: text_page(source, offset, size),
            lambda cursor: make_token("read", "https://example.com", cursor["offset"]),
            requested_chars=10000,
            result_limit=None,
        )

    registry.register(read)
    offset = 0
    collected = []
    for step in range(100):
        budget = ResultBudget([240, 500, 1000][step % 3])
        result = registry.execute_detailed("read", {"offset": offset}, result_budget=budget).text
        assert budget.fits(result)
        body, more, token = parse_paged_result(result)
        collected.append(body)
        if not more:
            break
        offset = parse_token(token, "read")["offset"]
        assert offset == sum(map(len, collected))
    else:
        pytest.fail("Pagination did not finish")
    assert "".join(collected) == source


def test_impossible_envelope_does_not_return_a_partial_cursor():
    token = current_result_budget.set(ResultBudget(1))
    try:
        with pytest.raises(PageSizeError):
            format_page_to_limit(
                lambda size: text_page("x" * 100, 0, size),
                lambda cursor: make_token("read", "https://example.com", cursor["offset"]),
                requested_chars=50,
                result_limit=None,
            )
    finally:
        current_result_budget.reset(token)


def test_pdf_pages_fit_varying_token_budgets_without_content_loss(monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader

    from ollama_web.tools import pdf as pdf_mod
    from tests.test_tools import _make_multipage_pdf

    data = _make_multipage_pdf(["PAGE0-" + "A" * 2200, "PAGE1-" + "B" * 1100])
    expected = "\n\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    monkeypatch.setattr(
        pdf_mod, "fetch_public_bytes", lambda url, timeout=None: (data, url, "application/pdf")
    )
    registry = ToolRegistry()
    registry.register(pdf_mod.pdf_to_text)
    collected = []
    token = None
    for index in range(100):
        budget = ResultBudget([450, 900, 1400][index % 3])
        result = registry.execute_detailed(
            "pdf_to_text",
            {
                "url_or_path": "https://example.com/doc.pdf",
                "continue_from": token,
                "max_chars": 10000,
            },
            result_budget=budget,
        ).text
        assert budget.fits(result)
        body, more, token = parse_paged_result(result)
        collected.append(body)
        if not more:
            break
    else:
        pytest.fail("PDF pagination did not finish")
    assert "".join(collected) == expected


def test_media_omission_notice_survives_a_large_text_result():
    from ollama_web.llm import _trim_tool_result

    budget = ResultBudget(100)
    notice = "Image omitted from model input: insufficient context."
    result = _trim_tool_result("x" * 10000, budget, notice)
    assert budget.fits(result)
    assert notice in result
    assert "truncated" in result


def test_parallel_registry_execution_resets_context_even_on_error():
    registry = ToolRegistry()

    def read(fail: bool = False) -> int:
        budget = current_result_budget.get()
        assert budget is not None
        if fail:
            raise RuntimeError("expected")
        return budget.tokens

    registry.register(read)

    def run(limit):
        result = registry.execute_detailed("read", {}, result_budget=ResultBudget(limit))
        assert current_result_budget.get() is None
        return result.text

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run, [10, 100, 1000, 10000])) == ["10", "100", "1000", "10000"]
    registry.execute_detailed("read", {"fail": True}, result_budget=ResultBudget(10))
    assert current_result_budget.get() is None


def test_old_settings_preserve_options_when_budget_is_missing(tmp_path):
    old = {
        "ui": {"language": "en"},
        "ollama": {
            "system_prompt": "custom",
            "options": {"temperature": 0.3, "num_ctx": 131072},
        },
    }
    saved = save_app_settings(tmp_path, old)
    assert saved["tools"] == DEFAULT_TOOL_SETTINGS
    assert saved["ollama"] == old["ollama"]
    assert load_app_settings(tmp_path) == saved


@pytest.mark.parametrize("reserve,expected", [(0.2, 0.8), (0.3, 0.7)])
def test_old_four_ratio_settings_migrate_without_resetting_ollama(tmp_path, reserve, expected):
    import json

    old = {
        "ui": {"language": "ja"},
        "ollama": {
            "system_prompt": "keep this",
            "options": {"temperature": 1.2, "num_ctx": 131072},
        },
        "tool_budget": {
            "total_ratio": 0.5,
            "per_result_ratio": 0.05,
            "response_reserve_ratio": reserve,
            "safety_margin_ratio": 0.1,
        },
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    loaded = load_app_settings(tmp_path)
    assert loaded["ollama"] == old["ollama"]
    assert loaded["tools"] == {"context_usage_limit": expected}
    assert "tool_budget" not in loaded
    assert json.loads(path.read_text(encoding="utf-8")) == old
    saved = save_app_settings(tmp_path, loaded)
    assert saved["tools"] == {"context_usage_limit": expected}
    assert "tool_budget" not in json.loads(path.read_text(encoding="utf-8"))


def test_new_tool_settings_take_precedence_over_legacy_settings(tmp_path):
    value = {
        "ui": {"language": "en"},
        "ollama": {
            "system_prompt": "",
            "options": {"temperature": 0.8, "num_ctx": 8192},
        },
        "tools": {"context_usage_limit": 0.75},
        "tool_budget": {"response_reserve_ratio": 0.4},
    }
    assert save_app_settings(tmp_path, value)["tools"] == {"context_usage_limit": 0.75}


def test_model_metadata_failure_uses_request_capacity():
    from ollama_web.llm import get_model_info

    def retired(_model):
        raise RuntimeError("HTTP 410")

    info = get_model_info("retired:cloud", client=SimpleNamespace(show=retired))
    assert info == {}
    context = ModelContext.resolve("retired:cloud", info, {"num_ctx": 65536})
    assert (context.capacity, context.source, context.estimated) == (65536, "requested", True)


def test_large_context_continues_beyond_old_character_limit(monkeypatch):
    from ollama_web import llm

    registry = ToolRegistry()
    executed = []

    def lookup(index: int) -> str:
        executed.append(index)
        return "x" * 7000

    registry.register(lookup)

    class Client:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if not kwargs["tools"]:
                return [SimpleNamespace(message={"content": "partial answer"})]
            assert self.calls < 50
            return [
                SimpleNamespace(
                    message={
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "lookup",
                                    "arguments": {"index": self.calls},
                                }
                            }
                        ]
                    }
                )
            ]

    monkeypatch.setattr(llm, "get_client", lambda host=None: Client())
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research")],
            "test",
            capabilities={"tools"},
            registry=registry,
            options={"num_ctx": 131072},
            model_info=metadata(),
            language="en",
        )
    )
    results = [e["result"] for e in events if e["type"] == "tool_end"]
    assert sum(map(len, results)) > 24000
    assert sum(ResultBudget(0).estimate(result) for result in results) > 131072 * 0.5
    assert len(executed) > 5
    final = events[-1]
    assert final["type"] == "done"
    assert final["context_budget"]["tool_tokens"] <= int(131072 * 0.8)
    assert final["context_budget"]["stop_reason"] == "context_headroom"


def test_metadata_skip_does_not_block_a_smaller_call_in_same_batch(monkeypatch):
    from ollama_web import llm

    registry = ToolRegistry()
    executed = []

    def scrape_url(url: str) -> str:
        pytest.fail("Impossible page metadata must be rejected before tool execution")

    def lookup() -> str:
        executed.append(True)
        return "ok"

    registry.register(scrape_url)
    registry.register(lookup)

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
                                        "arguments": {
                                            "url": "https://example.com/" + "long" * 500,
                                        },
                                    }
                                },
                                {"function": {"name": "lookup", "arguments": {}}},
                            ]
                        }
                    )
                ]
            assert not kwargs["tools"]
            assert len([m for m in kwargs["messages"] if m.role == "tool"]) == 2
            return [SimpleNamespace(message={"content": "partial"})]

    monkeypatch.setattr(llm, "get_client", lambda host=None: Client())
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="research")],
            "test",
            capabilities={"tools"},
            registry=registry,
            options={"num_ctx": 8192},
            language="en",
        )
    )
    assert executed == [True]
    assert [e["name"] for e in events if e["type"] == "tool_start"] == ["lookup"]
    assert events[-1]["context_budget"]["stop_reason"] == "result_metadata_too_large"


def test_no_network_generation_when_history_and_reserve_do_not_fit(monkeypatch):
    from ollama_web import llm

    def chat(**kwargs):
        pytest.fail("An overfull conversation must not be submitted")

    monkeypatch.setattr(llm, "get_client", lambda host=None: SimpleNamespace(chat=chat))
    events = list(
        llm.stream_chat_with_tools(
            [Message(role="user", content="x" * 20000)],
            "test",
            model_info={},
            options={"num_ctx": 8192},
            language="en",
        )
    )
    assert events[-1]["type"] == "error"
    assert events[0]["stop_reason"] == "context_headroom"


@pytest.mark.parametrize("with_counts", [True, False])
def test_final_budget_survives_session_storage(tmp_path, with_counts):
    from ollama_web.routes.chat import _token_count_from_done
    from ollama_web.sessions import SessionStore

    budget = ContextBudget(ModelContext.resolve("test", metadata(), {"num_ctx": 65536}))
    snapshot = budget.snapshot([], [])
    done = {
        "prompt_eval_count": 200,
        "eval_count": 50,
        "num_ctx": 65536,
        "requested_num_ctx": 65536,
        "context_budget": snapshot,
    }
    if not with_counts:
        done.pop("prompt_eval_count")
        done.pop("eval_count")
    count = _token_count_from_done(done, done["num_ctx"])
    store = SessionStore(tmp_path)
    session = store.create()
    store.add_message(session["id"], "assistant", "answer", token_count=count)
    reloaded = store.get(session["id"])
    assert reloaded["token_count"]["context_budget"] == snapshot
    assert reloaded["messages"][-1]["token_count"]["requested_num_ctx"] == 65536


def test_sdk_transport_preserves_cloud_alias_metadata_with_one_request():
    import httpx
    import ollama

    from ollama_web.llm import get_model_info

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                **metadata(),
                "remote_host": "https://example.com",
                "remote_model": "remote",
                "capabilities": ["tools"],
            },
        )

    client = ollama.Client(host="http://fixture", transport=httpx.MockTransport(respond))
    info = get_model_info("custom-alias", client=client)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/show"
    assert info["capabilities"] == ["tools"]
    assert ModelContext.resolve("custom-alias", info, {"num_ctx": 8192}).capacity == 131072
