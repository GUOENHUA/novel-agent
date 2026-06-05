"""Main AIAgent class — initialization and lifecycle management.

Borrows patterns from hermes-agent `agent/agent_init.py` and `run_agent.py`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import anthropic
import dotenv

from novel_agent.memory.memory_manager import MemoryManager
from novel_agent.memory.builtin_provider import BuiltinProvider
from novel_agent.state.truth_files import TruthFileManager
from novel_agent.state.hook_ledger import HookLedger
from novel_agent.skills.loader import SkillLoader
from novel_agent.context.compressor import NovelCompressor
from novel_agent.context.config import budget
from novel_agent.utils.constants import (
    DEFAULT_WRITER_MODEL,
    DEFAULT_CHAPTER_WORDS,
    DEFAULT_TOTAL_CHAPTERS,
    DEFAULT_TOTAL_WORDS,
)
from novel_agent.utils.files import ensure_dir

logger = logging.getLogger(__name__)


class AIAgent:
    """Main agent orchestrating novel writing sessions.

    One instance per project. Shared by both conversational and auto modes.
    """

    def __init__(
        self,
        project_dir: str | Path,
        model: str | None = None,
        chapter_words: int | None = None,
        total_chapters: int | None = None,
        total_words: int | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.model = model or os.getenv("NOVEL_AGENT_MODEL", DEFAULT_WRITER_MODEL)

        # Load project config from novel.json (falls back to defaults)
        self._load_project_config(chapter_words, total_chapters, total_words)

        # Conversation history + session persistence
        self.session_path = self.project_dir / "session.json"
        self.conversation_history: list[dict[str, Any]] = []

        # Interrupt flag for auto mode → conversational switch
        self.interrupted = False

        # Anthropic client (supports custom base_url for compatible endpoints)
        api_key = self._resolve_api_key()
        base_url = self._resolve_base_url()
        if base_url:
            self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
            logger.info("Using custom API endpoint: %s", base_url)
        else:
            self.client = anthropic.Anthropic(api_key=api_key)

        # Project paths
        self.memory_dir = ensure_dir(self.project_dir / "memory")
        self.chapters_dir = ensure_dir(self.project_dir / "chapters")
        self.state_dir = ensure_dir(self.project_dir / "state")

        # Initialize subsystems
        self._init_subsystems()

    def chapter_path(self, num: int, title: str = "") -> Path:
        """Get chapter file path with consistent naming: ch_001_title-slug.md."""
        if not title:
            existing = list(self.chapters_dir.glob(f"ch_{num:03d}_*.md"))
            if existing:
                return existing[0]
        slug = ""
        if title:
            slug = "_" + "".join(c for c in title.lower().replace(" ", "-") if c.isalnum() or c in "-_")[:40]
        return self.chapters_dir / f"ch_{num:03d}{slug}.md"

    @staticmethod
    def extract_chapter_num(path: Path) -> int:
        """Extract chapter number from filename like ch_001_title.md."""
        import re
        m = re.match(r"ch_(\d+)", path.stem)
        return int(m.group(1)) if m else 0

    def _init_subsystems(self) -> None:
        """Initialize all subsystems."""
        self._memory_manager = MemoryManager()
        self._context_engine = None
        self._skill_loader = None

        # Initialize subsystems (each is self-contained, failures don't cascade)
        try:
            self._init_memory()
        except Exception:
            logger.exception("Memory init failed")

        try:
            self._init_state()
        except Exception:
            logger.exception("State init failed")

        try:
            self._init_skills()
        except Exception:
            logger.exception("Skills init failed")

        try:
            self._init_context()
        except Exception:
            logger.exception("Context init failed")

        # Load previous session if exists
        self._restore_session()

        logger.info("Agent initialized: project=%s model=%s", self.project_dir, self.model)

    def _restore_session(self) -> None:
        """Load previous conversation history from session.json."""
        import json
        if not self.session_path.exists():
            return
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
            history = data.get("history", [])
            if history:
                # Sanitize: remove orphaned tool_result messages
                # (every tool_result must follow a tool_use from assistant)
                clean = []
                for msg in history:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    is_tool_result = isinstance(content, list) and any(
                        b.get("type") == "tool_result" if isinstance(b, dict) else False
                        for b in content
                    )
                    if is_tool_result:
                        # Only keep if previous msg was an assistant with tool_use
                        if clean and clean[-1].get("role") == "assistant":
                            prev_content = clean[-1].get("content", [])
                            has_tool_use = isinstance(prev_content, list) and any(
                                b.get("type") == "tool_use" if isinstance(b, dict) else False
                                for b in prev_content
                            )
                            if has_tool_use:
                                clean.append(msg)
                                continue
                        continue  # Skip orphaned tool_result
                    clean.append(msg)
                # Convert old-format plain string messages to ContentBlock arrays
                for msg in clean:
                    if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                        msg["content"] = [{"type": "text", "text": msg["content"]}]
                self.conversation_history = clean
                logger.info("Loaded session: %d messages (%d cleaned)", len(history), len(clean))
        except Exception:
            pass

    def save_session(self) -> None:
        """Persist conversation history to session.json."""
        import json
        # Convert Anthropic content blocks to serializable dicts
        serializable = []
        for msg in self.conversation_history[-100:]:
            entry = {"role": msg["role"]}
            content = msg.get("content", "")
            if isinstance(content, list):
                # Convert ContentBlock objects to dicts
                blocks = []
                for block in content:
                    if hasattr(block, "type"):
                        b = {"type": block.type}
                        if hasattr(block, "text"):
                            b["text"] = block.text
                        if hasattr(block, "name"):
                            b["name"] = block.name
                            b["input"] = dict(block.input) if hasattr(block, "input") and block.input else {}
                        if hasattr(block, "thinking"):
                            b["thinking"] = block.thinking
                        if hasattr(block, "id"):
                            b["id"] = block.id
                        blocks.append(b)
                    else:
                        blocks.append(block)
                entry["content"] = blocks
            elif msg.get("role") == "assistant":
                # Assistant messages must be ContentBlock arrays for API
                entry["content"] = [{"type": "text", "text": str(content)}]
            else:
                entry["content"] = str(content)
            serializable.append(entry)

        data = {
            "model": self.model,
            "total_words": self.total_words,
            "history": serializable,
        }
        self.session_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _init_memory(self) -> None:
        """Initialize the memory subsystem."""
        builtin = BuiltinProvider()
        self._memory_manager.add_provider(builtin)
        self._memory_manager.initialize_all(str(self.memory_dir))

        # Wire memory tool to registry
        from novel_agent.tools.memory_tool import set_provider
        set_provider(builtin)

        logger.info("Memory system initialized: %s", self.memory_dir)

    def _init_state(self) -> None:
        """Initialize the state management subsystem."""
        self.truth_files = TruthFileManager(self.state_dir)
        self.hook_ledger = HookLedger(self.state_dir)

        # Wire hook tool to registry
        from novel_agent.tools.hook_tool import set_hook_ledger
        set_hook_ledger(self.hook_ledger)

        logger.info("State system initialized: %s", self.state_dir)

    def _init_skills(self) -> None:
        """Initialize the skills subsystem."""
        self.skill_loader = SkillLoader()
        self.skill_loader.discover_all(self.project_dir)

        # Wire skill tool to registry
        from novel_agent.tools.skill_tool import set_skill_loader
        set_skill_loader(self.skill_loader)

        skills = self.skill_loader.list_all()
        logger.info("Skills initialized: %d skills loaded", len(skills))

    def _init_context(self) -> None:
        """Initialize the context compression engine.

        All budgets derive from NOVEL_AGENT_CONTEXT_LENGTH (default 200k).
        Change that env var and everything scales proportionally.
        """
        threshold_pct = float(os.getenv("NOVEL_AGENT_COMPRESS_THRESHOLD", "0.70"))
        self.context_engine = NovelCompressor(
            model=self.model,
            context_length=budget.total,
            threshold_percent=threshold_pct,
            tail_token_budget=budget.tail_token_budget,
        )
        logger.info(
            "Context engine initialized: %s (context=%d, threshold=%.0f%%), %s",
            self.context_engine.name, budget.total, threshold_pct * 100,
            budget.describe().replace('\n', ', ') if logger.isEnabledFor(logging.DEBUG) else "",
        )

    @staticmethod
    def _resolve_api_key() -> str:
        """Resolve API key from env (supports both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN)."""
        dotenv.load_dotenv()
        key = os.getenv("ANTHROPIC_AUTH_TOKEN", "") or os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY not set. "
                "Set it in .env or environment."
            )
        return key

    @staticmethod
    def _resolve_base_url() -> str | None:
        """Resolve custom API base URL (for Anthropic-compatible endpoints)."""
        return os.getenv("ANTHROPIC_BASE_URL") or None

    def _load_project_config(
        self,
        chapter_words: int | None = None,
        total_chapters: int | None = None,
        total_words: int | None = None,
    ) -> None:
        """Load project configuration from novel.json, with CLI override support."""
        import json

        config_path = self.project_dir / "novel.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                config = {}
        else:
            config = {}

        # CLI args > novel.json > defaults
        self.novel_title = config.get("title", self.project_dir.name)
        self.chapter_words = chapter_words or config.get("chapter_words", DEFAULT_CHAPTER_WORDS)
        self.total_chapters = total_chapters or config.get("total_chapters", DEFAULT_TOTAL_CHAPTERS)
        self.total_words = total_words or config.get("total_words", DEFAULT_TOTAL_WORDS)

    _stable_prompt_cache: str | None = None  # Cache for stable layer

    # -- System prompt (stable layer, cached) -----------------------------------

    def build_system_prompt(self) -> str:
        """Build the STABLE system prompt layer.

        This is book-wide, rarely changes — safe for prompt caching.
        Novel-specific state goes into build_novel_context() instead.
        """
        if self._stable_prompt_cache is not None:
            return self._stable_prompt_cache

        parts = []

        # Core identity + writing philosophy
        parts.append(
            "你是一个专业的小说写作 AI 助手。你的目标是帮助用户创作高质量的小说。\n\n"
            "## 核心能力\n"
            "- 撰写和修改章节\n"
            "- 发展和完善角色\n"
            "- 规划情节结构\n"
            "- 管理伏笔和线索\n"
            "- 检查一致性和 AI 痕迹\n"
            "- 搜索世界观和角色信息\n\n"
            "## 工作原则\n"
            "- 每种内容只存一份：大纲→outline.md / 章节→chapters/ / 角色世界观→memory / 伏笔→track_hooks。禁止同内容存两处\n"
            "## 对话模式创作流程（严格遵守）\n"
            "- 每次回复末尾必须给出下一步引导——建议用户接下来做什么，输入什么指令。例如'接下来可以写大纲，输入继续开始'。永远不要让用户猜下一步做什么\n"
            "- 每步结束后展示待办清单并推荐下一步：\n"
            "  '已完成：大纲 ✓。建议下一步：创建主角。输入 继续 或 yes 开始。'\n"
            "- 标准顺序：大纲 → 主角 → 重要配角 → 世界观核心 → 第一章\n"
            "- 用户说'继续'/'yes'时，执行推荐的那一步，完成后再次推荐下一步\n"
            "- 指令模糊时追问，不确定的情节/角色/风格提供2-3个选项\n\n"
            "## 讨论与生成流程\n"
            "\n"
            "【讨论阶段】先和用户分步讨论，一次只问一个问题：\n"
            "  先问核心概念 → 再问主角方向 → 然后问风格偏好 → 最后问规模\n"
            "  用 clarify 收集信息，把回答记住\n"
            "\n"
            "【生成前确认】总结全部要点，用 clarify 做最终确认：\n"
            "  '我整理了以下要点：1. xxx 2. xxx 3. xxx。确认无误开始生成？'\n"
            "  选项：'确认，开始生成'、'我有调整'、'重新讨论'\n"
            "  用户选确认后才开始写内容，在此之前绝对不要输出正文\n"
            "\n"
            "## 内容生成三步流程（每次创作前必须遵守）\n"
            "\n"
            "无论生成什么——章节/大纲/角色/世界观/风格——都走这三步：\n"
            "\n"
            "第1步 生成前确认：用 clarify 询问，例如'准备写第3章，有什么要补充的吗？'\n"
            "  选项固定为：'直接开始'、'我有补充'、'换个方向'\n"
            "\n"
            "第2步 生成内容：流式输出完整内容，让用户实时看到所有文字\n"
            "\n"
            "第3步 预览保存：内容写完之后才调预览工具，用户确认后才持久化。\n"
            "  注意：必须先把内容写出来，再调工具！不要反过来。\n"
            "  章节 → preview_chapter / 大纲 → preview_outline\n"
            "  角色/世界观/风格 → preview_setting\n"
            "\n"
            "⚠️ 极其重要：即使你已经在对话中输出了完整的章节正文，\n"
            "系统也不会自动保存。你必须调用 preview_chapter 工具！\n"
            "跳过第3步意味着你的工作成果不会被写入文件。\n"
            "每写完一章必须立即调用 preview_chapter，不要等到下一章。\n"
            "\n"
            "绝不跳过任何一步，绝不在用户确认前保存。\n\n"
            "- 自动模式：不提问，基于上下文做出最佳判断直接执行\n"
            "- 工具按需取用，优先使用上下文已有信息，不要做全盘搜索\n\n"
            "## 写作哲学\n"
            "- 角色必须真正地改变（警惕稳定性陷阱）\n"
            "- 让坏事保持坏——不是一切都能修复\n"
            "- 允许不可逆的决定和不可逆的损失\n"
            "- 扣留信息——读者不需要立即知道一切\n"
            "- 具体胜过抽象（'一只松鸦'胜过'一只鸟'）\n"
            "- 变化情感强度：安静/爆发/恐惧/解脱/无聊/惊奇/恐怖\n"
        )

        # Memory system
        memory_prompt = self._memory_manager.build_system_prompt()
        if memory_prompt:
            parts.append(memory_prompt)

        # Skills listing
        if self.skill_loader:
            skills_prompt = self.skill_loader.build_system_prompt_block()
            if skills_prompt:
                parts.append(skills_prompt)

        # Craft education
        craft_path = Path(__file__).parent / "craft" / "CRAFT.md"
        if craft_path.exists():
            parts.append(f"\n\n## 写作工艺参考\n\n{craft_path.read_text(encoding='utf-8')}")

        # Anti-slop reference
        antislop_path = Path(__file__).parent / "craft" / "ANTI_SLOP.md"
        if antislop_path.exists():
            parts.append(f"\n\n## AI 痕迹检测参考\n\n{antislop_path.read_text(encoding='utf-8')}")

        self._stable_prompt_cache = "\n".join(parts)
        return self._stable_prompt_cache

    @staticmethod
    def _empty_state() -> Any:
        """Return a default empty novel state."""
        from novel_agent.state.schemas import NovelState
        return NovelState()

    def clear_prompt_cache(self) -> None:
        """Clear the stable prompt cache (e.g. after skills or memory change)."""
        self._stable_prompt_cache = None

    # -- Novel context (dynamic layer, per-turn) ---------------------------------

    def build_novel_context(self, user_message: str = "") -> str:
        """Build the DYNAMIC novel context for injection before the user message.

        This is the per-turn state snapshot. It changes every turn and should
        NOT be in the system prompt (would break prompt caching).
        """
        import re

        state = self.truth_files.load_state() if hasattr(self, "truth_files") and self.truth_files else self._empty_state()
        lines = []

        # 1. Progress
        existing = list(self.chapters_dir.glob("ch_*_*.md"))
        completed_chapters = len(existing)
        total_written = sum(len(p.read_text(encoding="utf-8")) for p in existing)
        ch = state.current_chapter or (completed_chapters + 1)
        lines.append(f"## 📖 《{self.novel_title}》")
        lines.append(f"第 {ch}/{self.total_chapters} 章 | {total_written} 字 | 每章目标 {self.chapter_words} 字 | {state.phase}")
        if state.current_scene:
            lines.append(f"当前场景: {state.current_scene}")
        lines.append("")

        # 1b. Rhythm bar — mood + word count of last 5 chapters
        summaries = self.truth_files.load_summaries() if hasattr(self, "truth_files") and self.truth_files else []
        if summaries and len(summaries) >= 2:
            recent_5 = summaries[-5:]
            parts = []
            for s in recent_5:
                title = s.title or f"Ch{s.chapter_number}"
                mood_map = {"tense": "紧张", "hopeful": "希望", "tragic": "悲壮",
                            "mysterious": "神秘", "dark": "黑暗", "neutral": "中性",
                            "romantic": "浪漫", "whimsical": "奇幻"}
                mood_cn = mood_map.get(s.mood, s.mood) if s.mood else "—"
                parts.append(f"{title} {s.word_count}字 {mood_cn}")
            if ch > len(summaries):
                parts.append(f"Ch{ch} (待写)")
            lines.append(f"📊 最近: {' → '.join(parts)}")
            lines.append("")

        # 2. Previous chapter ending (most important anchor)
        if ch > 1:
            prev_path = self.chapter_path(ch - 1)
            if prev_path.exists():
                prev_text = prev_path.read_text(encoding="utf-8")
                ending = prev_text[-budget.chapter_ending_chars:] if len(prev_text) > budget.chapter_ending_chars else prev_text
                lines.append("## 📝 前一章结尾")
                lines.append("```")
                lines.append(ending.strip())
                lines.append("```")
                lines.append("")

        # 3. Multi-level outline
        outline_path = self.project_dir / "outline" / "full.md"
        vol_outline_dir = self.project_dir / "outline"
        if outline_path.exists():
            outline_text = outline_path.read_text(encoding="utf-8")

            # 3a. Book-level framing (first ~500 chars — overall arc, theme, ending vision)
            book_framing = outline_text[:budget.book_framing_chars].strip()
            if book_framing:
                lines.append("## 📖 全书框架")
                lines.append(book_framing)
                lines.append("")

            # 3b. Volume/arc context (find current volume based on chapter)
            vol_pattern = rf"(第[一二三四五六七八九十\d]+卷[^\n]*|Volume\s*\d+[^\n]*)"
            vol_matches = list(re.finditer(vol_pattern, outline_text))
            current_vol = None
            for vm in vol_matches:
                # Check if this volume declaration is before current chapter
                vol_start = vm.start()
                ch_pattern = rf"第\s*{ch}\s*章"
                ch_match = re.search(ch_pattern, outline_text)
                if ch_match and vol_start < ch_match.start():
                    current_vol = vm
            if current_vol:
                vol_end = outline_text.find("\n#", current_vol.end())
                if vol_end == -1:
                    vol_end = min(len(outline_text), current_vol.end() + budget.volume_context_chars)
                lines.append("## 📋 当前卷")
                lines.append(outline_text[current_vol.start():vol_end].strip())
                lines.append("")

            # 3c. Adjacent chapters (previous 1 + current + next 1)
            ch_positions = []
            for m in re.finditer(rf"第\s*(\d+)\s*章", outline_text):
                cn = int(m.group(1))
                ch_positions.append((cn, m.start(), m.end()))

            for cn, start, end in ch_positions:
                if cn in (ch - 1, ch, ch + 1):
                    # Find next chapter boundary (or end of section)
                    next_pos = len(outline_text)
                    for cn2, s2, _ in ch_positions:
                        if cn2 > cn and s2 > end:
                            next_pos = s2
                            break
                    entry = outline_text[max(0, start - 20):min(len(outline_text), end + budget.chapter_entry_chars)]
                    label = "本章大纲" if cn == ch else ("前一章大纲" if cn < ch else "下一章大纲")
                    lines.append(f"## 📋 {label} (第{cn}章)")
                    lines.append(entry.strip())
                    lines.append("")
        else:
            # No outline file — suggest creating one
            lines.append("## 📋 大纲")
            lines.append(f"(尚无 outline.md — 使用 outline_plot 生成)")
            lines.append("")

        # 4. Scene characters (from state file)
        active_chars: set = set()
        if state.characters:
            active = {n: c for n, c in state.characters.items() if c.alive}
            if active:
                active_chars = set(active.keys())
                lines.append("## 🎬 当前场景角色")
                for name, char in active.items():
                    loc = f" @{char.current_location}" if char.current_location else ""
                    goal = f" → {char.goal}" if char.goal else ""
                    lines.append(f"- **{name}**: {char.emotional_state}{loc}{goal}")
                lines.append("")

        # 4b. Character voice samples (1-2 representative lines per active character)
        voice_samples = self._fetch_character_voices(active_chars)
        if voice_samples:
            lines.append("## 🗣 角色声音样本")
            for vs in voice_samples:
                lines.append(f"- {vs}")
            lines.append("")

        # 4b2. Minor character register — compact visibility without search.
        # Scan memory headers for tier=minor/cameo chars seen in recent chapters.
        # Not in search results, but still visible in context while relevant.
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.memory_dir)
            all_headers = store.scan_memory_headers()
            minor_chars = [
                h for h in all_headers
                if h.get("type") == "character" and h.get("tier") in ("minor", "cameo")
            ]
            if minor_chars:
                items = []
                for h in minor_chars:
                    desc = h.get("description", "")[:60]
                    items.append(f"{h['name']}: {desc}" if desc else h["name"])
                lines.append(f"📋 龙套 ({len(minor_chars)}): {', '.join(items)}")
                lines.append("")
        except Exception:
            pass

        # 4c. Golden paragraph — style anchor from ~20% into previous chapter.
        # Chapter openings warm up (scene-setting, bridging); endings wrap up.
        # The middle section (~20% in) captures the chapter's core narrative
        # rhythm — the best reference for maintaining consistent voice.
        if ch > 1:
            prev_path = self.chapter_path(ch - 1)
            if prev_path.exists():
                prev_text = prev_path.read_text(encoding="utf-8")
                # Find body start (skip title/header/frontmatter lines)
                body_start = 0
                for i, line in enumerate(prev_text.split("\n")):
                    if line.strip() and not line.startswith("#") and not line.startswith("---"):
                        body_start = prev_text.find(line)
                        break
                body = prev_text[body_start:] if body_start > 0 else prev_text
                # Take golden paragraph from ~20% into the body
                mid_pos = len(body) // 5
                golden = body[mid_pos:mid_pos + budget.style_anchor_chars].strip()
                if golden:
                    lines.append("## 🖋 前一章文风锚点")
                    lines.append(f"```\n{golden}\n```")
                    lines.append("")

        # 4d. POV anchor — viewpoint character and narrative setup for previous chapter
        prev_summary = summaries[-1] if summaries and summaries[-1].chapter_number == ch - 1 else None
        if prev_summary and prev_summary.pov_character:
            pov_parts = [f"上一章视角: {prev_summary.pov_character}"]
            if prev_summary.narrative_distance:
                dist_map = {"close_third": "第三人称有限", "omniscient": "全知",
                            "first_person": "第一人称"}
                pov_parts.append(dist_map.get(prev_summary.narrative_distance, prev_summary.narrative_distance))
            if prev_summary.tense:
                tense_map = {"past": "过去时", "present": "现在时"}
                pov_parts.append(tense_map.get(prev_summary.tense, prev_summary.tense))
            lines.append(f"👁 {' | '.join(pov_parts)}")
            lines.append("")

        # 5. Hooks — tiered by scope and deadline window
        active_hooks = self.hook_ledger.get_active()
        if active_hooks:
            lines.append("## 🔮 伏笔")
            overdue = self.hook_ledger.get_overdue(ch)
            overdue_ids = {h.id for h in overdue}
            window_end = ch + 10  # 10-chapter window for arc/chapter-level hooks

            # --- Overdue: never collapse ---
            if overdue:
                lines.append(f"**⚠️ 逾期 ({len(overdue)}):** {', '.join(f'[{h.id}]' for h in overdue)}")

            # --- Tier 1: Always loaded (book + volume) ---
            book_hooks = [h for h in active_hooks if getattr(h, 'scope', None) == "book"]
            volume_hooks = [h for h in active_hooks if getattr(h, 'scope', None) == "volume"]
            always_shown = book_hooks + volume_hooks
            if always_shown:
                lines.append("**全书/本卷:**")
                for h in always_shown:
                    overdue_mark = " ⚠️" if h.id in overdue_ids else ""
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}{overdue_mark}")

            # --- Tier 2: Window (arc/chapter, within 10 chapters) ---
            window_hooks = [
                h for h in active_hooks
                if getattr(h, 'scope', None) not in ("book", "volume")
                and h.target_chapter and h.target_chapter <= window_end
            ]
            window_hooks.sort(key=lambda h: h.target_chapter or 999)
            if window_hooks:
                lines.append(f"**{ch}-{window_end}章内 ({len(window_hooks)}):**")
                for h in window_hooks:
                    overdue_mark = " ⚠️" if h.id in overdue_ids else ""
                    remaining = f" (还剩{h.target_chapter - ch}章)" if h.target_chapter else ""
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}{remaining}{overdue_mark}")

            # --- Tier 3: Folded (far future) ---
            far_hooks = [
                h for h in active_hooks
                if getattr(h, 'scope', None) not in ("book", "volume")
                and (not h.target_chapter or h.target_chapter > window_end)
            ]
            if far_hooks:
                ids = ", ".join(f"[{h.id}]" for h in far_hooks[:10])
                more = f" ...等共{len(far_hooks)}个" if len(far_hooks) > 10 else ""
                lines.append(f"📦 远期伏笔 ({len(far_hooks)}): {ids}{more}")
                lines.append("  (需要时用 track_hooks(action=report) 查询全部)")

            lines.append("")

        # 6. Recent + relevant chapter summaries (last 3 always, +5 LLM-selected)
        summaries = self.truth_files.load_summaries() if hasattr(self, "truth_files") and self.truth_files else []
        if summaries:
            # Always include last 3 chapters for continuity
            recent = summaries[-3:] if len(summaries) >= 3 else summaries
            recent_nums = {s.chapter_number for s in recent}

            # LLM-select up to 5 more relevant earlier chapters
            candidates = [s for s in summaries if s.chapter_number not in recent_nums]
            selected = self._select_relevant_summaries(
                candidates, ch, user_message, max_count=5,
            )
            relevant = recent + [s for s in selected if s not in recent]

            lines.append("## 📚 相关章节摘要")
            if len(relevant) > 3:
                lines.append(f"(最近3章 + {len(relevant)-3}章相关)")
            for s in relevant:
                hook_note = ""
                if s.hooks_planted and s.hooks_resolved:
                    hook_note = f" [种:{len(s.hooks_planted)} 收:{len(s.hooks_resolved)}]"
                elif s.hooks_planted:
                    hook_note = f" [种:{len(s.hooks_planted)}]"
                elif s.hooks_resolved:
                    hook_note = f" [收:{len(s.hooks_resolved)}]"
                lines.append(f"- **Ch{s.chapter_number}** ({s.word_count}字, {s.mood}): {s.summary}{hook_note}")
            lines.append("")

        # 7. Style constraints + reference texts (from memory)
        style_constraints, style_references = self._fetch_style_constraints(ch)
        if style_constraints:
            lines.append("## 🖊 风格约束")
            for s in style_constraints[:3]:
                lines.append(f"- {s}")
            lines.append("")
        if style_references:
            lines.append("## 📖 参考文风样本")
            for ref in style_references[:2]:
                # Truncate long samples, keep the first ~800 chars per sample
                display = ref[:800] + ("..." if len(ref) > 800 else "")
                lines.append(f"```\n{display}\n```")
            lines.append("")

        # 9. User instruction
        if user_message:
            lines.append("## 🎯 用户指令")
            lines.append(user_message)
            lines.append("")

        # 10. On-demand context hint (what you can look up)
        lines.append("## 🔍 按需查询（需要时使用工具获取）")
        lines.append("- 全章节目录+摘要 → `write_chapter(action=read, chapter_number=0)`")
        lines.append("- 任意章节全文 → `write_chapter(action=read, chapter_number=N)`")
        lines.append("- 角色详细信息 → `memory(action=search, type=character, query=...)`")
        lines.append("- 世界观设定 → `search_lore(action=search, query=...)` 或 `memory(action=search, type=world, query=...)`")
        lines.append("- 完整伏笔报告 → `track_hooks(action=report, current_chapter=N)`")
        lines.append("- 风格约束详情 → `memory(action=search, type=style, query=...)`")
        lines.append("- 联网查资料 → `web_search(query=...)` / `web_fetch(urls=[...])`")
        # 10. Project health check — what's missing?
        missing = []
        if not outline_path.exists():
            missing.append("大纲 (outline_plot)")
        if not self.memory_dir.joinpath("MEMORY.md").exists() or len(list(self.memory_dir.glob("*.md"))) <= 1:
            missing.append("角色/世界观记忆 (memory add)")
        if not self.chapters_dir.joinpath("ch_001_*.md").exists() and not list(self.chapters_dir.glob("ch_*_*.md")):
            missing.append("还没有章节 (write chapter 1)")
        style_headers = [h for h in self._fetch_style_headers() if h.get("type") == "style"]
        if not style_headers:
            missing.append("风格偏好 (memory add type=style)")

        if missing:
            lines.append("## ⚠️ 待完善")
            for m in missing:
                lines.append(f"- 缺少 {m}")
            lines.append("")

        lines.append("**按需取用上方工具，查关键信息即可，不要全盘搜索。**")

        return "\n".join(lines)

    def _select_relevant_summaries(
        self, summaries: list, current_ch: int, query: str, max_count: int = 8,
    ) -> list:
        """Use a lightweight LLM call to pick chapter summaries relevant to the current context.

        Like claude-code's findRelevantMemories — a side query to select what matters,
        rather than blindly loading the most recent N chapters.
        """
        if len(summaries) <= max_count:
            return summaries

        # Build a compact manifest for the selector
        manifest_lines = []
        for s in summaries:
            manifest_lines.append(f"Ch{s.chapter_number} ({s.word_count}字): {s.summary[:100]}")
        manifest = "\n".join(manifest_lines)

        try:
            resp = self.call_llm(
                messages=[{"role": "user", "content": (
                    f"当前正在写第{current_ch}章。用户指令: {query}\n\n"
                    f"从以下章节摘要中选择与当前写作最相关的{max_count}章"
                    f"（按相关性排序，返回章节号列表，如 [3,7,12,5,...]）:\n\n{manifest}"
                )}],
                max_tokens=100, temperature=0,
            )
            import re, json
            raw = self.extract_text(resp.content).strip()
            # Parse chapter numbers from response
            nums = [int(n) for n in re.findall(r'\b(\d+)\b', raw) if 1 <= int(n) <= 999]
            selected = []
            for n in nums[:max_count]:
                for s in summaries:
                    if s.chapter_number == n and s not in selected:
                        selected.append(s)
                        break
            if selected:
                return selected
        except Exception:
            pass

        # Fallback: last N chapters
        return summaries[-max_count:]

    def _fetch_style_headers(self) -> list[dict]:
        """Fetch all memory file headers."""
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.memory_dir)
            return store.scan_memory_headers()
        except Exception:
            return []

    def _fetch_style_constraints(self, current_chapter: int = 0) -> tuple[list[str], list[str]]:
        """Fetch style constraints and reference texts from memory.

        Returns (constraints, reference_texts). Entries with an
        ``active_until_chapter`` frontmatter field are treated as
        reference samples — they auto-expire after that chapter.
        Entries without it are permanent constraints.
        """
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.memory_dir)
            headers = store.scan_memory_headers()
            style_headers = [h for h in headers if h.get("type") == "style"]
            constraints: list[str] = []
            references: list[str] = []
            for h in style_headers[:5]:
                content = store.read_memory(h["filename"])
                if not content:
                    continue
                fm = store._parse_simple_frontmatter(content) if content.startswith("---") else {}
                # Check expiration
                expires = fm.get("active_until_chapter", "")
                if expires:
                    try:
                        if current_chapter > int(expires):
                            continue  # expired — skip entirely
                    except ValueError:
                        pass  # malformed value — keep it
                body = content.split("---", 2)[-1].strip() if content.count("---") >= 2 else content
                if expires:
                    # Reference text — return full body
                    if body:
                        references.append(body)
                else:
                    # Permanent constraint — return one-line summary
                    first_line = body.split("\n")[0].strip()
                    if first_line and len(first_line) < 200:
                        constraints.append(first_line)
            return constraints, references
        except Exception:
            return [], []

    def _fetch_character_voices(self, active_names: set) -> list[str]:
        """Fetch 1-2 representative dialogue lines per active character from memory.

        Extracts the first quoted dialogue from each character's memory file
        to anchor the model's writing style to established voices.
        """
        import re
        samples = []
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.memory_dir)
            headers = store.scan_memory_headers()
            char_headers = [h for h in headers if h.get("type") == "character"
                           and h.get("name") in active_names]
            for h in char_headers[:5]:  # At most 5 characters
                content = store.read_memory(h["filename"])
                if not content:
                    continue
                body = content.split("---", 2)[-1].strip() if content.count("---") >= 2 else content
                # Find quoted dialogue lines
                quotes = re.findall(r'["""]([^"""]{10,80})["”"]', body)
                if not quotes:
                    quotes = re.findall(r'「([^」]{10,80})」', body)
                if quotes:
                    sample = quotes[0][:80]
                    samples.append(f"**{h['name']}**: \"{sample}\"")
                else:
                    # Fallback: first meaningful line
                    for line in body.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#") and len(line) > 10:
                            samples.append(f"**{h['name']}**: {line[:80]}")
                            break
            return samples[:8]  # Keep it compact
        except Exception:
            return []

    def prefetch_memories(self, query: str) -> list[dict[str, Any]]:
        """Prefetch relevant memories for the current user query."""
        return self._memory_manager.prefetch_all(query)

    def sync_memories(self, user_msg: str, assistant_msg: str) -> None:
        """Sync the completed turn to memory providers."""
        self._memory_manager.sync_all(user_msg, assistant_msg)

    # -- LLM call --------------------------------------------------------------

    def call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.8,
        max_tokens: int = 8192,
        max_retries: int = 3,
        extra_body: dict | None = None,
        stream: bool = False,
        tool_choice: dict | None = None,
    ) -> anthropic.types.Message | anthropic.types.RawMessageStreamEvent:
        """Make an API call to Claude, with optional streaming."""
        import time

        # Sanitize all messages to ensure API-compatible format
        clean_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                if role == "user":
                    # User messages: strings are fine
                    clean_messages.append(msg)
                else:
                    # Assistant messages: must be ContentBlock array
                    clean_messages.append({"role": role, "content": [{"type": "text", "text": content}]})
            elif isinstance(content, list):
                clean_messages.append(msg)
            else:
                clean_messages.append({"role": role, "content": [{"type": "text", "text": str(content)}]})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": self.build_system_prompt(),
            "messages": clean_messages,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if extra_body:
            kwargs["extra_body"] = extra_body

        last_error = None
        for attempt in range(max_retries):
            try:
                if stream:
                    return self.client.messages.stream(**kwargs)
                return self.client.messages.create(**kwargs)
            except anthropic.RateLimitError as e:
                last_error = e
                wait = 2 ** attempt * 5
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = e
                    wait = 2 ** attempt
                    time.sleep(wait)
                else:
                    raise

        raise last_error or RuntimeError("LLM call failed after max retries")

    def stream_with_display(self, messages, tools=None, temperature=0.8, max_tokens=8192, extra_body=None):
        """Stream LLM response with real-time text display. Returns final message."""
        import time
        last_error = None
        for attempt in range(3):
            try:
                return self._do_stream(messages, tools, temperature, max_tokens, extra_body)
            except anthropic.RateLimitError as e:
                last_error = e
                time.sleep(2 ** attempt * 5)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = e
                    time.sleep(2 ** attempt)
                else:
                    raise
        raise last_error or RuntimeError("Stream failed after 3 retries")

    def _do_stream(self, messages, tools, temperature, max_tokens, extra_body):
        """Internal streaming implementation."""
        full_text = ""
        tool_uses = []
        final_usage = None

        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=self.build_system_prompt(),
            messages=messages,
            tools=tools,
            extra_body=extra_body or {},
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        print(event.delta.text, end="", flush=True)
                        full_text += event.delta.text
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        tool_uses.append({"id": event.content_block.id, "name": event.content_block.name, "input": ""})
                elif event.type == "content_block_stop":
                    pass
                elif event.type == "message_delta":
                    if hasattr(event, "usage"):
                        final_usage = event.usage

        final_message = stream.get_final_message()
        print()  # newline after streaming
        return final_message

    @staticmethod
    def extract_text(content: list[Any], fallback_to_thinking: bool = False) -> str:
        """Extract text from response content blocks.

        By default, only returns text blocks. Set fallback_to_thinking=True
        for short queries (like title generation) where the model may only
        produce thinking blocks.
        """
        texts = []
        for block in content:
            if hasattr(block, "type"):
                if block.type == "text":
                    texts.append(block.text)
                elif fallback_to_thinking and block.type in ("thinking", "redacted_thinking"):
                    texts.append(getattr(block, "thinking", "") or getattr(block, "text", ""))
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif fallback_to_thinking and block.get("type") in ("thinking", "redacted_thinking"):
                    texts.append(block.get("thinking", "") or block.get("text", ""))
        return "\n".join(t for t in texts if t)
