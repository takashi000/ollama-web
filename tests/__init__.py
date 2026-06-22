</content>
</write_in_file><write_to_file>
<path>tests/test_main.py</path>
<content>"""Tests for ollama_web.main."""

from ollama_web.main import main


def test_main() -> None:
    """Test that main returns 0."""
    assert main() == 0