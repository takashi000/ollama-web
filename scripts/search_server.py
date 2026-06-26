"""Test MCP server providing a small searchable document index.

Usage:
  python scripts/search_server.py                # stdio transport
  python scripts/search_server.py streamable-http  # HTTP transport
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("search")

_DOCS: dict[str, str] = {
    "python": "Python is a high-level programming language.",
    "mcp": "Model Context Protocol enables tool interoperability.",
    "ollama": "Ollama runs large language models locally.",
    "starlette": "Starlette is a lightweight ASGI framework.",
}


@mcp.tool()
def search_documents(query: str) -> list[dict[str, str]]:
    """Search the document index by keyword.

    Returns a list of documents with 'title' and 'snippet' keys.
    """
    results = []
    query_lower = query.lower()
    for title, body in _DOCS.items():
        if query_lower in title.lower() or query_lower in body.lower():
            results.append({"title": title, "snippet": body})
    return results


@mcp.tool()
def get_document(title: str) -> str:
    """Return the full body of a document by title."""
    return _DOCS.get(title.lower(), f"document '{title}' not found")


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
