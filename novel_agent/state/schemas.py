"""Pydantic models for novel state entities.

Borrowed from inkos `packages/core/src/models/` — structured truth files
with JSON for machine processing and markdown projections for human reading.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# -- Hook (Foreshadowing) ------------------------------------------------------

class HookStatus(str, Enum):
    PLANTED = "planted"
    MENTIONED = "mentioned"
    RESOLVED = "resolved"
    DEFERRED = "deferred"
    ABANDONED = "abandoned"


class HookType(str, Enum):
    DIRECT = "direct"       # 直接伏笔：预言、警告
    SYMBOLIC = "symbolic"   # 象征伏笔：物品/意象
    DIALOGUE = "dialogue"   # 对话伏笔：后续才意识到的双重含义
    ACTION = "action"       # 行动伏笔：小事件预示大事件
    NAMING = "naming"       # 命名伏笔：标题/姓名中的隐藏意义


class HookScope(str, Enum):
    BOOK = "book"       # 全书级：贯穿整部小说的核心谜题
    VOLUME = "volume"   # 卷级：当前卷要解决的主要线索
    ARC = "arc"         # 弧线级：跨多章的次要线索
    CHAPTER = "chapter" # 章节级：几章内回收的小伏笔


class Hook(BaseModel):
    """A single foreshadowing hook in the ledger."""
    id: str = Field(description="Unique hook ID, e.g. 'hook-003'")
    description: str = Field(description="What is being foreshadowed")
    hook_type: HookType = Field(default=HookType.DIRECT)
    scope: HookScope = Field(default=HookScope.CHAPTER)
    status: HookStatus = Field(default=HookStatus.PLANTED)

    planted_chapter: int = Field(description="Chapter where the hook was first planted")
    target_chapter: Optional[int] = Field(default=None, description="Planned chapter for resolution")
    resolved_chapter: Optional[int] = Field(default=None, description="Chapter where it was resolved")

    related_characters: list[str] = Field(default_factory=list)
    related_hooks: list[str] = Field(default_factory=list, description="IDs of related hooks")

    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# -- Character State -----------------------------------------------------------

class CharacterState(BaseModel):
    """Runtime state of a character at a given point in the novel."""
    name: str
    current_location: str = ""
    emotional_state: str = ""
    goal: str = ""
    alive: bool = True
    chapter_updated: int = 0


# -- Chapter Summary -----------------------------------------------------------

class ChapterSummary(BaseModel):
    """Summary of a completed chapter for context window management."""
    chapter_number: int
    title: str = ""
    word_count: int = 0
    summary: str = Field(default="", description="2-3 sentence summary of key events")
    key_events: list[str] = Field(default_factory=list)
    characters_appearing: list[str] = Field(default_factory=list)
    hooks_planted: list[str] = Field(default_factory=list)
    hooks_resolved: list[str] = Field(default_factory=list)
    mood: str = ""  # e.g. "tense", "hopeful", "tragic"


# -- Novel State ---------------------------------------------------------------

class NovelState(BaseModel):
    """Top-level novel state, persisted as state/current_state.json."""
    novel_title: str = ""
    current_chapter: int = 0
    current_scene: str = ""
    total_words_written: int = 0
    phase: str = Field(default="writing", description="outline | writing | revision | complete")

    characters: dict[str, CharacterState] = Field(default_factory=dict)
    active_conflicts: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
