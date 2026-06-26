"""Registry of tools exposed to the LLM."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .fetch import search_and_fetch
from .pdf import pdf_to_text
from .scrape import scrape_url
from .search import web_search


@dataclass
class ToolRegistry:
    """A simple registry mapping tool names to callable implementations."""

    _tools: dict[str, Callable[..., object]] = field(default_factory=dict)
    _ollama_tool_defs: list[dict[str, Any]] = field(default_factory=list)
    _mcp_tool_names: set[str] = field(default_factory=set)

    def register(self, func: Callable[..., object]) -> Callable[..., object]:
        """Register a callable under its ``__name__``."""
        self._tools[func.__name__] = func
        return func

    def register_mcp_tool(
        self,
        name: str,
        tool_def: dict[str, Any],
        executor: Callable[..., object],
    ) -> Callable[..., object]:
        """Register a remote MCP tool under a normalized name.

        Args:
          name: The normalized ollama-visible tool name.
          tool_def: The ollama-compatible tool definition dict.
          executor: A callable that accepts ``(full_name, arguments)`` and
            returns a JSON-encoded result string.
        """
        self._tools[name] = executor
        self._mcp_tool_names.add(name)
        # Avoid duplicate definitions if a registry is rebuilt.
        self._ollama_tool_defs = [d for d in self._ollama_tool_defs if d.get("name") != name]
        self._ollama_tool_defs.append(tool_def)
        return executor

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    @property
    def callables(self) -> list[Callable[..., object]]:
        return list(self._tools.values())

    @property
    def ollama_tools(self) -> list[dict[str, Any] | Callable[..., object]]:
        """Return a list suitable for ollama's ``tools`` argument.

        Built-in tools are passed as their callables so ollama can infer schemas
        from docstrings. MCP tools are passed as pre-built dict definitions.
        """
        # Only return built-in callables and dict definitions; skip the MCP
        # executor callables stored in the tools dict.
        return [
            func for name, func in self._tools.items() if name not in self._mcp_tool_names
        ] + self._ollama_tool_defs

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
            if name in self._mcp_tool_names:
                # MCP executor expects (full_name, arguments).
                result = func(name, arguments)
            else:
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