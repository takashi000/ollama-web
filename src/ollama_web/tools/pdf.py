"""PDF tool exposed to Ollama."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config import settings
from .helper.pagination import (
    PageResult,
    PageSizeError,
    format_page_to_limit,
    make_token,
    parse_token,
)
from .helper.pdf import extract_pdf_page
from .helper.safe_http import UnsafeURL, fetch_public_bytes

_TOOL_NAME = "pdf_to_text"
_ATTACHMENT_PREFIX = "attachment:"
logger = logging.getLogger("ollama_web.tools.pdf")

AttachmentLoader = Callable[[str], bytes | None]


def _cursor_value(payload: dict[str, Any], key: str, fallback: int = 0) -> int:
    raw = payload.get(key, fallback)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"Token {key!r} is not an integer.")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Token {key!r} is not an integer.") from exc
    if value < 0:
        raise ValueError(f"Token {key!r} must not be negative.")
    return value


def _pdf_to_text(
    url_or_path: str,
    max_chars: int | None,
    continue_from: str | None,
    attachment_loader: AttachmentLoader | None,
) -> str:
    if not url_or_path or not url_or_path.strip():
        return "Invalid PDF source."

    src = url_or_path.strip()
    page_index = 0
    page_char_offset = 0
    if continue_from:
        try:
            payload = parse_token(continue_from, _TOOL_NAME)
            src = str(payload.get("url") or src)
            # Version 1 used offset as the PDF page index.
            page_index = _cursor_value(payload, "page_index", int(payload["offset"]))
            page_char_offset = _cursor_value(payload, "page_char_offset")
        except ValueError as exc:
            return f"Invalid continuation token: {exc}"

    if src.startswith(_ATTACHMENT_PREFIX):
        file_id = src.removeprefix(_ATTACHMENT_PREFIX)
        data = attachment_loader(file_id) if attachment_loader is not None else None
        if data is None:
            return "Attachment PDF is unavailable in this session."
        source_kind = "attachment"
    elif src.startswith("http://") or src.startswith("https://"):
        try:
            data, final_url, _ = fetch_public_bytes(src, timeout=settings.scrape_timeout)
            src = final_url or src
        except UnsafeURL as exc:
            return f"Blocked URL: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Failed to fetch PDF: {exc}"
        source_kind = "remote"
    else:
        return "Only http(s) PDF URLs are supported."

    requested = max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars

    def page_factory(body_budget: int) -> PageResult:
        return extract_pdf_page(
            data,
            max_chars=body_budget,
            page_index=page_index,
            page_char_offset=page_char_offset,
            max_size_mb=(settings.max_upload_mb if source_kind == "attachment" else None),
        )

    def token_factory(cursor: dict[str, int]) -> str:
        return make_token(
            _TOOL_NAME,
            src,
            cursor["page_index"],
            page_index=cursor["page_index"],
            page_char_offset=cursor["page_char_offset"],
        )

    try:
        result = format_page_to_limit(
            page_factory,
            token_factory,
            requested_chars=requested,
            result_limit=settings.max_tool_result_chars,
        )
    except PageSizeError as exc:
        return f"PDF pagination failed: {exc}"

    logger.info(
        "pdf page source=%s page_index=%d page_char_offset=%d result_chars=%d has_more=%s",
        source_kind,
        page_index,
        page_char_offset,
        len(result),
        "---page-info---" in result,
    )
    return result


def pdf_to_text(
    url_or_path: str, max_chars: int | None = None, continue_from: str | None = None
) -> str:
    """Extract a losslessly paged PDF from an HTTP(S) URL.

    Args:
      url_or_path: HTTP(S) PDF URL. In chat, a session attachment URI may also
        be supplied by the attachment context. Arbitrary filesystem paths are
        never accepted.
      max_chars: Requested maximum source characters for this page. The tool
        may return fewer characters so metadata and its token remain intact.
      continue_from: Opaque continuation token from the preceding result.
    """
    return _pdf_to_text(url_or_path, max_chars, continue_from, None)


def make_session_pdf_tool(attachment_loader: AttachmentLoader) -> Callable[..., str]:
    """Create a session-scoped ``pdf_to_text`` supporting attachment IDs."""

    def session_pdf_to_text(
        url_or_path: str,
        max_chars: int | None = None,
        continue_from: str | None = None,
    ) -> str:
        return _pdf_to_text(url_or_path, max_chars, continue_from, attachment_loader)

    session_pdf_to_text.__name__ = _TOOL_NAME
    session_pdf_to_text.__doc__ = pdf_to_text.__doc__
    return session_pdf_to_text
