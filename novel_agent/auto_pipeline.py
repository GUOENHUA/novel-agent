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

from rich.console import Console

from novel_agent.agent import AIAgent
from novel_agent.utils.constants import MAX_RETRY_ATTEMPTS, DRAFT_PASS_THRESHOLD

logger = logging.getLogger(__name__)
console = Console(highlight=False)


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
            print(f"\n  WARNING: {count} chapters, ~{estimated_tokens:,.0f} tokens estimated")
            confirm = input("  Continue? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                print("  Cancelled")
                return []

        print(f"\n  Auto mode: ch{start_chapter}-{start_chapter + count - 1}, {count} chapters, ~{words} words each")
        print(f"  (Ctrl+C to interrupt)\n")

        # Setup signal handler for graceful interrupt
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._interrupt_handler)

        try:
            for ch in range(start_chapter, start_chapter + count):
                if self.agent.interrupted:
                    print(f"\n  PAUSED  自动模式已暂停 (已完成 {ch - start_chapter}/{count} 章)")
                    break

                result = self._write_chapter_with_retry(ch, words)
                self.results.append(result)
                progress = f"{ch - start_chapter + 1}/{count}"

                if result["success"]:
                    hooks_info = result.get("hooks_info", "")
                    print(f"  [{progress}] ch{ch} OK ({result['word_count']} chars, slop {result['slop_score']:.0f}{hooks_info})")
                else:
                    print(f"  [{progress}] ch{ch} FAILED after {result['attempts']} retries")

            # Summary
            completed = [r for r in self.results if r["success"]]
            total_words = sum(r["word_count"] for r in completed)
            print(f"\n  OK {len(completed)}/{len(self.results)} 章完成，总计 {total_words} 字")

            warnings = [r for r in completed if r.get("slop_warnings")]
            if warnings:
                print(f"  WARN️  {len(warnings)} 章有 slop 警告，建议人工复查")

        finally:
            signal.signal(signal.SIGINT, original_handler)
            self.agent.interrupted = False

        return self.results

    def _write_chapter_with_retry(self, chapter_num: int, words: int) -> dict[str, Any]:
        """Write one chapter with slop-check retry loop."""
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            chapter_content = self._write_chapter(chapter_num, words, attempt)
            slop_score, slop_warnings = self._check_slop(chapter_content)

            if slop_score >= DRAFT_PASS_THRESHOLD:
                # Count hooks before/after to show delta
                hooks_before = len(self.agent.hook_ledger.get_active())
                self._settle_state(chapter_num, chapter_content)
                hooks_after = len(self.agent.hook_ledger.get_active())
                new_hooks = max(0, hooks_after - hooks_before)

                return {
                    "chapter": chapter_num,
                    "success": True,
                    "word_count": len(chapter_content),
                    "slop_score": slop_score,
                    "slop_warnings": slop_warnings,
                    "attempts": attempt,
                    "hooks_info": f", hooks +{new_hooks}" if new_hooks else "",
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

        label = f"Writing ch{chapter_num}" + (f" (retry {attempt})" if attempt > 1 else "")
        with console.status(f"[bold yellow]{label}...", spinner="dots") as status:
            t0 = time.time()
            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": directive}],
                max_tokens=words * 3,
                temperature=0.8,
            )
            elapsed = time.time() - t0
            content = self.agent.extract_text(resp.content)
            status.update(
                f"[bold yellow]{label}...[/bold yellow] "
                f"({elapsed:.1f}s, {resp.usage.input_tokens}+{resp.usage.output_tokens} tk, {len(content)} chars)"
            )
        return content

    def _check_slop(self, content: str) -> tuple[float, list[str]]:
        """Run mechanical slop check."""
        from novel_agent.tools.slop_checker import mechanical_scan
        result = mechanical_scan(content)
        warnings = [h["match"] for h in result.get("tier1_hits", [])]
        warnings += [h["match"] for h in result.get("fiction_hits", [])]
        return result["score"], warnings

    def _settle_state(self, chapter_num: int, content: str) -> None:
        """Save chapter to disk, extract hooks, update state."""
        title = self._generate_title(content, chapter_num)

        chapter_path = self.agent.chapters_dir / f"ch_{chapter_num:02d}.md"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        final = f"# 第{chapter_num}章: {title}\n\n{content}"
        chapter_path.write_text(final, encoding="utf-8")

        # Extract hooks via settlement (low-temp LLM call)
        self._extract_hooks(chapter_num, content)

        # Update novel state
        state = self.agent.truth_files.load_state()
        state.current_chapter = chapter_num + 1
        self.agent.truth_files.save_state(state)

    def _extract_hooks(self, chapter_num: int, content: str) -> None:
        """Extract hook changes from chapter content and update hook ledger."""
        import json
        try:
            directive = (
                f"从以下章节正文中提取伏笔变更。返回纯JSON（不要markdown代码块）：\n"
                f'{{"planted":[{{"id":"hook-xxx","desc":"...","type":"direct|symbolic|dialogue|action|naming"}}],'
                f'"mentioned":["hook-id"],"resolved":["hook-id"]}}\n\n'
                f"已存在的伏笔ID: {[h.id for h in self.agent.hook_ledger.load_all()]}\n"
                f"新ID格式: hook-{chapter_num:02d}-序号（如hook-{chapter_num:02d}-1）\n\n"
                f"{content[:5000]}"
            )
            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": directive}],
                max_tokens=500, temperature=0.3,
            )
            raw = self.agent.extract_text(resp.content).strip()
            # Strip markdown code fences if present
            for fence in ("```json", "```"):
                raw = raw.replace(fence, "").strip()
            data = json.loads(raw)

            for h in data.get("planted", []):
                self.agent.hook_ledger.upsert(
                    hook_id=h["id"], description=h["desc"],
                    planted_chapter=chapter_num, hook_type=h.get("type", "direct"),
                )
            for hid in data.get("mentioned", []):
                self.agent.hook_ledger.mention(hid, chapter_num)
            for hid in data.get("resolved", []):
                self.agent.hook_ledger.resolve(hid, chapter_num)

            planted = len(data.get("planted", []))
            resolved = len(data.get("resolved", []))
            if planted or resolved:
                logger.info("Ch%d hooks: +%d planted, %d resolved", chapter_num, planted, resolved)
        except Exception:
            logger.warning("Hook extraction failed for ch%d", chapter_num, exc_info=True)

    def _generate_title(self, content: str, chapter_num: int) -> str:
        """Generate a chapter title from the content using a fast LLM call."""
        try:
            preview = content[:1200]
            resp = self.agent.call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Based on this chapter opening, generate a short Chinese chapter title "
                        f"(4-8 characters, poetic, no quotes). Return ONLY the title, nothing else.\n\n"
                        f"{preview}"
                    ),
                }],
                max_tokens=200,  # DeepSeek thinking blocks need headroom
                temperature=0.3,
            )
            raw = self.agent.extract_text(resp.content).strip()
            # DeepSeek V4 may put title at end of thinking block — take last line
            title = raw.split("\n")[-1].strip().strip("《》\"'#*。，！？ ")
            if 2 <= len(title) <= 20:
                return title
        except Exception:
            pass
        return f"第{chapter_num}章"

    @staticmethod
    def _interrupt_handler(signum, frame):
        """Handle Ctrl+C gracefully."""
        print("\n\n  PAUSED  收到中断信号，完成当前章节后切换回对话模式...")
        # The agent.interrupted flag is checked at the top of each chapter loop
