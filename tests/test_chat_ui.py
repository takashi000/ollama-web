"""Playwright exercises the existing Stop button against the real HTTP app."""

import os
import re
import time

import pytest

from tests.support.chat_harness import alive, running_app, wait_for

pytestmark = pytest.mark.ui


@pytest.fixture
def browser_page(tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        yield page
        page.screenshot(path=str(tmp_path / "last-screen.png")) if not page.is_closed() else None
        context.tracing.stop(path=str(tmp_path / "trace.zip"))
        browser.close()


def login(page, app):
    page.goto(app.url)
    page.locator("#pin-input").fill("123456")
    page.locator("#login-form button").click()
    page.locator("#model-select option").first.wait_for(state="attached")


@pytest.mark.parametrize("mode,mcp", [("llm", None), ("stream", None),
                                      ("mcp", "list"), ("mcp", "call"), ("empty", None),
                                      ("mcp", "http-call")])
def test_stop_button_reaps_worker_and_discards_late_output(tmp_path, browser_page, mode, mcp):
    page = browser_page
    with running_app(tmp_path / "app", mode=mode, mcp=mcp) as app:
        login(page, app)
        page.locator("#input").fill("research")
        page.locator("#send-btn").click()
        pid = wait_for(lambda: app.worker_pids() and app.worker_pids()[-1])
        descendants = []
        if mcp:
            stage = mcp.removeprefix("http-")
            events = wait_for(lambda: [e for e in app.mcp_events() if e["stage"] == stage])
            descendants = [] if mcp.startswith("http-") else [events[-1]["pid"]]
            if "child" in events[-1]:
                descendants.append(events[-1]["child"])
        else:
            assert app.entered.wait(20)
        if mode == "stream":
            page.get_by_text("partial", exact=True).wait_for()
        started = time.monotonic()
        page.locator("#cancel-btn").click()
        page.locator("#cancel-btn").dispatch_event("click")
        wait_for(lambda: all(not alive(p) for p in [pid, *descendants]), timeout=3)
        elapsed = time.monotonic() - started
        assert elapsed < 3
        print(f"stop mode={mode} mcp={mcp} elapsed={elapsed:.3f}s")
        app.release.set()
        if app.mcp_process:
            assert app.mcp_process.poll() is None
        page.locator("#send-btn").wait_for(state="visible")
        assert page.locator("#cancel-btn").is_disabled()
        assert "LATE" not in page.locator("#chat").inner_text()
        saved = app.client.get("/api/sessions").json()
        # The UI creates/selects its own session.
        session_id = saved[0]["id"] if isinstance(saved, list) else saved["sessions"][0]["id"]
        messages = app.client.get(f"/api/sessions/{session_id}").json()["messages"]
        if mode == "stream":
            assert messages[-1]["content"] == "partial"
        page.reload()
        assert "LATE" not in page.locator("#chat").inner_text()
        assert app.client.get(f"/api/sessions/{session_id}").json()["messages"] == messages
        app.mode = "normal"
        # Remove stalled listing for the next request.
        (app.directory / "mcpServers.json").write_text('{"mcpServers": {}}')
        page.locator("#input").fill("again")
        page.locator("#send-btn").click()
        page.get_by_text("done", exact=True).first.wait_for()


def test_closing_tab_stops_stream(tmp_path, browser_page):
    page = browser_page
    with running_app(tmp_path / "app", mode="llm") as app:
        login(page, app)
        page.locator("#input").fill("research")
        page.locator("#send-btn").click()
        pid = wait_for(lambda: app.worker_pids() and app.worker_pids()[-1])
        assert app.entered.wait(20)
        started = time.monotonic()
        page.close()
        wait_for(lambda: not alive(pid), timeout=3)
        assert time.monotonic() - started < 3


@pytest.mark.parametrize("finish_race", [False, True])
def test_stop_and_immediate_resend(tmp_path, browser_page, finish_race):
    from playwright.sync_api import expect

    page = browser_page
    with running_app(tmp_path / "app", mode="stream") as app:
        login(page, app)
        page.locator("#input").fill("first request")
        page.locator("#send-btn").click()
        page.get_by_text("partial", exact=True).wait_for()
        old_pid = app.worker_pids()[-1]
        if finish_race:
            app.release.set()
        page.locator("#cancel-btn").dispatch_event("click")
        expect(page.locator("#send-btn")).to_be_enabled()
        # Resend without waiting for server-side process cleanup.
        app.mode = "normal"
        page.locator("#input").fill("second request")
        page.locator("#send-btn").click()
        page.get_by_text("done", exact=True).first.wait_for()
        wait_for(lambda: not alive(old_pid), timeout=3)
        app.release.set()
        sessions = app.client.get("/api/sessions").json()["sessions"]
        session = app.client.get(f"/api/sessions/{sessions[0]['id']}").json()
        assert [m["content"] for m in session["messages"] if m["role"] == "user"] == [
            "first request", "second request",
        ]
        answers = [m["content"] for m in session["messages"] if m["role"] == "assistant"]
        assert len(answers) == 2
        assert answers[0] in {"partial", "partialLATE"}
        assert answers[1] == "done"


@pytest.mark.skipif(not os.environ.get("OLLAMA_WEB_LIVE_MODEL"), reason="Set live model explicitly")
def test_live_ollama_stop_and_resend(tmp_path, browser_page):
    from playwright.sync_api import expect

    model = os.environ["OLLAMA_WEB_LIVE_MODEL"]
    with running_app(tmp_path / "live", real_host="http://127.0.0.1:11434", model=model) as app:
        (app.directory / "settings.json").write_text(
            '{"ollama":{"options":{"num_predict":64,"num_ctx":4096,"temperature":0.0}}}',
            encoding="utf-8",
        )
        page = browser_page
        login(page, app)
        page.locator("#model-select").select_option(model)
        page.locator("#input").fill("Without using tools, list 1000 computing facts.")
        page.locator("#send-btn").click()
        pid = wait_for(lambda: app.worker_pids() and app.worker_pids()[-1])
        expect(page.locator(".message.assistant .body").last).to_contain_text(
            re.compile("[A-Za-z0-9]"), timeout=120000,
        )
        page.locator("#cancel-btn").click()
        wait_for(lambda: not alive(pid), timeout=3)
        page.locator("#input").fill("Reply with only OK.")
        page.locator("#send-btn").click()
        expect(page.locator("#cancel-btn")).to_be_disabled(timeout=120000)
        assert "OK" in page.locator("#chat").inner_text()


@pytest.mark.skipif(not os.environ.get("OLLAMA_WEB_LIVE_MODEL"), reason="Set live model explicitly")
def test_live_ollama_stdio_stop_and_resend(tmp_path, browser_page):
    from playwright.sync_api import expect

    model = os.environ["OLLAMA_WEB_LIVE_MODEL"]
    with running_app(
        tmp_path / "live-mcp", real_host="http://127.0.0.1:11434", model=model, mcp="call",
    ) as app:
        (app.directory / "settings.json").write_text(
            '{"ollama":{"options":{"num_predict":256,"num_ctx":4096,"temperature":0.0}}}',
            encoding="utf-8",
        )
        page = browser_page
        login(page, app)
        page.locator("#model-select").select_option(model)
        page.locator("#input").fill(
            "Call mcp__fixture__lookup once to look up information, then report its result."
        )
        page.locator("#send-btn").click()
        event = wait_for(
            lambda: next((e for e in app.mcp_events() if e["stage"] == "call"), None),
            timeout=120,
        )
        pid = app.worker_pids()[-1]
        started = time.monotonic()
        page.locator("#cancel-btn").click()
        wait_for(lambda: all(not alive(p) for p in [pid, event["pid"], event["child"]]), timeout=3)
        print(f"live stdio stop elapsed={time.monotonic() - started:.3f}s")
        (app.directory / "mcpServers.json").write_text('{"mcpServers": {}}')
        page.locator("#input").fill("Reply with only OK. Do not use tools.")
        page.locator("#send-btn").click()
        expect(page.locator("#cancel-btn")).to_be_disabled(timeout=120000)
        assert "OK" in page.locator("#chat").inner_text()
