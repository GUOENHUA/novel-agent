"""Shared constants for novel-agent."""

# Default Anthropic models
DEFAULT_WRITER_MODEL = "deepseek-v4-pro[1m]"
DEFAULT_JUDGE_MODEL = "deepseek-v4-flash[1m]"

# Writing defaults
DEFAULT_CHAPTER_WORDS = 3000
DEFAULT_TOTAL_CHAPTERS = 24
DEFAULT_TOTAL_WORDS = 80000
MAX_RETRY_ATTEMPTS = 3

# Context budget (characters, not tokens)
CRAFT_CONTEXT_BUDGET = 20000
MEMORY_CONTEXT_BUDGET = 8000
TRUTH_FILES_BUDGET = 15000

# Slop checker thresholds
SLOP_TIER1_PENALTY = 3       # Per banned word
SLOP_TIER2_WARNING = 2       # Per cluster of 3+
SLOP_PASS_THRESHOLD = 7.0    # Score >= this = pass

# Chapter evaluation thresholds
DRAFT_PASS_THRESHOLD = 6.0
FOUNDATION_PASS_THRESHOLD = 7.5
