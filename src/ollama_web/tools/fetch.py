"""Search-and-fetch tool that finds files on the web by type and returns text."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from ..config import settings
from ..prompts import get_prompt, get_prompts
from .helper.pagination import format_paged_result, make_token, parse_token
from .helper.pdf import extract_pdf_text
from .helper.safe_http import fetch_public_bytes
from .scrape import scrape_url
from .search import web_search

_FetchedFile = dict[str, Any]

# Per-session temporary storage for files fetched by search_and_fetch.
# The chat route drains this after the tool call finishes.
_fetched_files: dict[str, list[_FetchedFile]] = {}


_FILETYPE_SUFFIXES: dict[str, str] = {
    "pdf": "pdf",
    "txt": "txt",
    "csv": "csv",
    "md": "md",
    "json": "json",
    "html": "html",
    "htm": "htm",
    "xml": "xml",
    "py": "py",
    "js": "js",
    "ts": "ts",
}

_TOOL_NAME = "search_and_fetch"


def _slim_matched(matched: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return a token-embeddable view of ``matched`` without the ``body`` field.

    ``search_and_fetch`` embeds the candidate list in its continuation token
    so subsequent pages can resume without re-running the web search. The
    rendering layer only uses ``title`` and ``href``; the search-engine
    snippet (``body``) is unused and can be large (multi-KB for PDF
    abstracts). Including it inflates the token to the point where the
    model or tool-result trimming may truncate it, corrupting the base64
    payload. Stripping ``body`` keeps the token to a few hundred bytes.
    """
    return [{"title": m.get("title", ""), "href": m.get("href", "")} for m in matched]


def set_session_context(session_id: str | None) -> None:
    """Set the active session id so fetched files can be persisted later."""
    # No-op marker kept for future hooks; storage is keyed by session_id directly.
    pass


def store_fetched_file(
    session_id: str,
    name: str,
    data: bytes,
    mime: str,
    text: str,
    url: str,
) -> None:
    """Store a fetched file so the chat route can persist it to the session."""
    if session_id:
        _fetched_files.setdefault(session_id, []).append(
            {
                "name": name,
                "data": data,
                "mime": mime,
                "text": text,
                "url": url,
            }
        )


def pop_fetched_files(session_id: str) -> list[_FetchedFile]:
    """Return and clear any files fetched for the given session."""
    return _fetched_files.pop(session_id, [])


def _normalize_file_type(file_type: str) -> str:
    ft = file_type.strip().lower().lstrip(".")
    return _FILETYPE_SUFFIXES.get(ft, ft)


def _filetype_query(query: str, file_type: str) -> str:
    return f"{query} filetype:{file_type}"


def _is_matching_url(url: str, file_type: str) -> bool:
    suffix = f".{file_type}"
    import httpx

    parsed = httpx.URL(url)
    path = parsed.path or ""
    if path.lower().endswith(suffix):
        return True
    # Some CDNs embed extension after query params.
    if re.search(rf"\.{re.escape(file_type)}(?:[?#]|$)", url, re.IGNORECASE):
        return True
    return False


def _guess_mime(name: str, file_type: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or f"application/{file_type}"


def _fetch_bytes(url: str) -> bytes:
    data, _, _ = fetch_public_bytes(url, timeout=settings.scrape_timeout)
    return data


def _text_from_bytes(url: str, data: bytes, file_type: str, max_chars: int) -> str:
    import httpx

    name = Path(httpx.URL(url).path or "file").name or f"file.{file_type}"
    if not name or "." not in name:
        name = f"file.{file_type}"

    if file_type == "pdf":
        text, _, _ = extract_pdf_text(data, max_chars=max_chars, page_offset=0)
        return text

    # Decode text-like formats.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    if file_type in {"html", "htm"}:
        text = scrape_url(url, max_chars=max_chars)

    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "\n…[truncated]"
    return text


def _full_text_for_file(url: str, data: bytes, file_type: str) -> str:
    """Return the *full* extracted text for a file (no truncation).

    Used by the pagination path so we can slice arbitrary offsets. For PDFs we
    extract all pages; for text-like formats we decode the full payload.
    """
    if file_type == "pdf":
        # Extract with a very large char budget to get the full text.
        text, _, _ = extract_pdf_text(data, max_chars=10_000_000, page_offset=0)
        return text

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    if file_type in {"html", "htm"}:
        # Reuse scrape_url's cleaning but request a large budget to get the
        # full cleaned text. scrape_url itself now supports pagination, but
        # here we want the full body for manual slicing.
        text = scrape_url(url, max_chars=10_000_000)
    return text


def search_and_fetch(
    query: str,
    file_type: str,
    max_results: int | None = None,
    max_chars: int | None = None,
    session_id: str | None = None,
    continue_from: str | None = None,
) -> str:
    """Search the web for files of a given type and return their text content.

    Results are returned one file at a time. When a file is truncated or more
    candidate files remain, a ``next_token`` is appended that can be passed
    back via ``continue_from`` to resume.

    Args:
      query: Search keywords.
      file_type: Desired file extension (e.g. "pdf", "csv", "txt", "md", "json").
      max_results: Number of candidate URLs to fetch.
      max_chars: Max characters per file to return.
      session_id: If provided, fetched files are queued for session persistence.
      continue_from: Opaque continuation token returned in a previous call's
        ``next_token``. When provided, resumes from the encoded file index and
        in-file offset without re-running the search.

    Returns:
      A formatted string containing metadata and extracted text for the
      current file page, followed by a ``---page-info---`` footer when more
      content is available.
    """
    if not query or not query.strip():
        return get_prompt("fetch.empty_query")

    ft = _normalize_file_type(file_type)
    if not ft:
        return get_prompt("fetch.invalid_type")

    limit = min(
        max_results if max_results is not None and max_results > 0 else settings.fetch_max_results,
        settings.fetch_max_results,
    )
    per_file_chars = (
        max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars
    )

    # --- Pagination resume path ---
    if continue_from:
        try:
            payload = parse_token(continue_from, _TOOL_NAME)
        except ValueError as exc:
            return f"Invalid continuation token: {exc}"
        resumed_matched = payload.get("matched", [])
        if not isinstance(resumed_matched, list) or not resumed_matched:
            return (
                get_prompt("fetch.no_results", fallback="")
                .replace("{file_type}", ft)
                .replace("{query}", query)
            )
        file_idx = int(payload.get("file_idx", 0))
        file_offset = int(payload.get("offset", 0))
        return _render_file_page(
            matched=resumed_matched,
            file_idx=file_idx,
            file_offset=file_offset,
            ft=ft,
            per_file_chars=per_file_chars,
            session_id=session_id,
            query=query,
        )

    # --- Initial search path ---
    search_query = _filetype_query(query, ft)
    results = web_search(search_query, max_results=limit * 2)

    matched: list[dict[str, str]] = []
    for item in results:
        href = item.get("href", "")
        if _is_matching_url(href, ft):
            matched.append(item)
            if len(matched) >= limit:
                break

    if not matched:
        return (
            get_prompt("fetch.no_results", fallback="")
            .replace("{file_type}", ft)
            .replace("{query}", query)
        )

    return _render_file_page(
        matched=matched,
        file_idx=0,
        file_offset=0,
        ft=ft,
        per_file_chars=per_file_chars,
        session_id=session_id,
        query=query,
    )


def _render_file_page(
    *,
    matched: list[dict[str, str]],
    file_idx: int,
    file_offset: int,
    ft: str,
    per_file_chars: int,
    session_id: str | None,
    query: str,
) -> str:
    """Render a single file page and emit a continuation token if needed.

    The token encodes the candidate list so subsequent pages do not need to
    re-run the web search.
    """
    if file_idx >= len(matched):
        # All candidates exhausted.
        return format_paged_result("", has_more=False, next_token=None)

    item = matched[file_idx]
    url = item.get("href", "")
    title = item.get("title", "")
    p = get_prompts().get("fetch", {})

    try:
        data = _fetch_bytes(url)
    except Exception as exc:  # noqa: BLE001
        # On fetch failure, advance to the next candidate rather than aborting
        # the whole pagination chain.
        body = (
            f"=== {p['candidate']} {file_idx + 1} ===\n"
            f"URL: {url}\n{p['title']}: {title}\n{p['status']}: {p['fetch_failed']} ({exc})\n"
        )
        next_token = make_token(
            _TOOL_NAME,
            url,
            0,
            matched=_slim_matched(matched),
            file_idx=file_idx + 1,
            file_offset=0,
        )
        return format_paged_result(body, has_more=True, next_token=next_token)

    full_text = _full_text_for_file(url, data, ft)
    page_text = full_text[file_offset : file_offset + per_file_chars]
    has_more_in_file = len(full_text) > file_offset + per_file_chars

    import httpx

    mime = _guess_mime(Path(httpx.URL(url).path or f"file.{ft}").name, ft)
    name = Path(httpx.URL(url).path or f"file.{ft}").name
    if session_id:
        store_fetched_file(session_id, name, data, mime, page_text, url)

    body = (
        f"=== {p['candidate']} {file_idx + 1} ===\n"
        f"URL: {url}\n"
        f"{p['title']}: {title}\n"
        f"{p['type']}: {mime}\n"
        f"{p['size']}: {len(data)} bytes\n"
        f"{p['chars']}: {len(page_text)}\n"
        f"{p['separator']}\n{page_text}\n"
    )

    if has_more_in_file:
        # Continue within the same file.
        next_token = make_token(
            _TOOL_NAME,
            url,
            file_offset + per_file_chars,
            matched=_slim_matched(matched),
            file_idx=file_idx,
            file_offset=file_offset + per_file_chars,
        )
        return format_paged_result(body, has_more=True, next_token=next_token)

    # Move to the next candidate file.
    if file_idx + 1 < len(matched):
        next_token = make_token(
            _TOOL_NAME,
            url,
            0,
            matched=_slim_matched(matched),
            file_idx=file_idx + 1,
            file_offset=0,
        )
        return format_paged_result(body, has_more=True, next_token=next_token)

    return format_paged_result(body, has_more=False, next_token=None)