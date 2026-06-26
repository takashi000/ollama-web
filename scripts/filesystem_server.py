"""Test MCP server exposing a small in-memory filesystem.

Usage:
  python scripts/filesystem_server.py                # stdio transport
  python scripts/filesystem_server.py streamable-http  # HTTP transport
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory_fs")

_ROOT = Path(".mcp_test_fs")


def _ensure_root() -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)


@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file from the test filesystem."""
    _ensure_root()
    target = (_ROOT / path).resolve()
    try:
        target.relative_to(_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("path must stay inside the test filesystem") from exc
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write a text file to the test filesystem."""
    _ensure_root()
    target = (_ROOT / path).resolve()
    try:
        target.relative_to(_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("path must stay inside the test filesystem") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"written {len(content)} chars to {path}"


@mcp.tool()
def list_files() -> list[str]:
    """List files in the test filesystem."""
    _ensure_root()
    return [str(p.relative_to(_ROOT)) for p in _ROOT.rglob("*") if p.is_file()]


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
