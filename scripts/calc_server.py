"""Test MCP server providing basic math tools.

Usage:
  python scripts/calc_server.py                # stdio transport
  python scripts/calc_server.py streamable-http  # HTTP transport
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calc")


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


@mcp.tool()
def sqrt(value: float) -> float:
    """Return the square root of a non-negative number."""
    if value < 0:
        raise ValueError("value must be non-negative")
    return value**0.5


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
