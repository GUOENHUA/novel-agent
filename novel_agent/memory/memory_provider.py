"""Abstract base class for pluggable memory providers.

Borrowed from hermes-agent `agent/memory_provider.py`.

Memory providers give the agent persistent recall across sessions.
The MemoryManager enforces a one-external-provider limit to prevent
tool schema bloat and conflicting memory backends.

Lifecycle (called by MemoryManager):
  initialize()          — connect, create resources, warm up
  system_prompt_block()  — static text for the system prompt
  prefetch(query)        — recall relevant context before each turn
  sync_turn(user, asst)  — persist after each turn
  get_tool_schemas()     — tool schemas to expose to the model
  handle_tool_call()     — dispatch a tool call
  shutdown()             — clean exit
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """Abstract base class for memory providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'builtin', 'vector_db')."""

    # -- Core lifecycle ---------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured and ready.

        Called during agent init to decide whether to activate the provider.
        Should not make network calls — just check config and installed deps.
        """

    @abstractmethod
    def initialize(self, memory_dir: str, **kwargs) -> None:
        """Initialize for a session.

        Called once at agent startup. May create directories, load indexes,
        establish connections, etc.

        Args:
            memory_dir: Path to the memory storage directory.
            **kwargs: Additional provider-specific config.
        """

    def system_prompt_block(self) -> str:
        """Return text to include in the system prompt.

        Called during system prompt assembly. Return empty string to skip.
        This is for STATIC provider info (instructions, status). Prefetched
        recall context is injected separately via prefetch().
        """
        return ""

    def prefetch(self, query: str) -> str:
        """Recall relevant context for the upcoming turn.

        Called before each API call. Return formatted text to inject as
        context, or empty string if nothing relevant.
        """
        return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        **kwargs,
    ) -> None:
        """Persist a completed turn to the backend.

        Called after each turn. Should be non-blocking.
        """

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas this provider exposes.

        Each schema follows the Anthropic tool use format:
        {"name": "...", "description": "...", "input_schema": {...}}

        Return empty list if this provider has no tools (context-only).
        """

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        """Handle a tool call for one of this provider's tools.

        Must return a JSON string (the tool result).
        Only called for tool names returned by get_tool_schemas().
        """
        raise NotImplementedError(
            f"Provider {self.name} does not handle tool {tool_name}"
        )

    def shutdown(self) -> None:
        """Clean shutdown — flush queues, close connections."""

    # -- Optional hooks ---------------------------------------------------------

    def on_session_end(self) -> None:
        """Called when a session ends (explicit exit or timeout)."""

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Called before context compression discards old messages.

        Return text to include in the compression summary prompt so the
        compressor preserves provider-extracted insights.
        """
        return ""
