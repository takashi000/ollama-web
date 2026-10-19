"""Web metasearch with internal region selection and compact summaries."""

from __future__ import annotations

import logging
import re
import unicodedata
from ipaddress import ip_address
from urllib.parse import unquote, urlsplit

from ddgs import DDGS

from ..config import settings

logger = logging.getLogger(__name__)

_QUERY_METADATA = re.compile(r"(?:https?://|www\.)\S+|(?<!\S)-?(?:site|filetype):\S+", re.I)
_KANA = re.compile(r"[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff]")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)


def _resolve_search_region(query: str) -> str:
    if settings.search_region != "auto":
        return settings.search_region
    text = _QUERY_METADATA.sub("", unicodedata.normalize("NFKC", query))
    return "jp-jp" if _KANA.search(text) else "us-en"


def _is_http_url(value: str) -> bool:
    """Validate syntax only; search summaries do not resolve or fetch URLs."""
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None:
            return False
        # Accessing port also rejects malformed/out-of-range port numbers.
        if parsed.port is not None and parsed.port == 0:
            return False
        try:
            ip_address(host)
            return True
        except ValueError:
            ascii_host = host.rstrip(".").encode("idna").decode("ascii")
            return len(ascii_host) <= 253 and all(
                _HOST_LABEL.fullmatch(label) for label in ascii_host.split(".")
            )
    except (ValueError, UnicodeError):
        return False


def _result_url(value: str) -> str:
    if not _is_http_url(value):
        return ""
    parsed = urlsplit(value)
    if (parsed.hostname or "").rstrip(".") == "r.search.yahoo.com" and "/RU=" in parsed.path:
        target = parsed.path.split("/RU=", 1)[1]
        value = unquote(re.split(r"/R[KS]=", target, maxsplit=1)[0])
        if not _is_http_url(value):
            return ""
        parsed = urlsplit(value)
    if (
        (parsed.hostname or "").rstrip(".") in {"bing.com", "www.bing.com"}
        and parsed.path.rstrip("/") == "/aclick"
    ):
        return ""
    return value


def _url_title(title: str, href: str) -> bool:
    def display_key(value: str) -> str:
        value = re.sub(r"^https?://", "", unquote(value), flags=re.I)
        value = re.sub(r"^www\.", "", value, flags=re.I)
        return value.rstrip("/")

    if display_key(title) == display_key(href):
        return True
    # Only whole URL-like labels, not headings such as "example.com の使い方".
    candidate = title if re.match(r"^https?://", title, re.I) else "https://" + title
    if _is_http_url(candidate):
        host = urlsplit(candidate).hostname or ""
        tld = host.rstrip(".").rsplit(".", 1)[-1].encode("idna").decode("ascii")
        return bool(re.match(r"^https?://", title, re.I)) or (
            "." in host and bool(re.fullmatch(r"[a-z]{2,}|xn--[a-z0-9-]+", tld, re.I))
        )
    return False


def web_search(query: str, max_results: int | None = None) -> list[dict[str, str]]:
    """Search the web with ddgs auto and return compact result summaries.

    Search region is selected internally from the query or server settings.

    Args:
      query: The search query string.
      max_results: Maximum number of results to return (default from settings).

    Returns:
      A list of dictionaries with keys 'title', 'href' and 'body' for each
      result. A missing or URL-only title is an empty string; use the body
      for context. Invalid URLs and known ad links are omitted, so fewer
      results may be returned. Returns an empty list for empty results. Provider failures
      are logged and propagated to the tool execution layer.
    """
    if not query or not query.strip():
        return []

    limit = (
        max_results if max_results is not None and max_results > 0 else settings.search_max_results
    )

    results: list[dict[str, str]] = []
    region = _resolve_search_region(query)
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=limit, region=region, backend="auto"):
                href = _result_url(str(item.get("href") or item.get("url") or "").strip())
                if not href:
                    continue
                title = str(item.get("title") or "").strip()
                results.append(
                    {
                        "title": "" if _url_title(title, href) else title,
                        "href": href,
                        "body": str(item.get("body") or item.get("snippet") or "").strip(),
                    }
                )
    except Exception:  # noqa: BLE001
        # Preserve the provider exception for the tool layer while recording
        # the traceback without logging the potentially sensitive query.
        logger.exception("ddgs web search failed")
        raise

    return results[:limit]
