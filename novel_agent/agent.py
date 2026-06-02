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
                self.conversation_history = history
                logger.info("Loaded session: %d messages", len(history))
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
        """Initialize the context compression engine."""
        context_length = int(os.getenv("NOVEL_AGENT_CONTEXT_LENGTH", "200000"))
        threshold_pct = float(os.getenv("NOVEL_AGENT_COMPRESS_THRESHOLD", "0.85"))
        self.context_engine = NovelCompressor(
            model=self.model,
            context_length=context_length,
            threshold_percent=threshold_pct,
            tail_token_budget=int(context_length * 0.15),  # 150K for 1M context
        )
        logger.info(
            "Context engine initialized: %s (context=%d, threshold=%.0f%%)",
            self.context_engine.name, context_length, threshold_pct * 100,
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
            "- 对话模式：不确定时主动询问，提出2-3个具体选项——尤其在情节走向、角色决策、风格选择上\n"
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
        lines.append(f"## 📖 当前进度")
        lines.append(f"第 {ch}/{self.total_chapters} 章 | {total_written} 字 | 每章目标 {self.chapter_words} 字 | {state.phase}")
        if state.current_scene:
            lines.append(f"当前场景: {state.current_scene}")
        lines.append("")

        # 2. Previous chapter ending (most important anchor)
        if ch > 1:
            prev_path = self.chapter_path(ch - 1)
            if prev_path.exists():
                prev_text = prev_path.read_text(encoding="utf-8")
                ending = prev_text[-1500:] if len(prev_text) > 1500 else prev_text
                lines.append("## 📝 前一章结尾")
                lines.append("```")
                lines.append(ending.strip())
                lines.append("```")
                lines.append("")

        # 3. Multi-level outline
        outline_path = self.project_dir / "outline.md"
        if outline_path.exists():
            outline_text = outline_path.read_text(encoding="utf-8")

            # 3a. Book-level framing (first ~500 chars — overall arc, theme, ending vision)
            book_framing = outline_text[:500].strip()
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
                    vol_end = min(len(outline_text), current_vol.end() + 800)
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
                    entry = outline_text[max(0, start - 20):min(len(outline_text), end + 400)]
                    label = "本章大纲" if cn == ch else ("前一章大纲" if cn < ch else "下一章大纲")
                    lines.append(f"## 📋 {label} (第{cn}章)")
                    lines.append(entry.strip())
                    lines.append("")
        else:
            # No outline file — suggest creating one
            lines.append("## 📋 大纲")
            lines.append(f"（尚无 outline.md，建议创建以提供全局规划。目前将基于章节摘要写作。）")
            lines.append("")

        # 4. Scene characters
        if state.characters:
            active = {n: c for n, c in state.characters.items() if c.alive}
            if active:
                lines.append("## 🎬 当前场景角色")
                for name, char in active.items():
                    loc = f" @{char.current_location}" if char.current_location else ""
                    goal = f" → {char.goal}" if char.goal else ""
                    lines.append(f"- **{name}**: {char.emotional_state}{loc}{goal}")
                lines.append("")

        # 5. Hooks by scope (book → volume → arc → chapter)
        active_hooks = self.hook_ledger.get_active()
        if active_hooks:
            lines.append("## 🔮 伏笔")
            book_hooks = [h for h in active_hooks if getattr(h, 'scope', None) == "book"]
            volume_hooks = [h for h in active_hooks if getattr(h, 'scope', None) == "volume"]
            chapter_hooks = [h for h in active_hooks if getattr(h, 'scope', None) not in ("book", "volume")]
            overdue = self.hook_ledger.get_overdue(ch)

            if overdue:
                lines.append(f"**⚠️ 过期 ({len(overdue)}):** {', '.join(f'[{h.id}]' for h in overdue)}")

            if book_hooks:
                lines.append("**全书:**")
                for h in book_hooks:
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}")
            if volume_hooks:
                lines.append("**本卷:**")
                for h in volume_hooks:
                    urgent = " ⚠️" if h.target_chapter and h.target_chapter <= ch + 3 else ""
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}{urgent}")
            if chapter_hooks[:8]:
                lines.append("**章节:**")
                for h in sorted(chapter_hooks, key=lambda h: h.target_chapter or 999)[:8]:
                    urgent = " ⚠️" if h.target_chapter and h.target_chapter <= ch + 1 else ""
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}{urgent}")
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

        # 7. Style constraints (from memory)
        style_memories = self._fetch_style_constraints()
        if style_memories:
            lines.append("## 🖊 风格约束")
            for s in style_memories[:2]:
                lines.append(f"- {s}")
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

    def _fetch_style_constraints(self) -> list[str]:
        """Fetch style constraints from memory system."""
        try:
            from novel_agent.memory.memory_store import MemoryStore
            store = MemoryStore(self.memory_dir)
            headers = store.scan_memory_headers()
            style_headers = [h for h in headers if h.get("type") == "style"]
            constraints = []
            for h in style_headers[:3]:
                content = store.read_memory(h["filename"])
                if content:
                    # Extract first meaningful line after frontmatter
                    body = content.split("---", 2)[-1].strip() if content.count("---") >= 2 else content
                    first_line = body.split("\n")[0].strip()
                    if first_line and len(first_line) < 200:
                        constraints.append(first_line)
            return constraints
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
    ) -> anthropic.types.Message:
        """Make an API call to Claude with retry logic."""
        import time

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": self.build_system_prompt(),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_error = None
        for attempt in range(max_retries):
            try:
                return self.client.messages.create(**kwargs)
            except anthropic.RateLimitError as e:
                last_error = e
                wait = 2 ** attempt * 5
                logger.warning("Rate limited, retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = e
                    wait = 2 ** attempt
                    logger.warning("Server error %d, retrying in %ds", e.status_code, wait)
                    time.sleep(wait)
                else:
                    raise

        raise last_error or RuntimeError("LLM call failed after max retries")

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
