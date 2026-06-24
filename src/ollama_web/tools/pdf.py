"""PDF text extraction helpers."""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader

from ..config import settings


def extract_pdf_text(data: bytes, max_chars: int | None = None) -> str:
    """Extract text from PDF bytes.

    Args:
      data: Raw PDF file bytes.
      max_chars: Maximum characters to return (default from settings).

    Returns:
      Extracted plain text. Errors are returned as short strings.
    """
    if not data:
        return "Empty PDF data."

    limit = max_chars if max_chars is not None and max_chars > 0 else settings.fetch_max_chars

    try:
        reader = PdfReader(stream=io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return f"Failed to parse PDF: {exc}"

    parts: list[str] = []
    for page in reader.pages:
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


def pdf_to_text(url_or_path: str, max_chars: int | None = None) -> str:
    """Extract text from a local PDF path or remote URL.

    Args:
      url_or_path: File path or URL pointing to a PDF.
      max_chars: Maximum characters to return.

    Returns:
      Extracted plain text or an error message.
    """
    if not url_or_path or not url_or_path.strip():
        return "Invalid PDF source."

    src = url_or_path.strip()
    data: bytes | None = None
    if src.startswith("http://") or src.startswith("https://"):
        import httpx
        from ..config import settings as cfg  # local import to avoid top-level cycles

        try:
            with httpx.Client(
                timeout=cfg.scrape_timeout,
                follow_redirects=True,
                headers={"User-Agent": "ollama-web/0.1 (+https://github.com/local)"},
            ) as client:
                resp = client.get(src)
                resp.raise_for_status()
                data = resp.content
        except Exception as exc:  # noqa: BLE001
            return f"Failed to fetch PDF: {exc}"
    else:
        path = Path(src)
        if not path.exists():
            return f"PDF not found: {src}"
        try:
            data = path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            return f"Failed to read PDF: {exc}"

    if data is None:
        return "No PDF data loaded."
    return extract_pdf_text(data, max_chars=max_chars)
