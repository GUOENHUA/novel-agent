"""Central context budget configuration.

ALL hardcoded numbers derive from a single master setting:
  NOVEL_AGENT_CONTEXT_LENGTH (env var, default 200_000)

Every budget is a percentage of the master value.  Change the env var
and everything scales — no magic numbers scattered across files.

Token budget allocation (sums to a coherent picture @ 200k):

  ┌─────────────────────────────────────────────────┐
  │              TOTAL: 200,000 tokens               │
  ├─────────────────────────────────────────────────┤
  │  System prompt (stable):       ~15k  (7.5%)     │
  │  Novel context (dynamic):      ~10k  (5%)       │
  │  Conversation working set:     ~80k  (40%)      │
  │  Compression fires at:         160k  (80%)      │
  │  Tail protected during comp:    40k  (20%)      │
  │  Max summary output:            10k  (5%)       │
  │  Output reservation:            10k  (5%)       │
  │  Buffer/headroom:               40k  (20%)      │
  └─────────────────────────────────────────────────┘

Char budgets (token-equiv ~4 chars/token, tracked separately):

  chapter_ending: 3000 chars  (~750 tokens, 0.4%)
  book_framing:   1000 chars  (~250 tokens, 0.1%)
  volume_context: 1600 chars  (~400 tokens, 0.2%)
  chapter_entry:   800 chars  (~200 tokens, 0.1%)
  style_anchor:    400 chars  (~100 tokens, 0.05%)
  ─────────────────────────────────────────
  Context injection total:  ~1700 tokens  (0.85%)

Borrowed pattern from Claude Code's getContextWindowForModel() +
getAutoCompactThreshold(), which derive all budgets from one model-aware
context length.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


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

    Categories:
      compression  — when to fire, what to protect, max summary output
      injection   — chars of novel context injected per-turn
      internals   — truncation limits for summarizer serialization
    """

    total: int = field(default_factory=_resolve_context_length)

    # =========================================================================
    # Compression lifecycle (operate on the total token window)
    # =========================================================================

    @property
    def compress_threshold(self) -> int:
        """Fire auto-compression at 80% of context window."""
        return int(self.total * 0.80)   # 160k @ 200k

    @property
    def tail_token_budget(self) -> int:
        """Protect 20% of context as recent tail during compression."""
        return int(self.total * 0.20)   #  40k @ 200k

    @property
    def history_tail_tokens(self) -> int:
        """Preserve 40% of context worth of recent history after compression."""
        return int(self.total * 0.40)   #  80k @ 200k

    @property
    def max_summary_tokens(self) -> int:
        """Cap compression summary at 5% of context (output, not input)."""
        return int(self.total * 0.05)   #  10k @ 200k

    @property
    def min_summary_tokens(self) -> int:
        """Floor for compression summary (below this, skip)."""
        return max(500, int(self.total * 0.005))  # 1000 @ 200k, never below 500

    @property
    def summary_ceiling(self) -> int:
        """Alias for max_summary_tokens — kept for backward compat."""
        return self.max_summary_tokens

    @property
    def summary_ratio(self) -> float:
        """Fraction of compressed content to allocate for summary output."""
        return 0.20

    # =========================================================================
    # Context injection (char budgets for build_novel_context)
    # =========================================================================

    @property
    def chapter_ending_chars(self) -> int:
        """Prev chapter ending to anchor continuity."""
        return int(self.total * 0.015)   # 3000 @ 200k

    @property
    def book_framing_chars(self) -> int:
        """Outline book-level framing (theme, arc, ending vision)."""
        return int(self.total * 0.005)   # 1000 @ 200k

    @property
    def volume_context_chars(self) -> int:
        """Current volume/arc context from outline."""
        return int(self.total * 0.008)   # 1600 @ 200k

    @property
    def chapter_entry_chars(self) -> int:
        """Per-chapter outline entry (adjacent chapters)."""
        return int(self.total * 0.004)   #  800 @ 200k

    @property
    def style_anchor_chars(self) -> int:
        """Prev chapter opening for style/voice anchoring."""
        return int(self.total * 0.002)   #  400 @ 200k

    # =========================================================================
    # Summarizer internals (truncation limits during serialization)
    # =========================================================================

    @property
    def max_tool_result_chars(self) -> int:
        """Truncate tool results larger than this in summarizer input."""
        return int(self.total * 0.025)   # 5000 @ 200k

    @property
    def tool_result_head_chars(self) -> int:
        """Chars to keep from head of a truncated tool result."""
        return int(self.total * 0.005)   # 1000 @ 200k

    @property
    def tool_result_tail_chars(self) -> int:
        """Chars to keep from tail of a truncated tool result."""
        return int(self.total * 0.005)   # 1000 @ 200k

    @property
    def serialized_turn_chars(self) -> int:
        """Max chars per serialized turn in summarizer input."""
        return int(self.total * 0.01)    # 2000 @ 200k

    @property
    def serialized_block_chars(self) -> int:
        """Max chars per content block in serialized summarizer input."""
        return int(self.total * 0.0025)  #  500 @ 200k

    # =========================================================================
    # Convenience
    # =========================================================================

    def describe(self, *, verbose: bool = False) -> str:
        """Human-readable budget summary.

        Set verbose=True for all derived values; default shows key numbers.
        """
        lines = [
            f"Context budget ({self.total:,} tokens total):",
            f"",
            f"  Compression: fire at {self.compress_threshold:,} (80%), "
            f"protect {self.tail_token_budget:,} tail (20%), "
            f"max summary {self.max_summary_tokens:,} (5%)",
            f"  History:     preserve {self.history_tail_tokens:,} tokens after compression (40%)",
            f"  Injection:   chapter ending {self.chapter_ending_chars:,} chars, "
            f"book framing {self.book_framing_chars:,}, "
            f"volume {self.volume_context_chars:,}, "
            f"chapter entry {self.chapter_entry_chars:,}",
        ]
        if verbose:
            for name in sorted(dir(self.__class__)):
                if name.startswith("_") or name in ("total", "describe"):
                    continue
                prop = getattr(self.__class__, name, None)
                if isinstance(prop, property):
                    val = getattr(self, name)
                    if isinstance(val, float):
                        lines.append(f"  {name}: {val:.2%}")
                    else:
                        lines.append(f"  {name}: {val:,}")
        return "\n".join(lines)


# Module-level singleton — import this everywhere
budget = ContextBudget()
