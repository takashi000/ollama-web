"""PDF text extraction helpers."""

from __future__ import annotations

import io

from pypdf import PdfReader

from ...config import settings


def extract_pdf_text(
    data: bytes,
    max_chars: int | None = None,
    page_offset: int = 0,
) -> tuple[str, bool, int]:
    """Extract text from PDF bytes, starting at ``page_offset``.

    Args:
      data: Raw PDF bytes.
      max_chars: Maximum characters to return. Defaults to
        ``settings.fetch_max_chars``.
      page_offset: Zero-based index of the first page to extract. Pages before
        this index are skipped. Used by the pagination layer to resume.

    Returns:
      A tuple ``(text, has_more, next_page_offset)`` where:
        - ``text`` is the extracted text for this page (truncated to
          ``max_chars``).
        - ``has_more`` is True when there are remaining pages after the
          extracted slice.
        - ``next_page_offset`` is the page index to resume from on the next
          call (only meaningful when ``has_more`` is True).
    """
    if not data:
        return "Empty PDF data.", False, 0

    limit = max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars

    if len(data) > settings.max_fetch_mb * 1024 * 1024:
        return "PDF exceeds the configured size limit.", False, 0

    try:
        reader = PdfReader(stream=io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return f"Failed to parse PDF: {exc}", False, 0

    total_pages = len(reader.pages)
    if page_offset < 0:
        page_offset = 0
    if page_offset >= total_pages:
        return "No text could be extracted from the PDF.", False, 0

    # Cap the number of pages processed per call to max_pdf_pages.
    end = min(page_offset + settings.max_pdf_pages, total_pages)

    parts: list[str] = []
    collected = 0
    last_extracted_page = page_offset
    for page_index in range(page_offset, end):
        try:
            text = reader.pages[page_index].extract_text()
        except Exception:  # noqa: BLE001
            continue
        if text:
            parts.append(text)
            collected += len(text)
            last_extracted_page = page_index
        # Stop as soon as we reach the per-call character budget so the page
        # boundary for the next token is accurate.
        if collected >= limit:
            break

    full = "\n\n".join(parts)
    if len(full) > limit:
        full = full[:limit].rsplit(" ", 1)[0] + "\n…[truncated]"

    # has_more when there are pages beyond the last one we extracted.
    has_more = (last_extracted_page + 1) < total_pages
    next_page_offset = last_extracted_page + 1 if has_more else 0

    if not full:
        return "No text could be extracted from the PDF.", has_more, next_page_offset
    return full, has_more, next_page_offset
