"""PDF text extraction helpers."""

from __future__ import annotations

import io

from pypdf import PdfReader

from ...config import settings


def extract_pdf_text(data: bytes, max_chars: int | None = None) -> str:
    """Extract text from PDF bytes."""
    if not data:
        return "Empty PDF data."

    limit = max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars

    if len(data) > settings.max_fetch_mb * 1024 * 1024:
        return "PDF exceeds the configured size limit."

    try:
        reader = PdfReader(stream=io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return f"Failed to parse PDF: {exc}"

    parts: list[str] = []
    for page in reader.pages[: settings.max_pdf_pages]:
        try:
            text = page.extract_text()
            if text:
                parts.append(text)
        except Exception:  # noqa: BLE001
            continue

    full = "\n\n".join(parts)
    if len(full) > limit:
        full = full[:limit].rsplit(" ", 1)[0] + "\n…[truncated]"
    return full or "No text could be extracted from the PDF."
