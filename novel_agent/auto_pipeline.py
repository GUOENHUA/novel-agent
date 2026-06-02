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
                hooks_before = len(self.agent.hook_ledger.get_active())
                summaries_before = len(self.agent.truth_files.load_summaries())
                self._settle_state(chapter_num, chapter_content)
                hooks_delta = len(self.agent.hook_ledger.get_active()) - hooks_before
                has_summary = len(self.agent.truth_files.load_summaries()) > summaries_before

                parts = []
                if hooks_delta:
                    parts.append(f"hooks {hooks_delta:+d}")
                if has_summary:
                    parts.append("summary")
                info = f", {' '.join(parts)}" if parts else ""

                return {
                    "chapter": chapter_num,
                    "success": True,
                    "word_count": len(chapter_content),
                    "slop_score": slop_score,
                    "slop_warnings": slop_warnings,
                    "attempts": attempt,
                    "hooks_info": info,
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
            f"请写第{chapter_num}章的完整正文。目标{words}字左右。\n\n"
            f"格式要求（严格遵守）：\n"
            f"- 不要输出章标题（标题会自动添加），直接开始正文\n"
            f"- 正文中禁止使用任何Markdown格式：禁止 # ## ### 标题、禁止 **加粗**、禁止 *斜体*\n"
            f"- 段落之间用空行分隔，除此之外不使用任何特殊格式\n"
            f"- 禁止在章末添加任何元注释：禁止（第一章完）、（字数：xxx）、（伏笔：xxx）等。你的正文应该是纯粹的叙事，像一本真正的书\n"
            f"- 开头直接进入场景，不要前言；结尾自然结束，不要后记\n"
            f"- 这是一段纯粹的叙事文本，像一本真正的书一样"
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
            content = self.agent.extract_text(resp.content, fallback_to_thinking=False)
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
        """Save chapter, extract hooks + summary + character changes, update state."""
        title = self._generate_title(content, chapter_num)

        # Clean formatting via LLM — more reliable than regex
        clean = self._clean_chapter_via_llm(content)

        chapter_path = self.agent.chapter_path(chapter_num, title)
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        final = f"# 第{chapter_num}章: {title}\n\n{clean}"
        chapter_path.write_text(final, encoding="utf-8")

        # Phase 2: settlement (low-temp extraction of structured data)
        settlement = self._run_settlement(chapter_num, content)

        # Update hook ledger (include scope when provided by settlement)
        for h in settlement.get("hooks_planted", []):
            self.agent.hook_ledger.upsert(
                hook_id=h["id"], description=h["desc"],
                planted_chapter=chapter_num,
                hook_type=h.get("type", "direct"),
                scope=h.get("scope", "chapter"),
            )
        for hid in settlement.get("hooks_mentioned", []):
            self.agent.hook_ledger.mention(hid, chapter_num)
        for hid in settlement.get("hooks_resolved", []):
            self.agent.hook_ledger.resolve(hid, chapter_num)

        # Update chapter summary
        if settlement.get("chapter_summary"):
            from novel_agent.state.schemas import ChapterSummary
            chars = settlement.get("characters_appearing", [])
            mood = settlement.get("mood", "neutral")
            self.agent.truth_files.add_summary(ChapterSummary(
                chapter_number=chapter_num, title=title,
                word_count=len(content), summary=settlement["chapter_summary"],
                key_events=settlement.get("key_events", []),
                characters_appearing=chars,
                hooks_planted=[h["id"] for h in settlement.get("hooks_planted", [])],
                hooks_resolved=settlement.get("hooks_resolved", []),
                mood=mood,
            ))

        # Update character states
        for name, changes in settlement.get("character_changes", {}).items():
            self.agent.truth_files.update_character(name, **changes)
            # Also save important changes to memory system
            if changes.get("important_fact"):
                self._save_to_memory("character", name, changes["important_fact"])

        # Save novel state
        state = self.agent.truth_files.load_state()
        state.current_chapter = chapter_num + 1
        self.agent.truth_files.save_state(state)

        planted = len(settlement.get("hooks_planted", []))
        resolved = len(settlement.get("hooks_resolved", []))
        chars_updated = len(settlement.get("character_changes", {}))
        logger.info(
            "Ch%d settled: +%d hooks, -%d hooks, %d chars, summary=%d chars",
            chapter_num, planted, resolved, chars_updated,
            len(settlement.get("chapter_summary", "")),
        )

    def _run_settlement(self, chapter_num: int, content: str) -> dict:
        """Run the full settlement extraction (hooks + summary + characters)."""
        import json
        try:
            existing_hooks = [h.id for h in self.agent.hook_ledger.load_all()]
            existing_chars = list(self.agent.truth_files.load_state().characters.keys())

            directive = (
                f"从以下章节正文中提取结构化信息。返回合法JSON（不要markdown，确保逗号正确，字符串内双引号用\\\"转义）：\n"
                f'{{\n'
                f'  "chapter_summary": "2-3句摘要",\n'
                f'  "key_events": ["事件1", "事件2"],\n'
                f'  "characters_appearing": ["角色名"],\n'
                f'  "mood": "tense|hopeful|tragic|mysterious|dark|neutral",\n'
                f'  "character_changes": {{\n'
                f'    "角色名": {{"location": "新位置", "emotional_state": "情绪", "goal": "目标", "important_fact": "新发现的重要事实"}}\n'
                f'  }},\n'
                f'  "hooks_planted": [{{"id": "hook-{chapter_num:02d}-1", "desc": "...", "type": "direct|symbolic|dialogue|action|naming"}}],\n'
                f'  "hooks_mentioned": ["hook-id"],\n'
                f'  "hooks_resolved": ["hook-id"]\n'
                f'}}\n\n'
                f"已有伏笔ID: {existing_hooks}\n"
                f"已有角色: {existing_chars}\n\n"
                f"{content[:6000]}"
            )
            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": directive}],
                max_tokens=800, temperature=0.3,
                extra_body={"response_format": {"type": "json_object"}},
            )
            raw = self.agent.extract_text(resp.content, fallback_to_thinking=True).strip()
            if not raw:
                return {}
            for fence in ("```json", "```"):
                raw = raw.replace(fence, "").strip()
            # Find JSON object boundaries — if no braces, settlement failed
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return {}  # No JSON in response, likely pure thinking block
            raw = raw[start:end + 1]
            # Try parsing; if broken, ask LLM to fix its own JSON
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                fix_resp = self.agent.call_llm(
                    messages=[{"role": "user", "content": (
                        f"Fix this invalid JSON. Return ONLY valid JSON, no explanation.\n"
                        f"Error: {e}\n\n{raw}"
                    )}],
                    max_tokens=800, temperature=0,
                    extra_body={"response_format": {"type": "json_object"}},
                )
                fixed = self.agent.extract_text(fix_resp.content, fallback_to_thinking=True).strip()
                fs = fixed.find("{")
                fe = fixed.rfind("}")
                if fs >= 0 and fe > fs:
                    return json.loads(fixed[fs:fe + 1])
                return {}
        except Exception:
            logger.warning("Settlement failed for ch%d", chapter_num, exc_info=True)
            return {}

    def _save_to_memory(self, mem_type: str, name: str, fact: str) -> None:
        """Save a key fact to the memory system (best-effort, non-blocking)."""
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.agent.memory_dir)
            safe_name = "".join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c in "-_")
            filename = f"char-{safe_name}.md" if mem_type == "character" else f"{mem_type}-{safe_name}.md"

            existing = store.read_memory(filename)
            if existing and fact not in existing:
                # Append fact to existing memory
                body = existing.split("---", 2)[-1].strip() if existing.count("---") >= 2 else existing.strip()
                store.write_memory(
                    filename,
                    {"name": name, "description": f"Auto-extracted facts about {name}", "type": mem_type},
                    body + "\n\n" + fact,
                )
            elif not existing:
                store.write_memory(
                    filename,
                    {"name": name, "description": f"Auto-extracted facts about {name}", "type": mem_type},
                    fact,
                )
                store.add_to_index(name, filename, fact[:120])
        except Exception:
            pass  # Best-effort, don't block the pipeline

    def _clean_chapter_via_llm(self, content: str) -> str:
        """Use LLM to clean chapter formatting instead of brittle regex."""
        try:
            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": (
                    "Clean this chapter text. Rules:\n"
                    "- Remove any chapter headings in the body (like '第一章：感应' or '# 第X章')\n"
                    "- Remove end-of-chapter meta annotations (like '(第一章完)', '(字数：3004字)', '(伏笔：...)')\n"
                    "- Remove **bold** and *italic* markdown — convert to plain text\n"
                    "- Remove --- separators\n"
                    "- Keep ALL narrative prose unchanged — don't edit the story\n"
                    "- Return ONLY the cleaned text, no explanation\n\n"
                    f"{content[:8000]}"
                )}],
                max_tokens=len(content) * 2, temperature=0.2,
            )
            return self.agent.extract_text(resp.content).strip()
        except Exception:
            return content  # Fallback: return original

    def _generate_title(self, content: str, chapter_num: int) -> str:
        """Generate a chapter title from the content using a fast LLM call."""
        try:
            preview = content[:1200]
            resp = self.agent.call_llm(
                messages=[{
                    "role": "user",
                    "content": (
                        f"为这一章起一个中文标题，4-8个汉字，富有诗意。只输出标题，不要引号、不要解释、不要英文。\n\n"
                        f"{preview}"
                    ),
                }],
                max_tokens=200,  # DeepSeek thinking blocks need headroom
                temperature=0.3,
            )
            raw = self.agent.extract_text(resp.content, fallback_to_thinking=True).strip()
            # DeepSeek V4 puts title suggestions inside thinking block
            import re
            # Find Chinese-quoted phrases: "标题" or 「标题」or 《标题》
            quoted = re.findall(r'["“]([^"”]{2,10})["”]', raw)
            bracketed = re.findall(r'[「《]([^」》]{2,10})[」》]', raw)
            all_candidates = quoted + bracketed
            # Filter out obvious non-titles (containing 的/了/是/或/可以)
            candidates = [c.strip() for c in all_candidates
                         if 3 <= len(c.strip()) <= 10
                         and not any(w in c for w in ["或", "可以", "需要", "应该", "这个", "那个"])]
            title = candidates[-1].strip() if candidates else ""
            # Fallback: search for title after keywords
            if not title:
                for kw in ["标题", "题目", "就叫", "用"]:
                    m = re.search(kw + r'\s*[：:]*\s*["“《「]?([^\n"」》]{3,10})', raw)
                    if m:
                        title = m.group(1).strip().strip("《》「」\"'“”")
                        if 3 <= len(title) <= 10:
                            break
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
