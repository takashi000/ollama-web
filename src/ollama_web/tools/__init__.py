"""Tool package for ollama-web."""

from .fetch import search_and_fetch
from .image import resize_image
from .pdf import extract_pdf_text, pdf_to_text
from .registry import ToolRegistry, all_tool_callables, default_registry
from .scrape import scrape_url
from .search import web_search

__all__ = [
    "search_and_fetch",
    "resize_image",
    "extract_pdf_text",
    "pdf_to_text",
    "ToolRegistry",
    "all_tool_callables",
    "default_registry",
    "scrape_url",
    "web_search",
]
