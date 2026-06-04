"""Novel-adapted context compressor with real LLM summarization.

Borrowed from hermes-agent `agent/context_compressor.py` and
claude-code `src/services/compact/compact.ts`.

Algorithm:
  1. Prune old tool results (cheap, no LLM call)
  2. Protect head messages (system prompt + first N exchanges)
  3. Protect tail messages by token budget
  4. Summarize middle turns with structured LLM prompt
  5. Iterative update on re-compression (merge with previous summary)
  6. Deterministic fallback when LLM summarizer is unavailable
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

from novel_agent.context.context_engine import ContextEngine
from novel_agent.context.token_counter import estimate_tokens, estimate_messages_tokens

logger = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Respond ONLY to the latest user message that appears AFTER this "
    "summary."
)

# Proportion of compressed content to allocate for summary
_SUMMARY_RATIO = 0.20
# Absolute ceiling for summary tokens
_SUMMARY_TOKENS_CEILING = 8_000
# Minimum summary tokens
_MIN_SUMMARY_TOKENS = 800
# Chars-per-token rough estimate
_CHARS_PER_TOKEN = 4

# Circuit breaker: stop retrying after N consecutive failures
_MAX_CONSECUTIVE_COMPRESS_FAILURES = 3


# -- Novel-specific summary template -----------------------------------------

NOVEL_SUMMARY_TEMPLATE = """## Active Task
[用户最新未完成的指令/问题 — 逐字保留。包括：任务分配、待回答的问题、待确认的决定。
如果用户刚问了一个问题，那个问题就是 Active Task。不要写"None"除非对话已完全结束。
如果用户的最新消息是反转信号（停止/撤销/换个方向/只要验证），写入该反转信号并丢弃被取消的任务。]

## Goal
[用户想要完成什么小说创作目标]

## Writing Progress
[当前章节号、已完成字数、写作阶段（大纲/角色/正文/修订）]

## Scene State
[当前场景：位置、在场角色、场景情绪/氛围、时间线位置]

## Active Characters
[当前活跃角色：姓名、位置、情绪状态、当前目标 — 只列出现在还在场景中的]

## Completed Actions
[编号列表：已完成的写作/修改操作 — 包含工具名和结果。格式：
1. 第3章已写 2800字 — 保存到 ch_003_下山.md [tool: write_chapter]
2. 角色"苏凝"已创建 — 青云宗内门巡察使 [tool: memory add]
具体一点 — 包含章节号、文件名、角色名、具体数值]

## Hook Status
[活跃伏笔列表，带 ID 和计划回收章节。标注逾期/即将到期的伏笔]

## Key Decisions
[重要的创作决定和原因。例如：选了限制视角而非全知视角，因为...]

## Style Anchors
[已验证的写作风格偏好 — 叙事语气、节奏、修辞习惯。从 style memory 提取]

## Constraints
[关键设定约束、角色关系约束、时间线约束 — 不能违背的规则]

## Remaining Work
[接下来要写什么 — 作为上下文参考，不是指令]

## Critical Context
[任何需要精确保留的具体数值、文件路径、角色名、伏笔ID。不要包含 API 密钥或密码]

Target ~{summary_budget} tokens. Be CONCRETE — include chapter numbers,
file paths, character names, hook IDs, and specific values.
Write only the summary body — no preamble or prefix."""


# -- Pruning helpers ---------------------------------------------------------

_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"


def _content_length_for_budget(raw_content: Any) -> int:
    """Return effective char-length for token budgeting."""
    if isinstance(raw_content, str):
        return len(raw_content)
    if not isinstance(raw_content, list):
        return len(str(raw_content or ""))
    total = 0
    for part in raw_content:
        if isinstance(part, str):
            total += len(part)
        elif isinstance(part, dict):
            total += len(part.get("text", "") or "")
    return total


class NovelCompressor(ContextEngine):
    """Compresses novel-writing conversation context via structured summarization.

    Uses a pluggable LLM callable for summarization — the agent injects
    this after construction so the compressor stays provider-agnostic.
    """

    @property
    def name(self) -> str:
        return "novel-compressor"

    def __init__(
        self,
        model: str = "deepseek-v4-pro[1m]",
        context_length: int = 200000,
        threshold_percent: float = 0.70,
        protect_first_n: int = 3,
        tail_token_budget: int = 150000,
    ):
        self.model = model
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.tail_token_budget = tail_token_budget
        self.threshold_tokens = int(context_length * threshold_percent)

        self.compression_count = 0
        self._previous_summary: str | None = None

        # Pluggable LLM callable: (system_prompt, user_prompt) -> str | None
        self._summarizer: Callable[[str, str], Optional[str]] | None = None

        # Circuit breaker state
        self._consecutive_failures = 0

        # Token state
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0

        # Diagnostics
        self._last_summary_error: str | None = None
        self._last_compress_aborted: bool = False

    def set_summarizer(self, fn: Callable[[str, str], Optional[str]]) -> None:
        """Inject a summarizer callable: fn(system_prompt, user_prompt) -> str|None."""
        self._summarizer = fn

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        self.last_completion_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        self.last_total_tokens = self.last_prompt_tokens + self.last_completion_tokens

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        tokens = prompt_tokens or self.last_prompt_tokens
        if self._consecutive_failures >= _MAX_CONSECUTIVE_COMPRESS_FAILURES:
            return False
        return tokens > self.threshold_tokens

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        force: bool = False,
        focus_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compress conversation messages by summarizing middle turns.

        Args:
            messages: Current message history.
            current_tokens: Pre-compression token estimate.
            force: If True, bypass circuit breaker.
            focus_topic: Optional focus string for guided compression —
                summariser prioritises preserving related information.
        """
        if force:
            self._consecutive_failures = 0

        n = len(messages)
        display_tokens = current_tokens or estimate_messages_tokens(messages)

        # Phase 1: Determine boundaries
        head_end = self._protect_head_size(messages)
        if n <= head_end + 3 + 1:
            return messages

        # Phase 2: Find tail boundary by token budget
        tail_start = self._find_tail_cut(messages, head_end)

        if head_end >= tail_start:
            return messages

        turns_to_summarize = messages[head_end:tail_start]

        # Phase 3: Prune old tool results in the summarization window
        turns_to_summarize = self._prune_tool_results(turns_to_summarize)

        # Phase 4: Generate structured summary
        summary = self._generate_summary(turns_to_summarize, focus_topic)

        # Phase 5: Assemble compressed list
        compressed = list(messages[:head_end])

        if summary:
            compressed.append({
                "role": "user",
                "content": f"{SUMMARY_PREFIX}\n\n{summary}\n\n--- END OF CONTEXT SUMMARY ---",
            })
        else:
            # LLM summarizer failed — insert deterministic fallback
            self._consecutive_failures += 1
            self._last_summary_error = "LLM summarizer returned empty response"
            self._last_compress_aborted = True
            # Drop middle messages anyway with a placeholder
            compressed.append({
                "role": "user",
                "content": (
                    f"{SUMMARY_PREFIX}\n\n"
                    f"[{len(turns_to_summarize)} earlier turns compacted — "
                    f"LLM summary unavailable, but context space needed to be freed. "
                    f"Review the most recent messages below for current state.]\n\n"
                    f"--- END OF CONTEXT SUMMARY ---"
                ),
            })

        compressed.extend(messages[tail_start:])

        self.compression_count += 1

        new_estimate = estimate_messages_tokens(compressed)
        saved = display_tokens - new_estimate
        savings_pct = (saved / display_tokens * 100) if display_tokens > 0 else 0
        logger.info(
            "Compressed: %d → %d messages (~%d tokens saved, %.0f%%)",
            n, len(compressed), saved, savings_pct,
        )

        return compressed

    # -- Internal helpers -------------------------------------------------------

    def _protect_head_size(self, messages: list[dict]) -> int:
        """Number of messages to protect at the head."""
        return 1 + self.protect_first_n  # first user msg + N exchanges

    def _find_tail_cut(self, messages: list[dict], head_end: int) -> int:
        """Walk backward accumulating tokens until tail budget is reached."""
        n = len(messages)
        min_tail = min(3, n - head_end - 1)
        budget = self.tail_token_budget
        accumulated = 0
        cut = n

        for i in range(n - 1, head_end, -1):
            msg = messages[i]
            content = msg.get("content", "")
            msg_tokens = estimate_tokens(str(content)) + 10
            if accumulated + msg_tokens > int(budget * 1.5) and (n - i) >= min_tail:
                break
            accumulated += msg_tokens
            cut = i

        return max(cut, n - min_tail, head_end + 1)

    def _prune_tool_results(self, messages: list[dict]) -> list[dict]:
        """Replace large tool results with a placeholder in summarization input."""
        pruned = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        inner = block.get("content", "")
                        inner_str = str(inner) if not isinstance(inner, str) else inner
                        if len(inner_str) > 5000:
                            block = {**block, "content": inner_str[:500] + f"\n... [{len(inner_str) - 500} chars truncated for compression]\n" + inner_str[-500:]}
                    new_blocks.append(block)
                pruned.append({**msg, "content": new_blocks})
            else:
                pruned.append(msg)
        return pruned

    def _generate_summary(
        self,
        turns: list[dict],
        focus_topic: str | None = None,
    ) -> Optional[str]:
        """Generate a structured summary using the configured LLM summarizer.

        Returns None if summarization fails (caller falls back to placeholder).
        """
        if self._summarizer is None:
            logger.warning("No summarizer configured — cannot generate compression summary")
            return None

        serialized = self._serialize_turns(turns)
        summary_budget = max(
            _MIN_SUMMARY_TOKENS,
            min(_SUMMARY_TOKENS_CEILING, int(estimate_tokens(serialized) * _SUMMARY_RATIO)),
        )

        template = NOVEL_SUMMARY_TEMPLATE.replace("{summary_budget}", str(summary_budget))

        if self._previous_summary:
            system_prompt = (
                "You are updating a context compaction summary for a novel writing session. "
                "Preserve all existing information that is still relevant. Add new completed "
                "actions to the numbered list (continue numbering). Move resolved items to "
                "completed. Update Active Task to reflect the user's most recent unfulfilled "
                "input. Remove information only if clearly obsolete. "
                "CRITICAL: Update '## Active Task' with the user's latest unfulfilled request."
            )
            user_prompt = (
                f"PREVIOUS SUMMARY:\n{self._previous_summary}\n\n"
                f"NEW TURNS TO INCORPORATE:\n{serialized}\n\n"
                f"Update the summary using this exact structure:\n\n{template}"
            )
        else:
            system_prompt = (
                "Create a structured checkpoint summary for a novel writing session. "
                "Preserve enough detail for continuity without re-reading the original "
                "turns. Be concrete — include chapter numbers, character names, hook IDs."
            )
            user_prompt = (
                f"TURNS TO SUMMARIZE:\n{serialized}\n\n"
                f"Use this exact structure:\n\n{template}"
            )

        if focus_topic:
            user_prompt += (
                f"\n\nFOCUS TOPIC: '{focus_topic}'. "
                "Prioritize preserving information related to this topic. "
                "Be more aggressive about compressing everything else."
            )

        try:
            summary = self._summarizer(system_prompt, user_prompt)
            if summary:
                self._previous_summary = summary
                self._consecutive_failures = 0
                self._last_summary_error = None
                self._last_compress_aborted = False
                return summary
            else:
                logger.warning("Summarizer returned empty response")
                self._last_summary_error = "LLM summarizer returned empty response"
                return None
        except Exception as e:
            logger.exception("Summary generation failed: %s", e)
            self._last_summary_error = str(e)
            return None

    def _serialize_turns(self, turns: list[dict]) -> str:
        """Serialize conversation turns into labeled text for the summarizer."""
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content[:2000]
            elif isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", "")[:500])
                        elif block.get("type") == "tool_use":
                            texts.append(f"[tool_use: {block.get('name', '?')}]")
                        elif block.get("type") == "tool_result":
                            inner = block.get("content", "")
                            t = str(inner) if not isinstance(inner, str) else inner
                            texts.append(f"[tool_result: {t[:300]}]")
                    elif hasattr(block, "text"):
                        texts.append(block.text[:500])
                text = "\n".join(t for t in texts if t)[:2000]
            else:
                text = str(content)[:2000]
            parts.append(f"[{role.upper()}]: {text}")
        return "\n\n".join(parts)

    def clear_previous_summary(self) -> None:
        """Reset iterative summary state (e.g., on /new)."""
        self._previous_summary = None
        self._consecutive_failures = 0
        self._last_summary_error = None
        self._last_compress_aborted = False
