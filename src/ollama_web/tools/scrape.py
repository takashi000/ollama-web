"""URL scraping tool that fetches a page and extracts readable text."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..config import settings
from ..context_budget import standalone_result_limit
from .helper.pagination import (
    PageSizeError,
    format_page_to_limit,
    make_token,
    parse_token,
    text_page,
)
from .helper.safe_http import UnsafeURL, fetch_public_bytes

_TOOL_NAME = "scrape_url"
logger = logging.getLogger("ollama_web.tools.scrape")


def extract_readable_text(data: bytes, content_type: str = "") -> str:
    """Decode and normalize the complete readable body of an HTML payload."""
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.rsplit("charset=", 1)[-1].split(";", 1)[0].strip()
    html = data.decode(encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "head", "nav", "footer", "aside"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or soup
    text = container.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def scrape_url(url: str, max_chars: int | None = None, continue_from: str | None = None) -> str:
    """Fetch a web page and return its main text content.

    Scripts, styles and other non-content elements are removed before
    collapsing whitespace. The returned text is a page of at most ``max_chars``
    characters; when more content is available, a ``next_token`` is appended
    that can be passed back via ``continue_from`` to fetch the next page.

    Args:
      url: The absolute URL of the page to scrape.
      max_chars: Maximum number of characters to return per page. Defaults to
        the value in settings.
      continue_from: Opaque continuation token returned in a previous call's
        ``next_token``. When provided, the page is resumed from the encoded
        offset instead of the beginning.

    Returns:
      The extracted plain text of the page (one page of ``max_chars``),
      followed by a ``---page-info---`` footer when more content is available.
      If the request fails a short error description is returned instead.
    """
    if not url or not url.strip():
        return "Invalid URL."

    limit = max_chars if max_chars is not None and max_chars > 0 else settings.scrape_max_chars

    # Resolve the resumption offset from the continuation token, if any.
    offset = 0
    if continue_from:
        try:
            payload = parse_token(continue_from, _TOOL_NAME)
        except ValueError as exc:
            return f"Invalid continuation token: {exc}"
        # The token's URL takes precedence; it identifies the page being paged.
        url = str(payload.get("url") or url)
        offset = int(payload.get("offset", 0))

    try:
        data, final_url, content_type = fetch_public_bytes(url, timeout=settings.scrape_timeout)
        url = final_url
    except UnsafeURL as exc:
        return f"Blocked URL: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Failed to fetch {url}: {exc}"

    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type.startswith(("image/", "audio/", "video/")):
        return f"Unsupported content type for text scraping: {media_type}."

    cleaned = extract_readable_text(data, content_type)

    if not cleaned:
        return "No readable text content found."

    try:
        result = format_page_to_limit(
            lambda body_budget: text_page(cleaned, offset, body_budget),
            lambda cursor: make_token(_TOOL_NAME, url, cursor["offset"]),
            requested_chars=limit,
            result_limit=standalone_result_limit(limit),
        )
        logger.info(
            "scrape page offset=%d result_chars=%d has_more=%s",
            offset,
            len(result),
            "---page-info---" in result,
        )
        return result
    except (PageSizeError, ValueError) as exc:
        return f"Scrape pagination failed: {exc}"
