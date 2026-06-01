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
        chapter_words: int = DEFAULT_CHAPTER_WORDS,
        total_chapters: int = DEFAULT_TOTAL_CHAPTERS,
        total_words: int = DEFAULT_TOTAL_WORDS,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.model = model or os.getenv("NOVEL_AGENT_MODEL", DEFAULT_WRITER_MODEL)
        self.chapter_words = chapter_words
        self.total_chapters = total_chapters
        self.total_words = total_words

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

        # Subsystems
        self._memory_manager: MemoryManager = MemoryManager()
        self._context_engine = None
        self._skill_loader = None

        # Initialize memory system
        self._init_memory()

        # Initialize state system
        self._init_state()

        # Initialize skills system
        self._init_skills()

        # Initialize context engine
        self._init_context()

        # Conversation history
        self.conversation_history: list[dict[str, Any]] = []

        logger.info("Agent initialized: project=%s model=%s", self.project_dir, self.model)

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
        threshold_pct = float(os.getenv("NOVEL_AGENT_COMPRESS_THRESHOLD", "0.70"))
        self.context_engine = NovelCompressor(
            model=self.model,
            context_length=context_length,
            threshold_percent=threshold_pct,
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

        state = self.truth_files.load_state()
        lines = []

        # 1. Progress bar
        existing = list(self.chapters_dir.glob("ch_*.md"))
        total_written = sum(len(p.read_text(encoding="utf-8")) for p in existing)
        ch = state.current_chapter or (len(existing) + 1)
        lines.append(f"## 📖 当前进度")
        lines.append(f"第 {ch} 章 | 已写 {total_written}/{self.total_words} 字 | {state.phase}")
        if state.current_scene:
            lines.append(f"当前场景: {state.current_scene}")
        lines.append("")

        # 2. Previous chapter ending (most important anchor)
        if ch > 1:
            prev_path = self.chapters_dir / f"ch_{ch - 1:02d}.md"
            if prev_path.exists():
                prev_text = prev_path.read_text(encoding="utf-8")
                ending = prev_text[-600:] if len(prev_text) > 600 else prev_text
                lines.append("## 📝 前一章结尾")
                lines.append("```")
                lines.append(ending.strip())
                lines.append("```")
                lines.append("")

        # 3. Current chapter outline
        outline_path = self.project_dir / "outline.md"
        if outline_path.exists():
            outline_text = outline_path.read_text(encoding="utf-8")
            pattern = rf"第\s*{ch}\s*章"
            match = re.search(pattern, outline_text)
            if match:
                start = max(0, match.start() - 30)
                end = min(len(outline_text), match.end() + 400)
                lines.append("## 📋 本章大纲")
                lines.append(outline_text[start:end].strip())
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

        # 5. Upcoming hooks (urgency-sorted)
        active_hooks = self.hook_ledger.get_active()
        if active_hooks:
            # Sort: hooks with near target chapters first
            sorted_hooks = sorted(active_hooks, key=lambda h: h.target_chapter or 999)
            upcoming = [h for h in sorted_hooks if h.target_chapter and h.target_chapter <= ch + 2]
            other = [h for h in sorted_hooks if h not in upcoming]

            lines.append("## 🔮 伏笔")
            if upcoming:
                lines.append("**即将到期（本周必须处理）:**")
                for h in upcoming:
                    lines.append(f"- [{h.id}] {h.description} → 第{h.target_chapter}章回收 ⚠️")
            if other[:5]:
                lines.append("**活跃:**")
                for h in other[:5]:
                    target = f" → 第{h.target_chapter}章" if h.target_chapter else ""
                    lines.append(f"- [{h.id}] {h.description}{target}")
            overdue = self.hook_ledger.get_overdue(ch)
            if overdue:
                lines.append(f"**⚠️ 过期未回收 ({len(overdue)}):** {', '.join(f'[{h.id}]' for h in overdue)}")
            lines.append("")

        # 6. Recent chapter summaries
        summaries = self.truth_files.load_summaries()
        if summaries:
            recent = summaries[-5:]
            lines.append("## 📚 最近章节摘要")
            for s in recent:
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

        # 8. User instruction
        if user_message:
            lines.append("## 🎯 用户指令")
            lines.append(user_message)
            lines.append("")

        return "\n".join(lines)

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
    def extract_text(content: list[Any]) -> str:
        """Extract text from response content blocks, skipping thinking blocks."""
        texts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
        return "\n".join(texts)
