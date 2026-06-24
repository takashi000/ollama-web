"""PDF tool exposed to Ollama."""

from __future__ import annotations

from ..config import settings
from .helper.pdf import extract_pdf_text
from .helper.safe_http import UnsafeURL, fetch_public_bytes


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
    return extract_pdf_text(data, max_chars=max_chars)
