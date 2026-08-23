"""Lossless PDF text extraction helpers."""

from __future__ import annotations

import io

from pypdf import PdfReader

from ...config import settings
from .pagination import PageResult


def extract_pdf_page(
    data: bytes,
    *,
    max_chars: int,
    page_index: int = 0,
    page_char_offset: int = 0,
    max_size_mb: int | None = None,
) -> PageResult:
    """Extract a PDF slice using a page-and-character continuation cursor."""
    start = {"page_index": page_index, "page_char_offset": page_char_offset}
    if not data:
        return PageResult("Empty PDF data.", start, start, False)
    size_limit_mb = settings.max_fetch_mb if max_size_mb is None else max_size_mb
    if len(data) > size_limit_mb * 1024 * 1024:
        return PageResult("PDF exceeds the configured size limit.", start, start, False)
    if page_index < 0 or page_char_offset < 0:
        return PageResult("Invalid PDF continuation cursor.", start, start, False)

    try:
        reader = PdfReader(stream=io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return PageResult(f"Failed to parse PDF: {exc}", start, start, False)

    total_pages = len(reader.pages)
    if page_index > total_pages or (page_index == total_pages and page_char_offset):
        return PageResult("Invalid PDF continuation cursor.", start, start, False)
    if page_index == total_pages:
        return PageResult("", start, start, False)

    parts: list[str] = []
    warnings: list[str] = []
    remaining = max(1, max_chars)
    current_page = page_index
    current_char = page_char_offset
    pages_processed = 0

    while current_page < total_pages and pages_processed < settings.max_pdf_pages:
        try:
            page_text = reader.pages[current_page].extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).replace("\n", " ")[:120]
            warnings.append(f"PDF page {current_page + 1} could not be extracted: {detail}")
            if current_page + 1 < total_pages:
                parts.append("\n\n")
            current_page += 1
            current_char = 0
            pages_processed += 1
            continue

        if current_char > len(page_text):
            return PageResult("Invalid PDF continuation cursor.", start, start, False)

        available = page_text[current_char:]
        take = min(remaining, len(available))
        if take:
            parts.append(available[:take])
            current_char += take
            remaining -= take

        if current_char < len(page_text):
            break

        if current_page + 1 < total_pages:
            parts.append("\n\n")
        current_page += 1
        current_char = 0
        pages_processed += 1
        if remaining <= 0:
            break

    next_cursor = {"page_index": current_page, "page_char_offset": current_char}
    has_more = current_page < total_pages
    text = "".join(parts)
    if not text and not warnings and not has_more:
        text = "No text could be extracted from the PDF."
    return PageResult(text, start, next_cursor, has_more, tuple(warnings))


def legacy_pdf_offset_to_cursor(data: bytes, flat_offset: int) -> tuple[int, int]:
    """Translate a v1 ``\n\n``-joined character offset to the v2 PDF cursor."""
    if flat_offset < 0:
        raise ValueError("Legacy PDF offset must not be negative.")
    reader = PdfReader(stream=io.BytesIO(data))
    remaining = flat_offset
    for page_index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            page_text = ""
        if remaining <= len(page_text):
            return page_index, remaining
        remaining -= len(page_text)
        if page_index + 1 < len(reader.pages):
            if remaining <= 2:
                return page_index + 1, 0
            remaining -= 2
    if remaining == 0:
        return len(reader.pages), 0
    raise ValueError("Legacy PDF offset is out of range.")


def extract_pdf_text(
    data: bytes,
    max_chars: int | None = None,
    page_offset: int = 0,
) -> tuple[str, bool, int]:
    """Backward-compatible wrapper for page-only internal callers."""
    limit = max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars
    page = extract_pdf_page(data, max_chars=limit, page_index=page_offset)
    text = "".join(f"[warning] {warning}\n" for warning in page.warnings) + page.text
    return text, page.has_more, page.next_cursor["page_index"] if page.has_more else 0
