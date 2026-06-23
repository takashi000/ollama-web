"""Registry of tools exposed to the LLM."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .fetch import search_and_fetch
from .pdf import pdf_to_text
from .scrape import scrape_url
from .search import web_search


@dataclass
class ToolRegistry:
    """A simple registry mapping tool names to callable implementations."""

    _tools: dict[str, Callable[..., object]] = field(default_factory=dict)

    def register(self, func: Callable[..., object]) -> Callable[..., object]:
        """Register a callable under its ``__name__``."""
        self._tools[func.__name__] = func
        return func

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    @property
    def callables(self) -> list[Callable[..., object]]:
        return list(self._tools.values())

    def get(self, name: str) -> Callable[..., object] | None:
        return self._tools.get(name)

    def execute(self, name: str, arguments: dict[str, object] | str) -> str:
        """Execute a named tool and return a JSON-encoded string result.

        Args:
          name: The tool function name.
          arguments: Either a dict of keyword arguments or a JSON string that
            decodes to one.

        Returns:
          A JSON string representation of the tool's return value, or an error
          object when the tool is unknown or raises.
        """
        func = self.get(name)
        if func is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                return json.dumps({"error": f"Invalid JSON arguments for {name}: {arguments}"})

        try:
            result = func(**arguments)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)


def default_registry() -> ToolRegistry:
    """Build a registry pre-loaded with the built-in tools."""
    reg = ToolRegistry()
    reg.register(web_search)
    reg.register(scrape_url)
    reg.register(search_and_fetch)
    reg.register(pdf_to_text)
    return reg


def all_tool_callables(registry: ToolRegistry | None = None) -> Sequence[Callable[..., object]]:
    """Return the list of callables suitable for ``ollama`` ``tools`` argument."""
    reg = registry or default_registry()
    return reg.callables