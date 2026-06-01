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

        if count > 10:
            estimated_tokens = count * words * 1.5
            print(f"\n  ⚠️  即将生成 {count} 章，预计消耗 ~{estimated_tokens:,.0f} tokens")
            confirm = input("  确认继续? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                print("  已取消")
                return []

        print(f"\n  自动模式: 从第{start_chapter}章开始，生成 {count} 章，每章 ~{words} 字")
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

    # -- Phase implementations --------------------------------------------------

    def _write_chapter(self, chapter_num: int, words: int, attempt: int) -> str:
        """Write a chapter by calling the LLM with novel context."""
        novel_context = self.agent.build_novel_context(f"写第{chapter_num}章")
        directive = (
            f"{novel_context}\n\n---\n\n"
            f"请写第{chapter_num}章的完整正文。目标{words}字左右。\n"
            f"只输出章节正文，不要解释，不要前言，不要后记。\n"
            f"开头直接进入场景，结尾留钩子。"
        )
        if attempt > 1:
            directive += f"\n\n（这是第{attempt}次重试，请确保质量。）"

        resp = self.agent.call_llm(
            messages=[{"role": "user", "content": directive}],
            max_tokens=words * 3,
            temperature=0.8,
        )
        return self.agent.extract_text(resp.content)

    def _check_slop(self, content: str) -> tuple[float, list[str]]:
        """Run mechanical slop check."""
        from novel_agent.tools.slop_checker import mechanical_scan
        result = mechanical_scan(content)
        warnings = [h["match"] for h in result.get("tier1_hits", [])]
        warnings += [h["match"] for h in result.get("fiction_hits", [])]
        return result["score"], warnings

    def _settle_state(self, chapter_num: int, content: str) -> None:
        """Save chapter to disk and update state."""
        # Save chapter file
        chapter_path = self.agent.chapters_dir / f"ch_{chapter_num:02d}.md"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(content, encoding="utf-8")

        # Update novel state
        state = self.agent.truth_files.load_state()
        state.current_chapter = chapter_num + 1
        self.agent.truth_files.save_state(state)

    @staticmethod
    def _interrupt_handler(signum, frame):
        """Handle Ctrl+C gracefully."""
        print("\n\n  ⏸️  收到中断信号，完成当前章节后切换回对话模式...")
        # The agent.interrupted flag is checked at the top of each chapter loop
