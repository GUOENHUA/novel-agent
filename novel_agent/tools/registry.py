"""Central tool registry — singleton pattern, thread-safe.

Borrowed from hermes-agent `tools/registry.py`.

Each tool module calls ``registry.register()`` at module level to declare
its schema, handler, and toolset membership. The agent queries the registry
for tool definitions to send to the LLM.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "description", "emoji",
    )

    def __init__(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Optional[Callable] = None,
        description: str = "",
        emoji: str = "",
    ):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.description = description or schema.get("description", "")
        self.emoji = emoji


class ToolRegistry:
    """Singleton registry collecting tool schemas + handlers."""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._toolset_checks: Dict[str, Callable] = {}

    # -- Registration ----------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Optional[Callable] = None,
        description: str = "",
        emoji: str = "",
    ) -> None:
        """Register a tool. Called at module-import time by tool files."""
        existing = self._tools.get(name)
        if existing and existing.toolset != toolset:
            logger.warning(
                "Tool '%s' (toolset '%s') shadows existing '%s'",
                name, toolset, existing.toolset,
            )
        self._tools[name] = ToolEntry(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            description=description,
            emoji=emoji,
        )
        if check_fn and toolset not in self._toolset_checks:
            self._toolset_checks[toolset] = check_fn

    # -- Schema retrieval ------------------------------------------------------

    def get_definitions(self, tool_names: set[str] | None = None) -> list[dict]:
        """Return Anthropic-format tool schemas.

        Only tools whose check_fn() returns True (or have no check_fn) are included.
        """
        result = []
        for entry in self._tools.values():
            if tool_names and entry.name not in tool_names:
                continue
            if entry.check_fn:
                try:
                    if not entry.check_fn():
                        continue
                except Exception:
                    continue
            schema_with_name = {**entry.schema, "name": entry.name}
            result.append(schema_with_name)
        return result

    def get_all_tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(entry.name for entry in self._tools.values())

    # -- Dispatch --------------------------------------------------------------

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Execute a tool handler by name. Returns JSON string."""
        entry = self._tools.get(name)
        if not entry:
            return tool_error(f"Unknown tool: {name}")
        try:
            return entry.handler(args, **kwargs)
        except Exception as e:
            logger.exception("Tool '%s' dispatch error: %s", name, e)
            return tool_error(f"Tool '{name}' failed: {e}")


# Module-level singleton
registry = ToolRegistry()


# -- Response helpers ---------------------------------------------------------

def tool_error(message: str, **extra) -> str:
    """Return a JSON error string for tool handlers."""
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """Return a JSON result string for tool handlers."""
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
