"""Tests for tool registry and built-in tools."""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# pdf_to_text tests (pagination)
# ---------------------------------------------------------------------------


def _make_multipage_pdf(num_pages: int, text_per_page: str = "Page content. ") -> bytes:
    """Build an in-memory multi-page PDF using pypdf's writer."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    # pypdf cannot easily inject text into blank pages; we rely on
    # extract_text returning empty for blank pages. To get real text we use
    # a reportlab-free approach: attach a content stream. For test purposes
    # we instead patch extract_pdf_text to return deterministic text.
    import io

    buf = io.BytesIO()
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


def test_pdf_to_text_pagination_roundtrip(monkeypatch):
    """Pagination across pages reconstructs all page text."""
    from ollama_web.tools import pdf as pdf_mod
    from ollama_web.tools.helper import pdf as pdf_helper

    # Patch extract_pdf_text to return deterministic pages.
    pages = [f"PAGE{i} " + ("X" * 50) for i in range(6)]

    def fake_extract(data, max_chars=None, page_offset=0):
        limit = max_chars if max_chars and max_chars > 0 else 10_000
        collected = ""
        last = page_offset
        for idx in range(page_offset, len(pages)):
            collected += pages[idx]
            last = idx
            if len(collected) >= limit:
                break
        has_more = (last + 1) < len(pages)
        return collected, has_more, last + 1 if has_more else 0

    monkeypatch.setattr(pdf_helper, "extract_pdf_text", fake_extract)
    monkeypatch.setattr(pdf_mod, "extract_pdf_text", fake_extract)

    # Patch fetch_public_bytes to return dummy bytes.
    monkeypatch.setattr(
        pdf_mod,
        "fetch_public_bytes",
        lambda url, timeout=None: (b"%PDF-1.4", "", "application/pdf"),
    )

    collected = ""
    token = None
    for _ in range(10):
        result = pdf_mod.pdf_to_text(
            "https://example.com/doc.pdf", max_chars=100, continue_from=token
        )
        body_text, has_more, token = parse_paged_result(result)
        collected += body_text
        if not has_more:
            break
    assert collected == "".join(pages)


# ---------------------------------------------------------------------------
# search_and_fetch tests (pagination)
# ---------------------------------------------------------------------------


def test_search_and_fetch_no_results(monkeypatch):
    """No matching candidates returns the no_results prompt (regression)."""
    from ollama_web.tools import fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "web_search", lambda q, max_results=None: [])
    result = fetch_mod.search_and_fetch("query", "pdf")
    assert "No pdf files found" in result


def test_search_and_fetch_continue_from_invalid_token():
    """Invalid continuation token returns an error."""
    from ollama_web.tools.fetch import search_and_fetch

    result = search_and_fetch("query", "pdf", continue_from="bad-token")
    assert "Invalid continuation token" in result


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


# ---------------------------------------------------------------------------
# pagination helper tests
# ---------------------------------------------------------------------------


def test_make_token_and_parse_token_roundtrip():
    token = make_token("scrape_url", "https://example.com/page", 4000)
    payload = parse_token(token, "scrape_url")
    assert payload["url"] == "https://example.com/page"
    assert payload["offset"] == 4000
    assert payload["tool"] == "scrape_url"
    assert payload["v"] == 1


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


def test_trim_tool_result_preserves_page_info():
    from ollama_web.llm import _trim_tool_result

    body = "A" * 5000
    footer = "\n---page-info---\nhas_more: true\nnext_token: tok123"
    result = body + footer
    trimmed = _trim_tool_result(result, 200)
    assert "---page-info---" in trimmed
    assert "next_token: tok123" in trimmed
    assert "has_more: true" in trimmed
    # Body should be truncated.
    assert len(trimmed) < len(result)


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
