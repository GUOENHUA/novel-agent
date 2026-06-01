"""MemoryManager — orchestrates memory providers for the agent.

Borrowed from hermes-agent `agent/memory_manager.py`.

Single integration point in agent.py. Replaces scattered per-backend
code with one manager that delegates to registered providers.

Only ONE external provider is allowed at a time — attempting to
register a second external provider is rejected.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional

from novel_agent.memory.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


class MemoryManager:
    """Orchestrates the built-in provider plus at most one external provider.

    The builtin provider is always first. Only one non-builtin (external)
    provider is allowed. Failures in one provider never block the other.
    """

    def __init__(self) -> None:
        self._providers: list[MemoryProvider] = []
        self._tool_to_provider: dict[str, MemoryProvider] = {}
        self._has_external: bool = False

    # -- Registration ----------------------------------------------------------

    def add_provider(self, provider: MemoryProvider) -> None:
        """Register a memory provider.

        Built-in provider (name == "builtin") is always accepted.
        Only ONE external provider is allowed — second attempt is rejected.
        """
        is_builtin = provider.name == "builtin"

        if not is_builtin:
            if self._has_external:
                existing = next(
                    (p.name for p in self._providers if p.name != "builtin"),
                    "unknown",
                )
                logger.warning(
                    "Rejected memory provider '%s' — external provider '%s' is "
                    "already registered. Only one external memory provider is "
                    "allowed at a time.",
                    provider.name, existing,
                )
                return
            self._has_external = True

        self._providers.append(provider)

        # Index tool names → provider for routing
        for schema in provider.get_tool_schemas():
            tool_name = schema.get("name", "")
            if tool_name and tool_name not in self._tool_to_provider:
                self._tool_to_provider[tool_name] = provider
            elif tool_name in self._tool_to_provider:
                logger.warning(
                    "Memory tool name conflict: '%s' already registered by %s",
                    tool_name, self._tool_to_provider[tool_name].name,
                )

        logger.info(
            "Memory provider '%s' registered (%d tools)",
            provider.name, len(provider.get_tool_schemas()),
        )

    @property
    def providers(self) -> list[MemoryProvider]:
        """All registered providers in order."""
        return list(self._providers)

    # -- System prompt ---------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Collect system prompt blocks from all providers."""
        blocks = []
        for provider in self._providers:
            try:
                block = provider.system_prompt_block()
                if block and block.strip():
                    blocks.append(block)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' system_prompt_block() failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(blocks)

    # -- Prefetch / recall -----------------------------------------------------

    def prefetch_all(self, query: str) -> list[dict[str, Any]]:
        """Collect prefetch results from all providers.

        Returns list of {"source": provider_name, "content": text, "files": [paths]}.
        Failures in one provider don't block others.
        """
        results = []
        for provider in self._providers:
            try:
                result = provider.prefetch(query)
                if result and result.strip():
                    results.append({
                        "source": provider.name,
                        "content": result,
                    })
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' prefetch failed (non-fatal): %s",
                    provider.name, e,
                )
        return results

    # -- Sync -------------------------------------------------------------------

    def sync_all(
        self,
        user_content: str,
        assistant_content: str,
        **kwargs,
    ) -> None:
        """Sync a completed turn to all providers."""
        for provider in self._providers:
            try:
                provider.sync_turn(user_content, assistant_content, **kwargs)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' sync_turn failed: %s",
                    provider.name, e,
                )

    # -- Tools ------------------------------------------------------------------

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Collect tool schemas from all providers."""
        schemas = []
        seen = set()
        for provider in self._providers:
            try:
                for schema in provider.get_tool_schemas():
                    name = schema.get("name", "")
                    if name and name not in seen:
                        schemas.append(schema)
                        seen.add(name)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' get_tool_schemas() failed: %s",
                    provider.name, e,
                )
        return schemas

    def has_tool(self, tool_name: str) -> bool:
        """Check if any provider handles this tool."""
        return tool_name in self._tool_to_provider

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        """Route a tool call to the correct provider. Returns JSON string."""
        provider = self._tool_to_provider.get(tool_name)
        if provider is None:
            from novel_agent.tools.registry import tool_error
            return tool_error(f"No memory provider handles tool '{tool_name}'")
        try:
            return provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as e:
            logger.error(
                "Memory provider '%s' handle_tool_call(%s) failed: %s",
                provider.name, tool_name, e,
            )
            from novel_agent.tools.registry import tool_error
            return tool_error(f"Memory tool '{tool_name}' failed: {e}")

    # -- Lifecycle hooks --------------------------------------------------------

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Notify all providers before context compression.

        Returns combined text to include in the compression summary prompt.
        """
        parts = []
        for provider in self._providers:
            try:
                result = provider.on_pre_compress(messages)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_pre_compress failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    def initialize_all(self, memory_dir: str, **kwargs) -> None:
        """Initialize all providers."""
        for provider in self._providers:
            try:
                provider.initialize(memory_dir, **kwargs)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' initialize failed: %s",
                    provider.name, e,
                )

    def shutdown_all(self) -> None:
        """Shut down all providers (reverse order for clean teardown)."""
        for provider in reversed(self._providers):
            try:
                provider.shutdown()
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' shutdown failed: %s",
                    provider.name, e,
                )
