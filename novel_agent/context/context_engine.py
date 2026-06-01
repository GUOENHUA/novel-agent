"""Abstract base class for pluggable context engines.

Borrowed from hermes-agent `agent/context_engine.py`.

A context engine controls how conversation context is managed when
approaching the model's token limit. The built-in NovelCompressor
is the default implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextEngine(ABC):
    """Base class all context engines must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'compressor')."""

    # -- Token state -----------------------------------------------------------

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0

    # -- Compaction parameters -------------------------------------------------

    threshold_percent: float = 0.70
    protect_first_n: int = 3
    protect_last_n: int = 6

    # -- Core interface --------------------------------------------------------

    @abstractmethod
    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Update tracked token usage from an API response."""

    @abstractmethod
    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """Return True if compaction should fire this turn."""

    @abstractmethod
    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compact the message list and return the new message list."""

    # -- Optional: session lifecycle -------------------------------------------

    def on_session_reset(self) -> None:
        """Called on /new or /reset."""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
