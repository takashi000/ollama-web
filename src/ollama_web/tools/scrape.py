"""URL scraping tool that fetches a page and extracts readable text."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from ..config import settings


def scrape_url(url: str, max_chars: int | None = None) -> str:
    """Fetch a web page and return its main text content.

    Scripts, styles and other non-content elements are removed before
    collapsing whitespace. The returned text is truncated to ``max_chars``.

    Args:
      url: The absolute URL of the page to scrape.
      max_chars: Maximum number of characters to return. Defaults to the value
        in settings.

    Returns:
      The extracted plain text of the page, truncated to ``max_chars``. If the
        request fails a short error description is returned instead.
    """
    if not url or not url.strip():
        return "Invalid URL."

    limit = max_chars if max_chars is not None and max_chars > 0 else settings.scrape_max_chars
    headers = {"User-Agent": "ollama-web/0.1 (+https://github.com/local)"}

    try:
        with httpx.Client(
            timeout=settings.scrape_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        return f"Failed to fetch {url}: {exc}"

    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags.
    for tag in soup(["script", "style", "noscript", "template", "head", "nav", "footer", "aside"]):
        tag.decompose()

    # Prefer article/main content when available.
    container = soup.find("article") or soup.find("main") or soup
    text = container.get_text(separator="\n")
    # Collapse blank lines and trailing whitespace per line.
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)

    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "\n…[truncated]"
    return cleaned or "No readable text content found."