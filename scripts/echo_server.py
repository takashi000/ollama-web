"""Test MCP server that echoes messages back.

Usage:
  python scripts/echo_server.py              # stdio transport
  python scripts/echo_server.py streamable-http  # HTTP transport
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(message: str) -> str:
    """Echo back the provided message unchanged."""
    return message


@mcp.tool()
def reverse(message: str) -> str:
    """Return the message reversed."""
    return message[::-1]


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
