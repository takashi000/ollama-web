"""Test MCP server that pretends to be different assistants.

Usage:
  python scripts/persona_server.py                # stdio transport
  python scripts/persona_server.py streamable-http  # HTTP transport
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("persona")

_PERSONAS: dict[str, str] = {
    "pirate": "Arr! Yer askin' a salty sea dog.",
    "robot": "Beep boop. Processing query with logic circuits.",
    "poet": "Hark, thy words dance like petals on the wind.",
    "chef": "Mamma mia! Let's cook up an answer.",
}


@mcp.tool()
def list_personas() -> list[str]:
    """Return available persona names."""
    return list(_PERSONAS.keys())


@mcp.tool()
def chat_as_persona(persona: str, message: str) -> str:
    """Respond to a message in the style of the chosen persona."""
    greeting = _PERSONAS.get(persona.lower(), "Hello.")
    return f"[{persona}] {greeting} You said: {message}"


@mcp.tool()
def explain_style(persona: str) -> str:
    """Describe how the chosen persona speaks."""
    return _PERSONAS.get(
        persona.lower(),
        "This persona has no special style.",
    )


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    mcp.settings.port = port
    mcp.run(transport=transport)
