"""Web search tool backed by DuckDuckGo (ddgs)."""

from __future__ import annotations

from ddgs import DDGS

from ..config import settings


def web_search(query: str, max_results: int | None = None) -> list[dict[str, str]]:
    """Search the web using DuckDuckGo and return compact result summaries.

    Args:
      query: The search query string.
      max_results: Maximum number of results to return (default from settings).

    Returns:
      A list of dictionaries with keys 'title', 'href' and 'body' for each
      result. Returns an empty list if the search fails or no results are found.
    """
    if not query or not query.strip():
        return []

    limit = (
        max_results
        if max_results is not None and max_results > 0
        else settings.search_max_results
    )

    results: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=limit):
                results.append(
                    {
                        "title": str(item.get("title", "")).strip(),
                        "href": str(item.get("href") or item.get("url") or "").strip(),
                        "body": str(item.get("body") or item.get("snippet") or "").strip(),
                    }
                )
    except Exception:  # noqa: BLE001
        # Swallow network/runtime errors so the chat loop can continue.
        return []

    return results[:limit]
