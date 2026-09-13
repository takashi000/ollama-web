"""Search-and-fetch tool that finds files on the web by type and returns text."""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from ..config import settings
from ..prompts import get_prompt, get_prompts
from .helper.pagination import (
    PageResult,
    PageSizeError,
    format_page_to_limit,
    make_token,
    parse_token,
    text_page,
)
from .helper.pdf import extract_pdf_page, legacy_pdf_offset_to_cursor
from .helper.safe_http import fetch_public_bytes
from .scrape import extract_readable_text
from .search import web_search

_FetchedFile = dict[str, Any]

# Per-session temporary storage for files fetched by search_and_fetch.
# The chat route drains this after the tool call finishes.
_fetched_files: dict[str, dict[str, _FetchedFile]] = {}


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
logger = logging.getLogger("ollama_web.tools.fetch")


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
        _fetched_files.setdefault(session_id, {}).setdefault(
            url,
            {
                "name": name,
                "data": data,
                "mime": mime,
                "text": text,
                "url": url,
            },
        )


def pop_fetched_files(session_id: str) -> list[_FetchedFile]:
    """Return and clear any files fetched for the given session."""
    return list(_fetched_files.pop(session_id, {}).values())


def _normalize_file_type(file_type: str) -> str:
    ft = file_type.strip().lower().lstrip(".")
    return _FILETYPE_SUFFIXES.get(ft, "")


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


def _fetch_bytes(url: str) -> tuple[bytes, str]:
    data, _, content_type = fetch_public_bytes(url, timeout=settings.scrape_timeout)
    return data, content_type


def _full_text_for_file(url: str, data: bytes, file_type: str) -> str:
    """Decode a complete text-like file for character-offset pagination."""
    if file_type == "pdf":
        raise ValueError("PDF files must use page-and-character cursor pagination.")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    if file_type in {"html", "htm"}:
        text = extract_readable_text(data)
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
        raw_matched = payload.get("matched", [])
        if not isinstance(raw_matched, list) or not raw_matched:
            return (
                get_prompt("fetch.no_results", fallback="")
                .replace("{file_type}", ft)
                .replace("{query}", query)
            )
        resumed_matched = [
            {"title": str(item.get("title", "")), "href": str(item.get("href", ""))}
            for item in raw_matched
            if isinstance(item, dict)
        ]
        if payload.get("v") == 2 and payload.get("file_type") != ft:
            return "Invalid continuation token: file type does not match."
        try:
            file_idx = int(payload.get("file_idx", 0))
            file_offset = int(payload.get("file_offset", payload.get("offset", 0)))
            pdf_page_index = int(payload.get("pdf_page_index", 0))
            pdf_page_char_offset = int(payload.get("pdf_page_char_offset", 0))
        except (TypeError, ValueError) as exc:
            return f"Invalid continuation token: cursor is not an integer: {exc}"
        legacy_pdf_offset = (
            file_offset if payload.get("v") == 1 and "pdf_page_index" not in payload else None
        )
        if min(file_idx, file_offset, pdf_page_index, pdf_page_char_offset) < 0:
            return "Invalid continuation token: cursor values must not be negative."
        if not resumed_matched or file_idx >= len(resumed_matched):
            return "Invalid continuation token: file index is out of range."
        return _render_file_page(
            matched=resumed_matched,
            file_idx=file_idx,
            file_offset=file_offset,
            pdf_page_index=pdf_page_index,
            pdf_page_char_offset=pdf_page_char_offset,
            legacy_pdf_offset=legacy_pdf_offset,
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
        pdf_page_index=0,
        pdf_page_char_offset=0,
        legacy_pdf_offset=None,
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
    pdf_page_index: int,
    pdf_page_char_offset: int,
    legacy_pdf_offset: int | None,
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
        return ""

    item = matched[file_idx]
    url = item.get("href", "")
    title = item.get("title", "")
    p = get_prompts().get("fetch", {})

    try:
        data, content_type = _fetch_bytes(url)
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type.startswith(("image/", "audio/", "video/")):
            raise ValueError(f"unsupported media content type: {media_type}")
    except Exception as exc:  # noqa: BLE001
        # On fetch failure, advance to the next candidate rather than aborting
        # the whole pagination chain.
        body = (
            f"=== {p['candidate']} {file_idx + 1} ===\n"
            f"URL: {url}\n{p['title']}: {title}\n{p['status']}: {p['fetch_failed']} ({exc})\n"
        )
        has_more = file_idx + 1 < len(matched)
        cursor = {
            "file_idx": file_idx + 1,
            "file_offset": 0,
            "pdf_page_index": 0,
            "pdf_page_char_offset": 0,
        }
        page = PageResult(body, cursor, cursor, has_more)
        return format_page_to_limit(
            lambda _budget: page,
            lambda next_cursor: _make_fetch_token(matched, next_cursor, ft),
            requested_chars=per_file_chars,
            result_limit=settings.max_tool_result_chars,
        )

    import httpx

    mime = _guess_mime(Path(httpx.URL(url).path or f"file.{ft}").name, ft)
    name = Path(httpx.URL(url).path or f"file.{ft}").name
    prefix = (
        f"=== {p['candidate']} {file_idx + 1} ===\n"
        f"URL: {url}\n"
        f"{p['title']}: {title}\n"
        f"{p['type']}: {mime}\n"
        f"{p['size']}: {len(data)} bytes\n"
        f"{p['separator']}\n"
    )

    if ft == "pdf":
        if legacy_pdf_offset is not None:
            try:
                pdf_page_index, pdf_page_char_offset = legacy_pdf_offset_to_cursor(
                    data, legacy_pdf_offset
                )
            except Exception as exc:  # noqa: BLE001
                return f"Invalid continuation token: {exc}"
        preview = extract_pdf_page(data, max_chars=min(per_file_chars, 1000)).text

        def page_factory(body_budget: int) -> PageResult:
            source_page = extract_pdf_page(
                data,
                max_chars=body_budget,
                page_index=pdf_page_index,
                page_char_offset=pdf_page_char_offset,
            )
            if source_page.has_more:
                next_cursor = {
                    "file_idx": file_idx,
                    "file_offset": 0,
                    "pdf_page_index": source_page.next_cursor["page_index"],
                    "pdf_page_char_offset": source_page.next_cursor["page_char_offset"],
                }
                has_more = True
            else:
                next_cursor = {
                    "file_idx": file_idx + 1,
                    "file_offset": 0,
                    "pdf_page_index": 0,
                    "pdf_page_char_offset": 0,
                }
                has_more = file_idx + 1 < len(matched)
            return PageResult(
                source_page.text,
                {
                    "file_idx": file_idx,
                    "file_offset": 0,
                    "pdf_page_index": pdf_page_index,
                    "pdf_page_char_offset": pdf_page_char_offset,
                },
                next_cursor,
                has_more,
                source_page.warnings,
            )

    else:
        full_text = _full_text_for_file(url, data, ft)
        preview = full_text[: min(per_file_chars, 1000)]

        def page_factory(body_budget: int) -> PageResult:
            source_page = text_page(full_text, file_offset, body_budget)
            if source_page.has_more:
                next_cursor = {
                    "file_idx": file_idx,
                    "file_offset": source_page.next_cursor["offset"],
                    "pdf_page_index": 0,
                    "pdf_page_char_offset": 0,
                }
                has_more = True
            else:
                next_cursor = {
                    "file_idx": file_idx + 1,
                    "file_offset": 0,
                    "pdf_page_index": 0,
                    "pdf_page_char_offset": 0,
                }
                has_more = file_idx + 1 < len(matched)
            return PageResult(
                source_page.text,
                {
                    "file_idx": file_idx,
                    "file_offset": file_offset,
                    "pdf_page_index": 0,
                    "pdf_page_char_offset": 0,
                },
                next_cursor,
                has_more,
            )

    if session_id:
        store_fetched_file(session_id, name, data, mime, preview, url)

    try:
        result = format_page_to_limit(
            page_factory,
            lambda cursor: _make_fetch_token(matched, cursor, ft),
            requested_chars=per_file_chars,
            result_limit=settings.max_tool_result_chars,
            prefix=prefix,
            suffix="\n",
        )
        logger.info(
            (
                "fetch page type=%s file_idx=%d file_offset=%d pdf_page_index=%d "
                "pdf_page_char_offset=%d result_chars=%d has_more=%s"
            ),
            ft,
            file_idx,
            file_offset,
            pdf_page_index,
            pdf_page_char_offset,
            len(result),
            "---page-info---" in result,
        )
        return result
    except (PageSizeError, ValueError) as exc:
        return f"File pagination failed: {exc}"


def _make_fetch_token(matched: list[dict[str, str]], cursor: dict[str, int], file_type: str) -> str:
    file_idx = cursor["file_idx"]
    url = matched[file_idx].get("href", "") if file_idx < len(matched) else "completed"
    return make_token(
        _TOOL_NAME,
        url,
        cursor["file_offset"],
        matched=_slim_matched(matched),
        file_idx=file_idx,
        file_offset=cursor["file_offset"],
        pdf_page_index=cursor["pdf_page_index"],
        pdf_page_char_offset=cursor["pdf_page_char_offset"],
        file_type=file_type,
    )
