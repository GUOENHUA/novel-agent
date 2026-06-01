"""Auto pipeline — Plan+Execute pattern with evaluate feedback loop.

The auto mode implements Plan+Execute+Evaluate:
  1. Plan: load outline, determine what to write
  2. Execute: write_chapter → settle_state (per chapter)
  3. Evaluate: slop_check → consistency_check → keep/retry

Interruptible via agent.interrupted flag (Ctrl+C → back to conversational).

Borrows patterns from autonovel `run_pipeline.py`.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from novel_agent.agent import AIAgent
from novel_agent.utils.constants import MAX_RETRY_ATTEMPTS, DRAFT_PASS_THRESHOLD

logger = logging.getLogger(__name__)


class AutoPipeline:
    """Automatic chapter generation engine.

    Wraps autonovel-style Plan→Execute→Evaluate loop with interrupt handling.
    """

    def __init__(self, agent: AIAgent):
        self.agent = agent
        self.results: list[dict[str, Any]] = []

    def run(
        self,
        start_chapter: int = 1,
        count: int = 1,
        words_per_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run the auto pipeline for N chapters.

        Args:
            start_chapter: First chapter number to write.
            count: Number of chapters to generate.
            words_per_chapter: Target word count per chapter (defaults to agent setting).

        Returns:
            List of result dicts per chapter.
        """
        words = words_per_chapter or self.agent.chapter_words
        self.results = []

        print(f"\n  📖 自动模式: 从第{start_chapter}章开始，生成 {count} 章，每章 ~{words} 字")
        print(f"  (Ctrl+C 可随时中断，返回对话模式)\n")

        # Setup signal handler for graceful interrupt
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._interrupt_handler)

        try:
            for ch in range(start_chapter, start_chapter + count):
                if self.agent.interrupted:
                    print(f"\n  ⏸️  自动模式已暂停 (已完成 {ch - start_chapter}/{count} 章)")
                    break

                result = self._write_chapter_with_retry(ch, words)
                self.results.append(result)

                if result["success"]:
                    print(f"  第{ch}章 ✅ {result['word_count']}字 | slop {result['slop_score']:.1f}")
                else:
                    print(f"  第{ch}章 ⚠️  重试{result['attempts']}次后仍未通过")

            # Summary
            completed = [r for r in self.results if r["success"]]
            total_words = sum(r["word_count"] for r in completed)
            print(f"\n  ✅ {len(completed)}/{len(self.results)} 章完成，总计 {total_words} 字")

            warnings = [r for r in completed if r.get("slop_warnings")]
            if warnings:
                print(f"  ⚠️  {len(warnings)} 章有 slop 警告，建议人工复查")

        finally:
            signal.signal(signal.SIGINT, original_handler)
            self.agent.interrupted = False

        return self.results

    def _write_chapter_with_retry(self, chapter_num: int, words: int) -> dict[str, Any]:
        """Write one chapter with slop-check retry loop.

        This is the inner Plan+Execute+Evaluate cycle per chapter.
        """
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            # Phase 1: Write (or rewrite on retry)
            chapter_content = self._write_chapter(chapter_num, words, attempt)

            # Phase 2: Evaluate — slop check
            slop_score, slop_warnings = self._check_slop(chapter_content)

            if slop_score >= DRAFT_PASS_THRESHOLD:
                # Phase 3: Settle state
                self._settle_state(chapter_num, chapter_content)

                return {
                    "chapter": chapter_num,
                    "success": True,
                    "word_count": len(chapter_content),
                    "slop_score": slop_score,
                    "slop_warnings": slop_warnings,
                    "attempts": attempt,
                }
            else:
                logger.info(
                    "Chapter %d attempt %d: slop %.1f < %.1f, retrying",
                    chapter_num, attempt, slop_score, DRAFT_PASS_THRESHOLD,
                )

        # All retries exhausted
        return {
            "chapter": chapter_num,
            "success": False,
            "word_count": 0,
            "slop_score": 0,
            "slop_warnings": [],
            "attempts": MAX_RETRY_ATTEMPTS,
        }

    # -- Phase implementations (stubs for now, real logic in Phase 4) ----------

    def _write_chapter(self, chapter_num: int, words: int, attempt: int) -> str:
        """Placeholder: call write_chapter tool."""
        # TODO Phase 4: integrate with chapter_tool.py
        content = (
            f"[第{chapter_num}章内容占位 — 第{attempt}次尝试]\n"
            f"目标字数: {words}\n"
        )
        return content

    def _check_slop(self, content: str) -> tuple[float, list[str]]:
        """Placeholder: run slop checker."""
        # TODO Phase 4: integrate with slop_checker.py
        return 10.0, []

    def _settle_state(self, chapter_num: int, content: str) -> None:
        """Placeholder: extract facts, update truth files and hook ledger."""
        # TODO Phase 3: integrate with state/ module
        pass

    @staticmethod
    def _interrupt_handler(signum, frame):
        """Handle Ctrl+C gracefully."""
        print("\n\n  ⏸️  收到中断信号，完成当前章节后切换回对话模式...")
        # The agent.interrupted flag is checked at the top of each chapter loop
