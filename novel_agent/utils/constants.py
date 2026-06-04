"""Shared constants for novel-agent."""

# Default Anthropic models
DEFAULT_WRITER_MODEL = "deepseek-v4-pro[1m]"
DEFAULT_JUDGE_MODEL = "deepseek-v4-flash[1m]"

# Writing defaults
DEFAULT_CHAPTER_WORDS = 3000
DEFAULT_TOTAL_CHAPTERS = 24
DEFAULT_TOTAL_WORDS = 80000
MAX_RETRY_ATTEMPTS = 3

# Context budgets (characters, not tokens) — derived from master setting.
# Import from novel_agent.context.config instead of using these directly.
from novel_agent.context.config import budget as _budget
CRAFT_CONTEXT_BUDGET = int(_budget.total * 0.10)     #  20k @ 200k (craft writing reference)
MEMORY_CONTEXT_BUDGET = int(_budget.total * 0.04)    #   8k @ 200k (memory type descriptions)
TRUTH_FILES_BUDGET = int(_budget.total * 0.075)      #  15k @ 200k (hook ledger + summaries)

# Slop checker thresholds
SLOP_TIER1_PENALTY = 3       # Per banned word
SLOP_TIER2_WARNING = 2       # Per cluster of 3+
SLOP_PASS_THRESHOLD = 7.0    # Score >= this = pass

# Chapter evaluation thresholds
DRAFT_PASS_THRESHOLD = 6.0
FOUNDATION_PASS_THRESHOLD = 7.5
