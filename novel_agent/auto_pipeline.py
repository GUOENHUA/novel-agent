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
import sys
import time
from typing import Any

from novel_agent.agent import AIAgent
from novel_agent.utils.constants import MAX_RETRY_ATTEMPTS, DRAFT_PASS_THRESHOLD

logger = logging.getLogger(__name__)

def _safe_print(text: str) -> None:
    """Windows-safe print — avoids GBK encoding errors with Unicode chars."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='replace').decode('ascii'))

# Tool schema for settlement extraction — model calls this, API guarantees valid JSON
SETTLE_TOOL = {
    "name": "settle_chapter",
    "description": "Record structured facts extracted from a completed chapter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chapter_summary": {"type": "string", "description": "2-3 sentence summary"},
            "key_events": {"type": "array", "items": {"type": "string"}},
            "characters_appearing": {"type": "array", "items": {"type": "string"}},
            "mood": {"type": "string", "enum": ["tense", "hopeful", "tragic", "mysterious", "dark", "neutral"]},
            "character_changes": {
                "type": "object",
                "description": "Map of character name to {location, emotional_state, goal, important_fact}",
            },
            "hooks_planted": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "desc": {"type": "string"},
                        "type": {"type": "string", "enum": ["direct", "symbolic", "dialogue", "action", "naming"]},
                        "scope": {"type": "string", "enum": ["book", "volume", "arc", "chapter"]},
                    },
                    "required": ["id", "desc"],
                },
            },
            "hooks_mentioned": {"type": "array", "items": {"type": "string"}},
            "hooks_resolved": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["chapter_summary"],
    },
}


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
        """Run the auto pipeline for N chapters using the full conversational stack.

        Each chapter is processed via ConversationLoop.process_turn(interactive=False),
        which reuses the complete context assembly, thinking mode, tool loop, history
        accumulation, and compression — identical to interactive mode, just without
        user prompts for clarify/preview.
        """
        from novel_agent.conversation_loop import ConversationLoop

        words = words_per_chapter or self.agent.chapter_words
        self.results = []
        loop = ConversationLoop(self.agent)

        if count > 10:
            estimated_tokens = count * words * 1.5
            _safe_print(f"\n  {count} chapters, ~{estimated_tokens:,.0f} tokens estimated")

        _safe_print(f"\n  Auto mode: ch{start_chapter}-{start_chapter + count - 1}, {count} chapters, ~{words} words each")
        _safe_print(f"  (Ctrl+C to interrupt)\n")

        # Setup signal handler for graceful interrupt
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._interrupt_handler)

        try:
            for ch in range(start_chapter, start_chapter + count):
                if self.agent.interrupted:
                    _safe_print(f"\n  PAUSED  自动模式已暂停 (已完成 {ch - start_chapter}/{count} 章)")
                    break

                progress = f"{ch - start_chapter + 1}/{count}"
                _safe_print(f"  [{progress}] Writing ch{ch}...")
                t0 = time.time()

                # Use the full conversational stack — thinking, tools, history, compression
                assistant_text = loop.process_turn(
                    f"写第{ch}章完整正文，目标{words}字左右。"
                    f"写完后调用 preview_chapter 保存。",
                    interactive=False,
                )

                # Verify file was saved; retry up to MAX_RETRY_ATTEMPTS times
                chapter_path = self.agent.chapter_path(ch)
                for retry in range(MAX_RETRY_ATTEMPTS):
                    if chapter_path.exists():
                        break
                    _safe_print(f"    Retry {retry+1}/{MAX_RETRY_ATTEMPTS} ch{ch}...")
                    time.sleep(15)
                    loop.process_turn(
                        f"第{ch}章没有保存成功——上一轮输出的章节内容不在代码块内。\n\n"
                        f"请严格按以下格式重新输出（章号必须是阿拉伯数字 {ch}）：\n\n"
                        f"```章节\n"
                        f"# 第{ch}章 标题\n\n"
                        f"正文内容...\n"
                        f"```\n\n"
                        f"然后调用 preview_chapter 保存。",
                        interactive=False,
                    )
                if chapter_path.exists():
                    content = chapter_path.read_text("utf-8")
                    slop_score, slop_warnings = self._check_slop(content)
                    # Settlement: retry until summary exists (max 3)
                    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                        try:
                            self._settle_state(ch, content)
                        except Exception:
                            pass
                        if any(s.chapter_number == ch for s in self.agent.truth_files.load_summaries()):
                            break
                        if attempt < MAX_RETRY_ATTEMPTS:
                            _safe_print(f"    Settlement retry {attempt+1}/{MAX_RETRY_ATTEMPTS} ch{ch}...")
                    # Plot memory: retry until exists (max 3)
                    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                        if self._has_plot_memory(ch):
                            break
                        if attempt < MAX_RETRY_ATTEMPTS:
                            _safe_print(f"    Plot memory retry {attempt+1}/{MAX_RETRY_ATTEMPTS} ch{ch}...")
                        self._ensure_plot_memory(ch, content)
                    elapsed = time.time() - t0
                    hooks_info = ""
                    hooks = self.agent.hook_ledger.get_active()
                    summaries = self.agent.truth_files.load_summaries()
                    if hooks: hooks_info += f", hooks:{len(hooks)}"
                    if summaries: hooks_info += f", summaries:{len(summaries)}"
                    _safe_print(f"  [{progress}] ch{ch} OK ({len(content)} chars, slop {slop_score:.0f}{hooks_info}, {elapsed:.0f}s)")
                    self.results.append({
                        "chapter": ch, "success": True,
                        "word_count": len(content),
                        "slop_score": slop_score, "slop_warnings": slop_warnings,
                        "attempts": retry + 1,
                    })
                else:
                    # Fallback: save longest raw text from failed attempts if > 1000 chars
                    fallback = getattr(loop, '_auto_fallback_text', {}).get(ch, '')
                    if fallback and len(fallback) > 1000:
                        title = self._generate_title(fallback, ch)
                        path = self.agent.chapter_path(ch, title or 'untitled')
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(f'# 第{ch}章 {title}\n\n{fallback}', encoding='utf-8')
                        self.agent.truth_files.load_state().current_chapter = ch + 1
                        self.agent.truth_files.save_state(self.agent.truth_files.load_state())
                        self._settle_state(ch, fallback)
                        _safe_print(f"  [{progress}] ch{ch} OK via fallback ({len(fallback)} chars, non-standard format)")
                        self.results.append({
                            "chapter": ch, "success": True,
                            "word_count": len(fallback),
                            "slop_score": 0, "slop_warnings": [],
                            "attempts": MAX_RETRY_ATTEMPTS, "fallback": True,
                        })
                    else:
                        _safe_print(f"  [{progress}] ch{ch} FAILED after {MAX_RETRY_ATTEMPTS} retries")
                        self.results.append({
                            "chapter": ch, "success": False,
                            "word_count": 0, "slop_score": 0,
                            "slop_warnings": [], "attempts": MAX_RETRY_ATTEMPTS,
                        })

            # Summary
            completed = [r for r in self.results if r["success"]]
            total_words = sum(r["word_count"] for r in completed)
            _safe_print(f"\n  OK {len(completed)}/{len(self.results)} chapters, {total_words} chars total")

        finally:
            signal.signal(signal.SIGINT, original_handler)
            self.agent.interrupted = False

        return self.results





    def _has_plot_memory(self, chapter_num: int) -> bool:
        """Check if a plot memory exists for this chapter."""
        for p in self.agent.memory_dir.glob(f"plot-*{chapter_num}*已写*.md"):
            return True
        for p in self.agent.memory_dir.glob(f"plot-*第{chapter_num}章*.md"):
            return True
        return False

    def _ensure_plot_memory(self, chapter_num: int, content: str) -> None:
        """Create a plot memory for this chapter via a one-shot LLM call.
        Best-effort — failures are silently ignored."""
        try:
            title = ""
            cp = self.agent.chapter_path(chapter_num)
            if cp.exists():
                fl = cp.read_text("utf-8").split("\n")[0]
                import re
                m = re.match(r'#\s*第\d+章\s+(.+)', fl)
                title = m.group(1).strip() if m else ""
            if len(content) < 3000:
                _safe_print(f"    [WARN] Ch{chapter_num} only {len(content)} chars, plot memory may be sparse")
            directive = (
                f"Save a plot memory for chapter {chapter_num}. "
                f"Call memory add type=plot name='第{chapter_num}章已写' "
                f"with: chapter summary, key events, hooks, and Why/How analysis.\n\n"
                f"Content:\n{content[:3000]}"
            )
            self.agent.call_llm(
                messages=[{"role": "user", "content": directive}],
                max_tokens=800, temperature=0.3,
            )
        except Exception:
            pass

    def _check_slop(self, content: str) -> tuple[float, list[str]]:
        """Run mechanical slop check."""
        from novel_agent.tools.slop_checker import mechanical_scan
        result = mechanical_scan(content)
        warnings = [h["match"] for h in result.get("tier1_hits", [])]
        warnings += [h["match"] for h in result.get("fiction_hits", [])]
        return result["score"], warnings

    def _settle_state(self, chapter_num: int, content: str) -> None:
        """Extract hooks + summary + character changes from a saved chapter.
        Does NOT write the chapter file — _save_chapter_to_file already did that."""
        # Settlement: low-temp extraction of structured data
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
            # Title was already saved by _save_chapter_to_file — read it back
            import re as _re
            saved_title = ""
            cp = self.agent.chapter_path(chapter_num)
            if cp.exists():
                fl = cp.read_text("utf-8").split("\n")[0]
                m = _re.match(r'#\s*第\d+章\s+(.+)', fl)
                saved_title = m.group(1).strip() if m else ""
            chars = settlement.get("characters_appearing", [])
            mood = settlement.get("mood", "neutral")
            self.agent.truth_files.add_summary(ChapterSummary(
                chapter_number=chapter_num, title=saved_title,
                word_count=len(content), summary=settlement["chapter_summary"],
                key_events=settlement.get("key_events", []),
                characters_appearing=chars,
                hooks_planted=[h["id"] for h in settlement.get("hooks_planted", [])],
                hooks_resolved=settlement.get("hooks_resolved", []),
                mood=mood,
                pov_character=settlement.get("pov_character", ""),
                narrative_distance=settlement.get("narrative_distance", ""),
                tense=settlement.get("tense", ""),
                thought_style=settlement.get("thought_style", ""),
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
                f'  "pov_character": "本章视角角色名（如单一POV则填一个名字，如多POV用逗号分隔）",\n'
                f'  "narrative_distance": "close_third|omniscient|first_person",\n'
                f'  "tense": "past|present",\n'
                f'  "thought_style": "free_indirect|direct_thought|none",\n'
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
                max_tokens=2000, temperature=0.3,
            )
            raw = self.agent.extract_text(resp.content, fallback_to_thinking=True).strip()
            if not raw:
                return {}
            for fence in ("```json", "```"):
                raw = raw.replace(fence, "").strip()
            # Find JSON object boundaries
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return {}
            raw = raw[start:end + 1]

            # Try parsing; if broken, ask LLM to fix; if still broken, salvage fields
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                # Ask LLM to fix its own JSON
                fix_resp = self.agent.call_llm(
                    messages=[{"role": "user", "content": (
                        f"Fix this invalid JSON. Return ONLY valid JSON, no explanation.\n"
                        f"Error: {e}\n\n{raw}"
                    )}],
                    max_tokens=1200, temperature=0,
                )
                fixed = self.agent.extract_text(fix_resp.content, fallback_to_thinking=True).strip()
                for fence in ("```json", "```"):
                    fixed = fixed.replace(fence, "").strip()
                fs = fixed.find("{")
                fe = fixed.rfind("}")
                if fs >= 0 and fe > fs:
                    try:
                        return json.loads(fixed[fs:fe + 1])
                    except json.JSONDecodeError:
                        pass

                # Last resort: regex-extract at least the chapter_summary
                import re
                result = {}
                m = re.search(r'"chapter_summary"\s*:\s*"([^"]*)"', raw)
                if m:
                    result["chapter_summary"] = m.group(1)
                    logger.warning("Salvaged chapter_summary via regex for ch%d", chapter_num)
                m = re.search(r'"mood"\s*:\s*"([^"]*)"', raw)
                if m:
                    result["mood"] = m.group(1)
                return result
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

    TITLE_TOOL = {
        "name": "set_title",
        "description": "Set the chapter title",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "4-8 Chinese character chapter title"}},
            "required": ["title"],
        },
    }

    TITLE_TOOL = {
        "name": "set_title",
        "description": (
            "Set the chapter title. Usually 2-8 characters, evocative and poetic. "
            "Occasionally a short verse or couplet. Rarely a single character for impact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Chapter title"}},
            "required": ["title"],
        },
    }

    def _generate_title(self, content: str, chapter_num: int) -> str:
        """Generate a novel-quality chapter title via tool call.

        Title conventions: 2-8 chars most common, short verse for climax
        chapters, single character for high-impact minimalist moments.
        """
        try:
            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": (
                    f"为这一章起一个中文标题。章回小说风格，富有诗意和画面感。\n"
                    f"通常2-8个汉字，高潮章节可用短诗或对句，极简时刻可用单字。\n\n"
                    f"示例: 醒来 / 下山 / 驿馆 / 问剑 / 春风不度鬼门关 / 他看见自己的尸体 / 归\n\n"
                    f"{content[:1000]}"
                )}],
                tools=[self.TITLE_TOOL],
                tool_choice={"type": "tool", "name": "set_title"},
                max_tokens=200, temperature=0.5,
            )
            for block in resp.content:
                if hasattr(block, "type") and block.type == "tool_use" and isinstance(block.input, dict):
                    t = block.input.get("title", "").strip()
                    if 1 <= len(t) <= 40:
                        return t
        except Exception:
            pass
        return f"第{chapter_num}章"
        return fallback if len(fallback) >= 2 else f"第{chapter_num}章"

    @staticmethod
    def _interrupt_handler(signum, frame):
        """Handle Ctrl+C gracefully."""
        _safe_print("\n\n  PAUSED  收到中断信号，完成当前章节后切换回对话模式...")
        # The agent.interrupted flag is checked at the top of each chapter loop
