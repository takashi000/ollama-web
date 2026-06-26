"""Test MCP server simulating a weather service.

Usage:
  python scripts/weather_server.py                # stdio transport
  python scripts/weather_server.py streamable-http  # HTTP transport
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

_FORECASTS: dict[str, str] = {
    "tokyo": "sunny, 28C",
    "osaka": "cloudy, 26C",
    "sapporo": "rain, 18C",
    "new york": "sunny, 22C",
    "london": "rain, 15C",
}


@mcp.tool()
def get_weather(city: str) -> str:
    """Return a simple weather forecast for a city."""
    key = city.strip().lower()
    return _FORECASTS.get(key, f"no forecast available for {city}")


@mcp.tool()
def list_cities() -> list[str]:
    """Return the list of cities with available forecasts."""
    return list(_FORECASTS.keys())


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
