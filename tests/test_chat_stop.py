"""Real ASGI disconnects must reap all request-owned processes."""

import io
import time
from dataclasses import replace

import anyio
import pytest

from ollama_web import chat_worker
from ollama_web.config import settings
from ollama_web.sessions import SessionStore
from tests.support.chat_harness import alive, running_app, wait_for
from tests.support.pdf_worker import blocked_pdf_worker, crashing_worker


@pytest.mark.parametrize("mode,mcp", [("llm", None), ("show", None), ("stream", None),
                                      ("mcp", "list"), ("mcp", "call"), ("empty", None),
                                      ("mcp", "http-list"), ("mcp", "http-call")])
def test_disconnect_stops_request_process_tree(tmp_path, mode, mcp):
    with running_app(tmp_path, mode=mode, mcp=mcp) as app:
        with app.client.stream("POST", "/api/chat", headers=app.headers, json={
            "messages": [{"role": "user", "content": "research"}], "session_id": app.session,
        }) as response:
            chunks = response.iter_lines()
            assert next(chunks) == ":"
            pid = wait_for(lambda: app.worker_pids() and app.worker_pids()[-1])
            if mcp:
                stage = mcp.removeprefix("http-")
                events = wait_for(lambda: [e for e in app.mcp_events() if e["stage"] == stage])
                descendants = [] if mcp.startswith("http-") else [events[-1]["pid"]]
                if "child" in events[-1]:
                    descendants.append(events[-1]["child"])
            else:
                assert app.entered.wait(20)
                descendants = []
            if mode == "stream":
                wait_for(lambda: any('partial' in line for line in chunks))
            started = time.monotonic()
        wait_for(lambda: all(not alive(p) for p in [pid, *descendants]), timeout=3)
        assert time.monotonic() - started < 3
        saved = wait_for(lambda: app.client.get(f"/api/sessions/{app.session}").json()["messages"])
        app.release.set()
        assert app.client.get(f"/api/sessions/{app.session}").json()["messages"] == saved
        assert app.process.poll() is None
        if app.mcp_process:
            assert app.mcp_process.poll() is None
        assert app.thread.is_alive()


def test_stop_does_not_affect_another_request_and_allows_resend(tmp_path):
    with running_app(tmp_path, mode="llm") as app:
        payload = {"messages": [{"role": "user", "content": "one"}]}
        with app.client.stream("POST", "/api/chat", headers=app.headers, json=payload) as first:
            first_lines = first.iter_lines()
            next(first_lines)
            first_pid = wait_for(lambda: app.worker_pids() and app.worker_pids()[0])
            with app.client.stream(
                "POST", "/api/chat", headers=app.headers, json=payload,
            ) as second:
                second_lines = second.iter_lines()
                next(second_lines)
                second_pid = wait_for(lambda: len(app.worker_pids()) == 2 and app.worker_pids()[1])
                first.close()
                wait_for(lambda: not alive(first_pid), timeout=3)
                assert alive(second_pid)
        wait_for(lambda: not alive(second_pid), timeout=3)
        app.mode = "normal"
        response = app.client.post("/api/chat", headers=app.headers, json=payload)
        assert '"content": "done"' in response.text


def test_non_cooperative_pdf_parser_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_worker, "_child", blocked_pdf_worker)
    with running_app(tmp_path, mode="normal") as app:
        store = SessionStore(tmp_path)
        attachment = store.add_file(app.session, "slow.pdf", b"%PDF", "application/pdf", "")
        runtime = replace(settings, data_dir=str(tmp_path), ollama_host=app.host)
        marker = tmp_path / "parser.pid"
        payload = {"messages": [{"role": "user", "content": "read"}],
                   "file_ids": [attachment.id], "marker": str(marker)}

        async def run():
            async with anyio.create_task_group() as group:
                async def consume():
                    events = chat_worker.worker_events(runtime, store, payload, app.session)
                    async for _event in events:
                        pass

                group.start_soon(consume)
                with anyio.fail_after(20):
                    while not marker.exists():
                        await anyio.sleep(0.01)
                started = time.monotonic()
                group.cancel_scope.cancel()
            assert time.monotonic() - started < 3

        anyio.run(run)
        assert not alive(int(marker.read_text()))


def test_worker_transfers_media_without_changing_public_schema(tmp_path):
    with running_app(tmp_path, mode="mcp-normal", mcp="media") as app:
        response = app.client.post("/api/chat", headers=app.headers, json={
            "session_id": app.session, "messages": [{"role": "user", "content": "audio"}],
        })
        assert response.status_code == 200
        assert '"media":' in response.text
        session = app.client.get(f"/api/sessions/{app.session}").json()
        assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
        file = session["files"][0]
        assert session["messages"][-1]["attachments"] == [file["id"]]
        assert app.client.get(
            f"/api/sessions/{app.session}/files/{file['id']}/content"
        ).content == b"fixture audio"
        assert "run_id" not in response.text


def test_worker_can_read_existing_pdf_attachment(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    document = io.BytesIO()
    writer.write(document)
    with running_app(tmp_path, mode="normal") as app:
        store = SessionStore(tmp_path)
        file = store.add_file(
            app.session, "attached.pdf", document.getvalue(), "application/pdf", "",
        )
        response = app.client.post("/api/chat", headers=app.headers, json={
            "session_id": app.session, "messages": [{"role": "user", "content": "read"}],
            "file_ids": [file.id],
        })
        assert '"content": "done"' in response.text
        assert f'uri="attachment:{file.id}"' in app.requests[-1]["messages"][-1]["content"]


def test_unexpected_worker_exit_reports_error_before_save(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_worker, "_child", crashing_worker)
    store = SessionStore(tmp_path)
    events = []
    committed = []

    async def run():
        async for event in chat_worker.worker_events(
            replace(settings, data_dir=str(tmp_path)), store, {}, None,
            before_close=lambda: committed.extend(events),
        ):
            events.append(event)

    anyio.run(run)
    assert events[0]["type"] == "error"
    assert events[-1] == {"type": "done"}
    assert committed == events
