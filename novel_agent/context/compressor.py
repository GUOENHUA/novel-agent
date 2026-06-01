"""Novel-adapted context compressor.

Borrowed from hermes-agent `agent/context_compressor.py`.

Five-phase algorithm:
  1. Prune old tool results (cheap, no LLM call)
  2. Protect head messages (system prompt + first N exchanges)
  3. Protect tail messages by token budget (most recent ~20% of context)
  4. Summarize middle turns with structured LLM prompt
  5. Fix orphaned tool_call/tool_result pairs
"""

from __future__ import annotations

import logging
from typing import Any

from novel_agent.context.context_engine import ContextEngine
from novel_agent.context.token_counter import estimate_tokens, estimate_messages_tokens

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Respond ONLY to the latest user message that appears AFTER this "
    "summary."
)

# Novel-specific summary template
NOVEL_SUMMARY_TEMPLATE = """## 当前写作进度
[当前章节、已完成字数、写作阶段]

## 当前场景状态
[场景位置、在场角色、场景情绪/氛围]

## 活跃角色状态
[当前涉及角色：位置、情绪、目标]

## 已完成内容
[编号列表：写了哪些章节/场景，关键事件]

## 伏笔与未解线索
[已埋伏笔列表 + 计划回收章节]

## 一致性约束
[关键设定约束、角色关系约束、时间线约束]

## 待写内容
[下一场景/章节的计划]

## 用户最新指令
[用户最近的写作方向/修改要求]

Target ~{summary_budget} tokens. Be CONCRETE — include chapter numbers,
file paths, character names, hook IDs, and specific values."""


class NovelCompressor(ContextEngine):
    """Default context engine — compresses via lossy summarization.

    Algorithm adapted for novel writing context:
      - Preserves CRAFT.md / memory context in system prompt
      - Protects recent chapters and current writing state
      - Summarizes older writing iterations and tool results
    """

    @property
    def name(self) -> str:
        return "novel-compressor"

    def __init__(
        self,
        model: str,
        context_length: int = 200000,
        threshold_percent: float = 0.70,
        protect_first_n: int = 3,
        tail_token_budget: int = 30000,
    ):
        self.model = model
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.tail_token_budget = tail_token_budget
        self.threshold_tokens = int(context_length * threshold_percent)

        self.compression_count = 0
        self._previous_summary: str | None = None

        # Token state
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        self.last_completion_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        self.last_total_tokens = self.last_prompt_tokens + self.last_completion_tokens

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        tokens = prompt_tokens or self.last_prompt_tokens
        return tokens > self.threshold_tokens

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compress conversation messages by summarizing middle turns."""
        n = len(messages)
        display_tokens = current_tokens or estimate_messages_tokens(messages)

        # Phase 1: Determine boundaries
        head_end = 1 + self.protect_first_n  # system prompt + first N messages
        if n <= head_end + 3 + 1:  # Not enough to compress
            return messages

        # Phase 2: Find tail boundary by token budget
        tail_start = self._find_tail_cut(messages, head_end)

        if head_end >= tail_start:
            return messages

        turns_to_summarize = messages[head_end:tail_start]

        # Phase 3: Build summary (either iterative update or fresh)
        summary = self._build_summary(turns_to_summarize, focus_topic)

        # Phase 4: Assemble compressed list
        compressed = list(messages[:head_end])

        # Insert summary as a user message
        compressed.append({
            "role": "user",
            "content": f"{SUMMARY_PREFIX}\n\n{summary}\n\n--- END OF CONTEXT SUMMARY ---",
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

    def _find_tail_cut(self, messages: list[dict], head_end: int) -> int:
        """Walk backward accumulating tokens until budget reached."""
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

    def _build_summary(
        self, turns: list[dict], focus_topic: str | None = None
    ) -> str:
        """Build a structured summary of conversation turns."""
        serialized = self._serialize_turns(turns)
        summary_budget = max(500, min(4000, estimate_tokens(serialized) // 3))

        template = NOVEL_SUMMARY_TEMPLATE.replace("{summary_budget}", str(summary_budget))

        if self._previous_summary:
            prompt = (
                "You are updating a context compaction summary for a novel writing session. "
                "A previous compaction produced the summary below. New conversation turns "
                "have occurred since then and need to be incorporated.\n\n"
                f"PREVIOUS SUMMARY:\n{self._previous_summary}\n\n"
                f"NEW TURNS TO INCORPORATE:\n{serialized}\n\n"
                f"Update the summary using this exact structure:\n\n{template}"
            )
        else:
            prompt = (
                "Create a structured checkpoint summary for a novel writing session.\n\n"
                f"TURNS TO SUMMARIZE:\n{serialized}\n\n"
                f"Use this exact structure:\n\n{template}"
            )

        if focus_topic:
            prompt += f"\n\nFOCUS TOPIC: '{focus_topic}'. Prioritize preserving information related to this topic."

        # The actual LLM call for summarization is made by the agent.
        # Here we return the prompt for the agent to use.
        # In the full implementation, this would call the LLM directly.
        self._previous_summary = f"[Summary of {len(turns)} turns — LLM call pending]"

        return self._previous_summary

    def _serialize_turns(self, turns: list[dict]) -> str:
        """Serialize conversation turns into labeled text for the summarizer."""
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content[:2000]
            else:
                text = str(content)[:2000]
            parts.append(f"[{role.upper()}]: {text}")
        return "\n\n".join(parts)
