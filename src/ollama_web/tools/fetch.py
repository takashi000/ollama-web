"""Search-and-fetch tool that finds files on the web by type and returns text."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .pdf import extract_pdf_text
from .scrape import scrape_url


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


def set_session_context(session_id: str | None) -> None:
    """Set the active session id so fetched files can be persisted later."""
    # No-op marker kept for future hooks; storage is keyed by session_id directly.
    pass


def store_fetched_file(session_id: str, name: str, data: bytes, mime: str, text: str, url: str) -> None:
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
    with httpx.Client(
        timeout=settings.scrape_timeout,
        follow_redirects=True,
        headers={"User-Agent": "ollama-web/0.1 (+https://github.com/local)"},
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _text_from_bytes(url: str, data: bytes, file_type: str, max_chars: int) -> str:
    name = Path(httpx.URL(url).path or "file").name or f"file.{file_type}"
    if not name or "." not in name:
        name = f"file.{file_type}"

    if file_type == "pdf":
        return extract_pdf_text(data, max_chars=max_chars)

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


def search_and_fetch(
    query: str,
    file_type: str,
    max_results: int | None = None,
    max_chars: int | None = None,
    session_id: str | None = None,
) -> str:
    """Search the web for files of a given type and return their text content.

    Args:
      query: Search keywords.
      file_type: Desired file extension (e.g. "pdf", "csv", "txt", "md", "json").
      max_results: Number of candidate URLs to fetch.
      max_chars: Max characters per file to return.
      session_id: If provided, fetched files are queued for session persistence.

    Returns:
      A formatted string containing metadata and extracted text for each
      successfully fetched file.
    """
    if not query or not query.strip():
        return "Empty query."

    ft = _normalize_file_type(file_type)
    if not ft:
        return "Invalid file type."

    limit = (
        max_results
        if max_results is not None and max_results > 0
        else settings.fetch_max_results
    )
    per_file_chars = (
        max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars
    )

    from .search import web_search

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
        return f"No {ft} files found for query: {query}"

    outputs: list[str] = []
    for idx, item in enumerate(matched, 1):
        url = item.get("href", "")
        title = item.get("title", "")
        try:
            data = _fetch_bytes(url)
        except Exception as exc:  # noqa: BLE001
            outputs.append(f"=== 候補 {idx} ===\nURL: {url}\nタイトル: {title}\n状態: 取得失敗 ({exc})\n")
            continue

        text = _text_from_bytes(url, data, ft, per_file_chars)
        mime = _guess_mime(Path(httpx.URL(url).path or f"file.{ft}").name, ft)
        name = Path(httpx.URL(url).path or f"file.{ft}").name
        if session_id:
            store_fetched_file(session_id, name, data, mime, text, url)
        outputs.append(
            f"=== 候補 {idx} ===\n"
            f"URL: {url}\n"
            f"タイトル: {title}\n"
            f"形式: {mime}\n"
            f"サイズ: {len(data)} bytes\n"
            f"文字数: {len(text)}\n"
            f"---\n{text}\n"
        )

    return "\n".join(outputs)
