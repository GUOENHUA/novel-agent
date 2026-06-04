"""Central context budget configuration.

ALL hardcoded numbers derive from a single master setting:
  NOVEL_AGENT_CONTEXT_LENGTH (env var, default 200_000)

Every per-field budget is computed as a percentage of the master value.
Change the env var and everything scales automatically — no need to hunt
down magic numbers scattered across files.

Borrowed pattern from Claude Code's getContextWindowForModel() +
getAutoCompactThreshold(), which derive all budgets from one model-aware
context length.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# -- Master setting ----------------------------------------------------------

def _resolve_context_length() -> int:
    """Resolve the master context length from env or default."""
    val = os.getenv("NOVEL_AGENT_CONTEXT_LENGTH", "")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return 200_000


@dataclass(frozen=True)
class ContextBudget:
    """All context budgets derived from a single master context length.

    Every field is a fraction of ``total``.  Change the env var
    ``NOVEL_AGENT_CONTEXT_LENGTH`` and every number in the system
    scales with it.
    """

    total: int = field(default_factory=_resolve_context_length)

    # -- Compression -----------------------------------------------------------

    @property
    def compress_threshold(self) -> int:
        """Token count at which auto-compression fires."""
        return int(self.total * 0.70)

    @property
    def tail_token_budget(self) -> int:
        """Tokens of recent context protected from compression (tail)."""
        return int(self.total * 0.25)

    @property
    def min_summary_tokens(self) -> int:
        """Minimum tokens for a compression summary output."""
        return max(800, int(self.total * 0.004))

    @property
    def summary_ceiling(self) -> int:
        """Absolute ceiling for compression summary tokens."""
        return int(self.total * 0.04)

    @property
    def summary_ratio(self) -> float:
        """Fraction of compressed content to allocate for summary."""
        return 0.20

    # -- Novel context: chapter buffers ---------------------------------------

    @property
    def chapter_ending_chars(self) -> int:
        """Chars of previous chapter ending to include as context anchor."""
        return int(self.total * 0.015)  # 3000 @ 200k

    @property
    def book_framing_chars(self) -> int:
        """Chars of outline book-level framing to include."""
        return int(self.total * 0.005)  # 1000 @ 200k

    @property
    def volume_context_chars(self) -> int:
        """Chars of current volume/arc context from outline."""
        return int(self.total * 0.008)  # 1600 @ 200k

    @property
    def chapter_entry_chars(self) -> int:
        """Chars per adjacent chapter entry in outline context."""
        return int(self.total * 0.004)  # 800 @ 200k

    @property
    def style_anchor_chars(self) -> int:
        """Chars of previous chapter opening for style anchoring."""
        return int(self.total * 0.002)  # 400 @ 200k

    # -- Compression internals ------------------------------------------------

    @property
    def max_tool_result_chars(self) -> int:
        """Max chars of a tool result before truncation in summarizer input."""
        return int(self.total * 0.025)  # 5000 @ 200k

    @property
    def tool_result_head_chars(self) -> int:
        """Chars to keep from head of truncated tool result."""
        return int(self.total * 0.005)  # 1000 @ 200k

    @property
    def tool_result_tail_chars(self) -> int:
        """Chars to keep from tail of truncated tool result."""
        return int(self.total * 0.005)  # 1000 @ 200k

    @property
    def serialized_turn_chars(self) -> int:
        """Max chars per serialized turn in summarizer input."""
        return int(self.total * 0.01)  # 2000 @ 200k

    @property
    def serialized_block_chars(self) -> int:
        """Max chars per content block in serialized summarizer input."""
        return int(self.total * 0.0025)  # 500 @ 200k

    # -- Conversation loop ----------------------------------------------------

    @property
    def history_tail_tokens(self) -> int:
        """Token budget for preserved tail of conversation_history."""
        return int(self.total * 0.50)  # 100000 @ 200k

    # -- Convenience ----------------------------------------------------------

    def describe(self) -> str:
        """Human-readable summary of all budgets."""
        lines = [f"Context budget ({self.total:,} tokens total):"]
        for name in sorted(dir(self.__class__)):
            if name.startswith("_") or name in ("total", "describe"):
                continue
            prop = getattr(self.__class__, name, None)
            if isinstance(prop, property):
                val = getattr(self, name)
                if isinstance(val, float):
                    lines.append(f"  {name}: {val:.1%}")
                else:
                    lines.append(f"  {name}: {val:,}")
        return "\n".join(lines)


# Module-level singleton — import this everywhere
budget = ContextBudget()
