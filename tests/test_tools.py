"""Tests for tool registry and built-in tools."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from ollama_web.tools.helper.pagination import (
    InvalidTokenError,
    format_paged_result,
    make_token,
    parse_paged_result,
    parse_token,
)
from ollama_web.tools.registry import ToolRegistry, default_registry

# ---------------------------------------------------------------------------
# Registry tests (existing)
# ---------------------------------------------------------------------------


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
    assert "pdf_to_text" in reg.names
    assert "search_and_fetch" in reg.names


# ---------------------------------------------------------------------------
# web_search tests (existing)
# ---------------------------------------------------------------------------


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


def test_web_search_returns_empty_list_for_no_provider_results() -> None:
    from ollama_web.tools import search as search_mod

    class EmptyDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def text(self, query, max_results=5):
            return []

    with patch.object(search_mod, "DDGS", EmptyDDGS):
        assert search_mod.web_search("nothing found") == []


def test_web_search_logs_and_propagates_provider_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ollama_web.tools import search as search_mod

    class FailingDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def text(self, query, max_results=5):
            raise RuntimeError("403 Forbidden")

    with (
        patch.object(search_mod, "DDGS", FailingDDGS),
        caplog.at_level("ERROR", logger="ollama_web.tools.search"),
    ):
        with pytest.raises(RuntimeError, match="403 Forbidden"):
            search_mod.web_search("private search terms")

    assert "DuckDuckGo web search failed" in caplog.text
    assert "private search terms" not in caplog.text

    registry = ToolRegistry()
    registry.register(search_mod.web_search)
    with patch.object(search_mod, "DDGS", FailingDDGS):
        result = json.loads(registry.execute("web_search", {"query": "test"}))
    assert result == {"error": "RuntimeError: 403 Forbidden"}


# ---------------------------------------------------------------------------
# scrape_url tests (existing + pagination)
# ---------------------------------------------------------------------------


def test_scrape_url_empty():
    from ollama_web.tools.scrape import scrape_url

    assert scrape_url("") == "Invalid URL."


def _fake_fetch_public_bytes(html: bytes, content_type: str = "text/html; charset=utf-8"):
    """Return a fake fetch_public_bytes that yields the given HTML."""
    return MagicMock(return_value=(html, "https://example.com/page", content_type))


def test_scrape_url_no_continue_from_returns_first_page():
    """continue_from=None returns the first page with has_more + next_token."""
    from ollama_web.tools import scrape as scrape_mod

    # Build HTML longer than max_chars so pagination kicks in.
    body = "<article>" + ("A" * 100) + "</article>"
    html = f"<html><body>{body}</body></html>"
    with patch.object(
        scrape_mod, "fetch_public_bytes", _fake_fetch_public_bytes(html.encode("utf-8"))
    ):
        result = scrape_mod.scrape_url("https://example.com/page", max_chars=40)
    body_text, has_more, next_token = parse_paged_result(result)
    assert len(body_text) == 40
    assert has_more is True
    assert next_token is not None


def test_scrape_url_continue_from_returns_next_page():
    """Passing next_token returns the continuation page."""
    from ollama_web.tools import scrape as scrape_mod

    body = "<article>" + ("A" * 100) + "</article>"
    html = f"<html><body>{body}</body></html>"
    with patch.object(
        scrape_mod, "fetch_public_bytes", _fake_fetch_public_bytes(html.encode("utf-8"))
    ):
        first = scrape_mod.scrape_url("https://example.com/page", max_chars=40)
    _, _, next_token = parse_paged_result(first)

    with patch.object(
        scrape_mod, "fetch_public_bytes", _fake_fetch_public_bytes(html.encode("utf-8"))
    ):
        second = scrape_mod.scrape_url(
            "https://example.com/page", max_chars=40, continue_from=next_token
        )
    body_text, has_more, _ = parse_paged_result(second)
    # Second page should start at offset 40.
    assert body_text == "A" * 40
    assert has_more is True


def test_scrape_url_pagination_covers_full_text():
    """Iterating all pages reconstructs the full cleaned text."""
    from ollama_web.tools import scrape as scrape_mod

    body = "<article>" + ("A" * 100) + "</article>"
    html = f"<html><body>{body}</body></html>"
    collected = ""
    token = None
    for _ in range(10):
        with patch.object(
            scrape_mod, "fetch_public_bytes", _fake_fetch_public_bytes(html.encode("utf-8"))
        ):
            result = scrape_mod.scrape_url(
                "https://example.com/page", max_chars=40, continue_from=token
            )
        body_text, has_more, token = parse_paged_result(result)
        collected += body_text
        if not has_more:
            break
    assert collected == "A" * 100


def test_scrape_url_continue_from_invalid_token():
    """An invalid continuation token returns an error message."""
    from ollama_web.tools.scrape import scrape_url

    result = scrape_url("https://example.com/page", continue_from="not-a-valid-token")
    assert "Invalid continuation token" in result


def test_scrape_url_short_text_no_next_token():
    """Text shorter than max_chars has no next_token."""
    from ollama_web.tools import scrape as scrape_mod

    html = b"<html><body><article>short</article></body></html>"
    with patch.object(scrape_mod, "fetch_public_bytes", _fake_fetch_public_bytes(html)):
        result = scrape_mod.scrape_url("https://example.com/page", max_chars=4000)
    body_text, has_more, next_token = parse_paged_result(result)
    assert body_text == "short"
    assert has_more is False
    assert next_token is None


@pytest.mark.parametrize("content_type", ["image/png", "audio/mpeg", "video/mp4"])
def test_scrape_url_rejects_media_content(content_type):
    from ollama_web.tools import scrape as scrape_mod

    with patch.object(
        scrape_mod,
        "fetch_public_bytes",
        _fake_fetch_public_bytes(b"binary", content_type),
    ):
        result = scrape_mod.scrape_url("https://example.com/media")
    assert "Unsupported content type" in result


# ---------------------------------------------------------------------------
# pdf_to_text tests (pagination)
# ---------------------------------------------------------------------------


def _make_multipage_pdf(page_texts: list[str]) -> bytes:
    """Build a text-bearing PDF fixture without an additional dependency."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 20 760 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_to_text_only_https_supported():
    """Local paths are rejected (regression)."""
    from ollama_web.tools.pdf import pdf_to_text

    result = pdf_to_text("/local/path.pdf")
    assert "Only http(s) PDF URLs are supported." in result


def test_pdf_to_text_continue_from_invalid_token():
    """Invalid continuation token returns an error."""
    from ollama_web.tools.pdf import pdf_to_text

    result = pdf_to_text("https://example.com/doc.pdf", continue_from="bad-token")
    assert "Invalid continuation token" in result
    assert "restart_from_beginning: false" in result
    assert "required_action: reuse_latest_next_token" in result


def test_pdf_to_text_rejects_negative_page_character_cursor():
    from ollama_web.tools.pdf import pdf_to_text

    token = make_token(
        "pdf_to_text",
        "https://example.com/doc.pdf",
        0,
        page_index=0,
        page_char_offset=-1,
    )
    result = pdf_to_text("https://example.com/doc.pdf", continue_from=token)
    assert "Invalid continuation token" in result


def test_pdf_to_text_pagination_roundtrip(monkeypatch):
    """Real PDF pagination reconstructs long pages and documents over 25 pages."""
    from pypdf import PdfReader

    from ollama_web.tools import pdf as pdf_mod

    pages = [f"PAGE{i:02d}-" + ("X" * (7000 if i == 0 else 80)) for i in range(30)]
    data = _make_multipage_pdf(pages)
    expected = "\n\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages)

    # Patch fetch_public_bytes to return dummy bytes.
    monkeypatch.setattr(
        pdf_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (data, url, "application/pdf"),
    )

    collected = ""
    token = None
    for _ in range(50):
        result = pdf_mod.pdf_to_text(
            "https://example.com/doc.pdf" if token is None else "",
            max_chars=8000,
            continue_from=token,
        )
        assert len(result) <= 6000
        body_text, has_more, token = parse_paged_result(result)
        assert "next_token_is_latest:" not in body_text
        if has_more:
            assert "resume_tool: pdf_to_text" in result
            assert "next_token_is_latest: true" in result
            assert "supersedes_previous_tokens: true" in result
            assert "resume_with_continue_from_only: true" in result
            assert "preserve_next_token_on_tool_limit: true" in result
        else:
            assert "resume_tool:" not in result
        collected += body_text
        if not has_more:
            break
    assert collected == expected


def test_pdf_to_text_continuation_token_source_is_authoritative(monkeypatch):
    """A continuation restores its URL and does not require url_or_path."""
    from ollama_web.tools import pdf as pdf_mod

    data = _make_multipage_pdf(["SOURCE-" + "S" * 9000])
    fetched: list[str] = []

    def fake_fetch(url: str, timeout=None):
        fetched.append(url)
        return data, url, "application/pdf"

    monkeypatch.setattr(pdf_mod, "fetch_public_bytes", fake_fetch)
    first = pdf_mod.pdf_to_text("https://example.com/source.pdf")
    _, has_more, token = parse_paged_result(first)
    assert has_more is True
    assert token is not None

    resumed = pdf_mod.pdf_to_text(continue_from=token)
    body, _, _ = parse_paged_result(resumed)
    assert body
    assert fetched == [
        "https://example.com/source.pdf",
        "https://example.com/source.pdf",
    ]

    # Even a conflicting argument cannot redirect a valid continuation.
    pdf_mod.pdf_to_text("https://attacker.example/wrong.pdf", continue_from=token)
    assert fetched[-1] == "https://example.com/source.pdf"


def test_pdf_to_text_ollama_schema_allows_token_only_continuation():
    from ollama._utils import convert_function_to_tool

    from ollama_web.tools.pdf import pdf_to_text

    schema = convert_function_to_tool(pdf_to_text).function.parameters
    assert "url_or_path" not in (schema.required or [])
    assert "continue_from" not in (schema.required or [])


def test_session_pdf_tool_reads_original_bytes_and_is_session_scoped(tmp_path):
    from pypdf import PdfReader

    from ollama_web.sessions import SessionStore
    from ollama_web.tools.pdf import make_session_pdf_tool

    data = _make_multipage_pdf(["FIRST-" + "A" * 7000, "SECOND-" + "B" * 200])
    expected = "\n\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages)
    store = SessionStore(tmp_path)
    session = store.create()
    other_session = store.create()
    chat_file = store.add_file(
        session["id"],
        "document.pdf",
        data,
        "application/pdf",
        "legacy truncated preview",
    )
    tool = make_session_pdf_tool(lambda file_id: store.get_file_data(session["id"], file_id))

    token = None
    collected = ""
    for _ in range(10):
        result = tool(
            f"attachment:{chat_file.id}" if token is None else "",
            continue_from=token,
        )
        body, has_more, token = parse_paged_result(result)
        collected += body
        if not has_more:
            break

    assert collected == expected
    other_tool = make_session_pdf_tool(
        lambda file_id: store.get_file_data(other_session["id"], file_id)
    )
    assert "unavailable" in other_tool(f"attachment:{chat_file.id}").lower()


def test_search_and_fetch_pdf_pagination_is_lossless_and_persists_once(monkeypatch):
    from pypdf import PdfReader

    from ollama_web.tools import fetch as fetch_mod

    data = _make_multipage_pdf([f"P{i:02d}-" + "Z" * 300 for i in range(30)])
    expected = "\n\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages)
    monkeypatch.setattr(
        fetch_mod,
        "web_search",
        lambda query, max_results=None: [
            {"title": "PDF", "href": "https://example.com/document.pdf", "body": ""}
        ],
    )
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (data, url, "application/pdf"),
    )

    token = None
    collected = ""
    for _ in range(20):
        result = fetch_mod.search_and_fetch(
            "query", "pdf", max_results=1, session_id="session", continue_from=token
        )
        assert len(result) <= 6000
        body, has_more, token = parse_paged_result(result)
        collected += body.split("---\n", 1)[1].removesuffix("\n")
        if not has_more:
            break

    assert collected == expected
    assert len(fetch_mod.pop_fetched_files("session")) == 1


def test_search_and_fetch_accepts_legacy_v1_pdf_offset(monkeypatch):
    import base64

    from pypdf import PdfReader

    from ollama_web.tools import fetch as fetch_mod

    url = "https://example.com/legacy.pdf"
    data = _make_multipage_pdf(["LEGACY-" + "Q" * 500])
    expected = PdfReader(BytesIO(data)).pages[0].extract_text() or ""
    matched = [{"title": "Legacy", "href": url}]
    payload = {
        "v": 1,
        "tool": "search_and_fetch",
        "url": url,
        "offset": 100,
        "file_offset": 100,
        "file_idx": 0,
        "matched": matched,
    }
    token = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("ascii"))
        .decode("ascii")
        .rstrip("=")
    )
    monkeypatch.setattr(
        fetch_mod, "fetch_public_bytes", lambda fetched_url, timeout=None: (data, url, "")
    )
    result = fetch_mod.search_and_fetch("query", "pdf", continue_from=token)
    body, has_more, _ = parse_paged_result(result)
    assert body.split("---\n", 1)[1].removesuffix("\n") == expected[100:]
    assert has_more is False


def test_attachment_context_keeps_pdf_continuation_and_registry_can_resume(tmp_path):
    from ollama_web.routes.chat import _attachment_context, _build_registry
    from ollama_web.sessions import SessionStore

    data = _make_multipage_pdf(["ATTACHED-" + "R" * 8000])
    store = SessionStore(tmp_path)
    session = store.create()
    chat_file = store.add_file(
        session["id"], "attached.pdf", data, "application/pdf", "old preview"
    )
    context = _attachment_context(store, session["id"], [chat_file.id], total_limit=5000)
    assert len(context) <= 5000
    assert f'uri="attachment:{chat_file.id}"' in context
    assert "next_token:" in context

    registry = asyncio.run(_build_registry(session["id"], store))
    first_result = context.split("<attached-file", 1)[1]
    token = first_result.split("next_token:", 1)[1].splitlines()[0].strip()
    resumed = registry.execute(
        "pdf_to_text",
        {"continue_from": token},
    )
    assert "unavailable" not in resumed.lower()


def test_uploaded_pdf_can_be_resumed_from_original_file(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from ollama_web.app import create_app
    from ollama_web.config import settings
    from ollama_web.routes.chat import _build_registry

    data = _make_multipage_pdf(["UPLOAD-" + "U" * 8000])
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "pin", "123456")
    app = create_app()
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"pin": "123456"})
    csrf = login.headers["x-csrf-token"]
    session_id = client.post("/api/sessions", headers={"X-CSRF-Token": csrf}).json()["id"]
    uploaded = client.post(
        f"/api/sessions/{session_id}/files",
        headers={"X-CSRF-Token": csrf},
        files={"files": ("upload.pdf", data, "application/pdf")},
    ).json()["files"][0]
    assert len(uploaded["text"]) <= settings.max_tool_result_chars

    registry = asyncio.run(_build_registry(session_id, app.state.session_store))
    first = registry.execute("pdf_to_text", {"url_or_path": f"attachment:{uploaded['id']}"})
    _, has_more, token = parse_paged_result(first)
    assert has_more is True
    resumed = registry.execute(
        "pdf_to_text",
        {"url_or_path": f"attachment:{uploaded['id']}", "continue_from": token},
    )
    assert "unavailable" not in resumed.lower()


# ---------------------------------------------------------------------------
# search_and_fetch tests (pagination)
# ---------------------------------------------------------------------------


def test_search_and_fetch_no_results(monkeypatch):
    """No matching candidates returns the no_results prompt (regression)."""
    from ollama_web.tools import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: [])
    result = fetch_mod.search_and_fetch("query", "pdf")
    assert "No pdf files found" in result


def test_search_and_fetch_rejects_unsupported_file_type(monkeypatch):
    from ollama_web.tools import fetch as fetch_mod

    search = MagicMock(return_value=[])
    monkeypatch.setattr(fetch_mod, "web_search", search)
    result = fetch_mod.search_and_fetch("query", "png")
    assert result == "Invalid file type."
    search.assert_not_called()


def test_search_and_fetch_rejects_media_response(monkeypatch):
    from ollama_web.tools import fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod,
        "web_search",
        lambda query, max_results=None: [
            {"title": "Fake text", "href": "https://example.com/file.txt", "body": ""}
        ],
    )
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"binary", url, "image/png"),
    )
    result = fetch_mod.search_and_fetch("query", "txt")
    assert "unsupported media content type" in result


def test_search_and_fetch_continue_from_invalid_token():
    """Invalid continuation token returns an error."""
    from ollama_web.tools.fetch import search_and_fetch

    result = search_and_fetch("query", "pdf", continue_from="bad-token")
    assert "Invalid continuation token" in result


def test_search_and_fetch_v2_token_is_bound_to_file_type(monkeypatch):
    from ollama_web.tools import fetch as fetch_mod

    monkeypatch.setattr(
        fetch_mod,
        "web_search",
        lambda query, max_results=None: [
            {"title": "Text", "href": "https://example.com/file.txt", "body": ""}
        ],
    )
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"A" * 200, url, "text/plain"),
    )
    first = fetch_mod.search_and_fetch("query", "txt", max_chars=50)
    _, _, token = parse_paged_result(first)
    result = fetch_mod.search_and_fetch("query", "pdf", continue_from=token)
    assert "file type does not match" in result


def test_search_and_fetch_pagination_across_files(monkeypatch):
    """Pagination walks through multiple candidate files in order."""
    from ollama_web.tools import fetch as fetch_mod

    matched = [
        {"title": "File1", "href": "https://example.com/f1.txt", "body": ""},
        {"title": "File2", "href": "https://example.com/f2.txt", "body": ""},
    ]
    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: matched)
    # fetch_public_bytes returns distinct content per URL.
    contents = {
        "https://example.com/f1.txt": b"FILE1 " + b"A" * 200,
        "https://example.com/f2.txt": b"FILE2 " + b"B" * 200,
    }
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (contents[url], "", "text/plain"),
    )

    seen_titles = []
    token = None
    for _ in range(20):
        result = fetch_mod.search_and_fetch("query", "txt", max_chars=50, continue_from=token)
        body_text, has_more, token = parse_paged_result(result)
        # Record the first time each file title appears, preserving order.
        if "File1" in body_text and "File1" not in seen_titles:
            seen_titles.append("File1")
        if "File2" in body_text and "File2" not in seen_titles:
            seen_titles.append("File2")
        if not has_more:
            break
    # Both files must be visited, in order.
    assert seen_titles == ["File1", "File2"]


def test_search_and_fetch_long_html_uses_full_cleaned_source(monkeypatch):
    from ollama_web.tools import fetch as fetch_mod

    source = "".join(f"paragraph-{index:04d} " for index in range(1200))
    html = f"<html><body><main>{source}</main></body></html>".encode()
    monkeypatch.setattr(
        fetch_mod,
        "web_search",
        lambda query, max_results=None: [
            {"title": "Long HTML", "href": "https://example.com/page.html", "body": ""}
        ],
    )
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (html, url, "text/html; charset=utf-8"),
    )

    token = None
    collected = ""
    for _ in range(20):
        result = fetch_mod.search_and_fetch("query", "html", max_results=1, continue_from=token)
        body, has_more, token = parse_paged_result(result)
        collected += body.split("---\n", 1)[1].removesuffix("\n")
        if not has_more:
            break
    assert collected == source.strip()


def test_make_token_ensure_ascii_true():
    """Tokens are pure ASCII even with non-ASCII content (ensure_ascii=True)."""
    token = make_token("scrape_url", "https://example.com/日本語", 0, title="論文–要約")
    # base64url alphabet is pure ASCII; any non-ASCII byte would break this.
    assert all(ord(c) < 128 for c in token)
    payload = parse_token(token, "scrape_url")
    assert payload["url"] == "https://example.com/日本語"
    assert payload["title"] == "論文–要約"


def test_search_and_fetch_token_excludes_body(monkeypatch):
    """The continuation token must not embed the search snippet (body)."""
    from ollama_web.tools import fetch as fetch_mod

    matched = [
        {
            "title": "File1",
            "href": "https://example.com/f1.txt",
            "body": "X" * 5000,  # large snippet that would bloat the token
        }
    ]
    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: matched)
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"A" * 200, "", "text/plain"),
    )
    result = fetch_mod.search_and_fetch("query", "txt", max_chars=50)
    _, _, next_token = parse_paged_result(result)
    assert next_token is not None
    payload = parse_token(next_token, "search_and_fetch")
    embedded = payload.get("matched", [])
    assert embedded, "matched list should be embedded for resume"
    for entry in embedded:
        assert "body" not in entry, "body must be stripped from the token"
        assert "title" in entry and "href" in entry


def test_search_and_fetch_token_size_reduced(monkeypatch):
    """A large body snippet must not inflate the token."""
    from ollama_web.tools import fetch as fetch_mod

    matched = [
        {
            "title": "File1",
            "href": "https://example.com/f1.txt",
            "body": "B" * 10_000,
        }
    ]
    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: matched)
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"A" * 200, "", "text/plain"),
    )
    result = fetch_mod.search_and_fetch("query", "txt", max_chars=50)
    _, _, next_token = parse_paged_result(result)
    assert next_token is not None
    # Token should be far smaller than the 10KB body it would otherwise carry.
    assert len(next_token) < 1000


def test_search_and_fetch_continue_with_slim_token(monkeypatch):
    """Resuming with a slim (body-less) token walks files correctly."""
    from ollama_web.tools import fetch as fetch_mod

    matched = [
        {"title": "File1", "href": "https://example.com/f1.txt", "body": "ignored1"},
        {"title": "File2", "href": "https://example.com/f2.txt", "body": "ignored2"},
    ]
    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: matched)
    contents = {
        "https://example.com/f1.txt": b"FILE1 " + b"A" * 200,
        "https://example.com/f2.txt": b"FILE2 " + b"B" * 200,
    }
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (contents[url], "", "text/plain"),
    )

    seen = []
    token = None
    for _ in range(20):
        result = fetch_mod.search_and_fetch("query", "txt", max_chars=50, continue_from=token)
        body_text, has_more, token = parse_paged_result(result)
        if "File1" in body_text and "File1" not in seen:
            seen.append("File1")
        if "File2" in body_text and "File2" not in seen:
            seen.append("File2")
        if not has_more:
            break
    assert seen == ["File1", "File2"]


def test_search_and_fetch_pagination_with_nonascii_title(monkeypatch):
    """Non-ASCII titles/URLs survive a full pagination round-trip."""
    from ollama_web.tools import fetch as fetch_mod

    matched = [
        {"title": "日本語–タイトル", "href": "https://example.com/論文.txt", "body": ""},
    ]
    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: matched)
    monkeypatch.setattr(
        fetch_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"A" * 200, "", "text/plain"),
    )
    result = fetch_mod.search_and_fetch("query", "txt", max_chars=50)
    body_text, has_more, next_token = parse_paged_result(result)
    assert "日本語–タイトル" in body_text
    assert has_more is True
    # Resuming must not raise even though the token carries non-ASCII fields.
    result2 = fetch_mod.search_and_fetch("query", "txt", max_chars=50, continue_from=next_token)
    assert isinstance(result2, str)


# ---------------------------------------------------------------------------
# pagination helper tests
# ---------------------------------------------------------------------------


def test_make_token_and_parse_token_roundtrip():
    token = make_token("scrape_url", "https://example.com/page", 4000)
    payload = parse_token(token, "scrape_url")
    assert payload["url"] == "https://example.com/page"
    assert payload["offset"] == 4000
    assert payload["tool"] == "scrape_url"
    assert payload["v"] == 2


def test_parse_token_accepts_legacy_v1_payload():
    import base64

    raw = json.dumps(
        {"v": 1, "tool": "pdf_to_text", "url": "https://example.com/a.pdf", "offset": 3}
    ).encode("ascii")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    payload = parse_token(token, "pdf_to_text")
    assert payload["v"] == 1
    assert payload["offset"] == 3


def test_parse_token_rejects_negative_and_oversized_tokens():
    import base64

    raw = json.dumps(
        {"v": 1, "tool": "scrape_url", "url": "https://example.com", "offset": -1}
    ).encode("ascii")
    negative = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    with pytest.raises(InvalidTokenError):
        parse_token(negative, "scrape_url")
    with pytest.raises(InvalidTokenError):
        parse_token("A" * 9000, "scrape_url")


def test_parse_token_invalid_base64():
    with pytest.raises(InvalidTokenError):
        parse_token("!!!not-base64!!!", "scrape_url")


def test_parse_token_invalid_json():
    import base64

    token = base64.urlsafe_b64encode(b"{not json").decode("ascii").rstrip("=")
    with pytest.raises(InvalidTokenError):
        parse_token(token, "scrape_url")


def test_parse_token_wrong_tool():
    token = make_token("scrape_url", "https://example.com/page", 0)
    with pytest.raises(InvalidTokenError):
        parse_token(token, "pdf_to_text")


def test_parse_token_missing_fields():
    import base64

    payload = json.dumps({"v": 1, "tool": "scrape_url"}).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    with pytest.raises(InvalidTokenError):
        parse_token(token, "scrape_url")


def test_parse_token_version_mismatch():
    import base64

    payload = json.dumps({"v": 999, "tool": "scrape_url", "url": "https://x", "offset": 0}).encode(
        "utf-8"
    )
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    with pytest.raises(InvalidTokenError):
        parse_token(token, "scrape_url")


def test_parse_token_empty_or_none():
    with pytest.raises(InvalidTokenError):
        parse_token("", "scrape_url")
    with pytest.raises(InvalidTokenError):
        parse_token(None, "scrape_url")


# ---------------------------------------------------------------------------
# format_paged_result tests
# ---------------------------------------------------------------------------


def test_format_paged_result_has_more_true():
    result = format_paged_result("body", has_more=True, next_token="tok")
    assert "---page-info---" in result
    assert "has_more: true" in result
    assert "next_token: tok" in result


def test_format_paged_result_has_more_false():
    result = format_paged_result("body", has_more=False, next_token=None)
    assert "---page-info---" not in result
    assert "next_token" not in result


def test_format_paged_result_marker_at_end():
    result = format_paged_result("body", has_more=True, next_token="tok")
    # Marker must come after the body.
    assert result.index("body") < result.index("---page-info---")


# ---------------------------------------------------------------------------
# _trim_tool_result page-info protection tests
# ---------------------------------------------------------------------------


def test_trim_tool_result_rejects_oversized_paged_result_atomically():
    from ollama_web.llm import _trim_tool_result

    body = "A" * 5000
    footer = "\n---page-info---\nhas_more: true\nnext_token: tok123"
    result = body + footer
    trimmed = _trim_tool_result(result, 200)
    assert "Repeat the same tool call" in trimmed
    assert "tok123" not in trimmed
    assert "A" * 20 not in trimmed


def test_trim_tool_result_no_page_info_unchanged():
    """Without a footer, legacy truncation applies (regression)."""
    from ollama_web.llm import _trim_tool_result

    result = "A" * 5000
    trimmed = _trim_tool_result(result, 200)
    assert "---page-info---" not in trimmed
    assert "…[tool result truncated]" in trimmed


def test_trim_tool_result_page_info_fits_limit():
    """When the whole result fits the limit, nothing is truncated."""
    from ollama_web.llm import _trim_tool_result

    result = "short body\n---page-info---\nhas_more: true\nnext_token: tok"
    trimmed = _trim_tool_result(result, 200)
    assert trimmed == result
