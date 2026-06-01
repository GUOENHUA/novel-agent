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

        # Anthropic client
        api_key = self._resolve_api_key()
        self.client = anthropic.Anthropic(api_key=api_key)

        # Project paths
        self.memory_dir = ensure_dir(self.project_dir / "memory")
        self.chapters_dir = ensure_dir(self.project_dir / "chapters")
        self.state_dir = ensure_dir(self.project_dir / "state")

        # Subsystems (lazy-loaded by phases)
        self._memory_manager = None
        self._context_engine = None
        self._skill_loader = None

        # Conversation history
        self.conversation_history: list[dict[str, Any]] = []

        logger.info("Agent initialized: project=%s model=%s", self.project_dir, self.model)

    @staticmethod
    def _resolve_api_key() -> str:
        """Resolve Anthropic API key from env or .env file."""
        dotenv.load_dotenv()
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Set it in .env or environment."
            )
        return key

    # -- System prompt ---------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Build the system prompt for this agent.

        Includes CRAFT.md and ANTI_SLOP.md as core education.
        Memory context and skill listings are added later (Phase 2+).
        """
        parts = []

        # Core identity
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
            "- 具体胜过抽象（"一只松鸦"胜过"一只鸟"）\n"
            "- 变化情感强度：安静/爆发/恐惧/解脱/无聊/惊奇/恐怖\n"
        )

        # Craft education (always loaded, ~8K tokens)
        craft_path = Path(__file__).parent / "craft" / "CRAFT.md"
        if craft_path.exists():
            parts.append(f"\n\n## 写作工艺参考\n\n{craft_path.read_text(encoding='utf-8')}")

        # Anti-slop reference
        antislop_path = Path(__file__).parent / "craft" / "ANTI_SLOP.md"
        if antislop_path.exists():
            parts.append(f"\n\n## AI 痕迹检测参考\n\n{antislop_path.read_text(encoding='utf-8')}")

        return "\n".join(parts)

    # -- LLM call --------------------------------------------------------------

    def call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.8,
        max_tokens: int = 8192,
    ) -> anthropic.types.Message:
        """Make an API call to Claude."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": self.build_system_prompt(),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        return self.client.messages.create(**kwargs)
