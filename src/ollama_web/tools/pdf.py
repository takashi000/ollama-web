"""PDF tool exposed to Ollama."""

from __future__ import annotations

from ..config import settings
from .helper.pagination import format_paged_result, make_token, parse_token
from .helper.pdf import extract_pdf_text
from .helper.safe_http import UnsafeURL, fetch_public_bytes

_TOOL_NAME = "pdf_to_text"


def pdf_to_text(
    url_or_path: str, max_chars: int | None = None, continue_from: str | None = None
) -> str:
    """Extract text from a local PDF path or remote URL.

    The text is returned one page at a time. When more pages remain, a
    ``next_token`` is appended that can be passed back via ``continue_from``
    to fetch the next batch of pages.

    Args:
      url_or_path: File path or URL pointing to a PDF.
      max_chars: Maximum characters to return per page.
      continue_from: Opaque continuation token returned in a previous call's
        ``next_token``. When provided, extraction resumes from the encoded
        page offset.

    Returns:
      Extracted plain text for this page, followed by a ``---page-info---``
      footer when more pages are available, or an error message.
    """
    if not url_or_path or not url_or_path.strip():
        return "Invalid PDF source."

    src = url_or_path.strip()

    # Resolve the resumption page offset from the continuation token, if any.
    page_offset = 0
    if continue_from:
        try:
            payload = parse_token(continue_from, _TOOL_NAME)
        except ValueError as exc:
            return f"Invalid continuation token: {exc}"
        src = str(payload.get("url") or src)
        page_offset = int(payload.get("offset", 0))
        if page_offset < 0:
            page_offset = 0

    data: bytes | None = None
    if src.startswith("http://") or src.startswith("https://"):
        try:
            data, _, _ = fetch_public_bytes(src, timeout=settings.scrape_timeout)
        except UnsafeURL as exc:
            return f"Blocked URL: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to fetch PDF: {exc}"
    else:
        return "Only http(s) PDF URLs are supported."

    if data is None:
        return "No PDF data loaded."

    text, has_more, next_page_offset = extract_pdf_text(
        data, max_chars=max_chars, page_offset=page_offset
    )
    next_token = make_token(_TOOL_NAME, src, next_page_offset) if has_more else None
    return format_paged_result(text, has_more=has_more, next_token=next_token)
