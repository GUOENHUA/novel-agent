"""Truth file management — JSON + markdown dual storage.

Borrowed from inkos `packages/core/src/state/` — structured state files
that are both machine-parseable (JSON schemas) and human-readable (markdown).

Truth files:
  current_state.json  — novel-level state (phase, progress, character states)
  hooks.json          — hook ledger (foreshadowing lifecycle)
  chapter_summaries.json — per-chapter summaries for context management
  character_matrix.md — human-readable character relationship matrix
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from novel_agent.state.schemas import (
    NovelState,
    ChapterSummary,
    CharacterState,
)
from novel_agent.utils.files import ensure_dir, read_file_safe, write_file_atomic

logger = logging.getLogger(__name__)


class TruthFileManager:
    """Manages the novel's truth files in the state/ directory."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = ensure_dir(Path(state_dir))
        self.state_path = self.state_dir / "current_state.json"
        self.hooks_path = self.state_dir / "hooks.json"
        self.summaries_path = self.state_dir / "chapter_summaries.json"
        self.matrix_path = self.state_dir / "character_matrix.md"

    # -- Novel state -----------------------------------------------------------

    def load_state(self) -> NovelState:
        """Load novel state, creating default if missing."""
        raw = read_file_safe(self.state_path)
        if raw:
            try:
                return NovelState.model_validate_json(raw)
            except Exception as e:
                logger.warning("Failed to parse state file, using default: %s", e)
        return NovelState()

    def save_state(self, state: NovelState) -> None:
        """Persist novel state."""
        state.last_updated = datetime.now().isoformat()
        write_file_atomic(
            self.state_path,
            state.model_dump_json(indent=2, exclude_none=True),
        )

    def update_character(self, name: str, **kwargs) -> None:
        """Update or create a character's runtime state."""
        state = self.load_state()
        if name in state.characters:
            char = state.characters[name]
            for k, v in kwargs.items():
                if hasattr(char, k):
                    setattr(char, k, v)
        else:
            state.characters[name] = CharacterState(name=name, **kwargs)
        self.save_state(state)

    # -- Chapter summaries -----------------------------------------------------

    def load_summaries(self) -> list[ChapterSummary]:
        """Load all chapter summaries."""
        raw = read_file_safe(self.summaries_path)
        if raw:
            try:
                data = json.loads(raw)
                return [ChapterSummary.model_validate(s) for s in data]
            except Exception as e:
                logger.warning("Failed to parse summaries: %s", e)
        return []

    def save_summaries(self, summaries: list[ChapterSummary]) -> None:
        """Persist chapter summaries."""
        data = [s.model_dump(exclude_none=True) for s in summaries]
        write_file_atomic(self.summaries_path, json.dumps(data, ensure_ascii=False, indent=2))

    def add_summary(self, summary: ChapterSummary) -> None:
        """Add or update a chapter summary."""
        summaries = self.load_summaries()
        # Replace if exists, append if new
        existing = [s for s in summaries if s.chapter_number == summary.chapter_number]
        if existing:
            idx = summaries.index(existing[0])
            summaries[idx] = summary
        else:
            summaries.append(summary)
            summaries.sort(key=lambda s: s.chapter_number)
        self.save_summaries(summaries)

    def get_summary(self, chapter_num: int) -> Optional[ChapterSummary]:
        """Get summary for a specific chapter."""
        summaries = self.load_summaries()
        for s in summaries:
            if s.chapter_number == chapter_num:
                return s
        return None

    # -- Character matrix (markdown) -------------------------------------------

    def read_matrix(self) -> str:
        """Read the character relationship matrix."""
        return read_file_safe(self.matrix_path) or "# Character Matrix\n\nNo entries yet."

    def write_matrix(self, content: str) -> None:
        """Write the character relationship matrix."""
        write_file_atomic(self.matrix_path, content)

    # -- Bulk context for LLM --------------------------------------------------

    def build_context_block(self, max_chars: int = 15000) -> str:
        """Build a compact context block for injection into LLM prompts.

        Includes current state + recent chapter summaries + hook overview.
        """
        parts = []

        state = self.load_state()
        parts.append(f"## Current State\nChapter: {state.current_chapter}/{state.total_words_written} words")
        parts.append(f"Phase: {state.phase}")

        if state.characters:
            parts.append("\nCharacters:")
            for name, char in state.characters.items():
                status = "alive" if char.alive else "dead"
                loc = f" at {char.current_location}" if char.current_location else ""
                parts.append(f"  - {name}: {char.emotional_state} | {status}{loc}")

        # Recent summaries
        summaries = self.load_summaries()
        if summaries:
            recent = summaries[-5:]  # Last 5 chapters
            parts.append("\nRecent Chapters:")
            for s in recent:
                parts.append(f"  Ch{s.chapter_number}: {s.summary[:200]}")

        result = "\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...[truncated]"
        return result
