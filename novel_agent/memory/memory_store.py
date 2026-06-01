"""File-based memory persistence with MEMORY.md indexing.

Borrowed from hermes-agent `tools/memory_tool.py` MemoryStore and
claude-code `src/memdir/memdir.ts`.

Two stores:
  - MEMORY.md: index of all memory files (one-line per memory)
  - Individual .md files: actual memory content with frontmatter

The index (MEMORY.md) is always loaded. Individual files are loaded
on demand via side-query recall.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from novel_agent.utils.files import read_file_safe, write_file_atomic, ensure_dir

logger = logging.getLogger(__name__)

MEMORY_INDEX_FILE = "MEMORY.md"
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000


class MemoryStore:
    """File-based memory storage with index file.

    Manages two layers:
      - Index (MEMORY.md): always loaded, one-line entries pointing to detail files
      - Detail files: individual .md files with full frontmatter + content
    """

    def __init__(self, memory_dir: str | Path):
        self.memory_dir = ensure_dir(Path(memory_dir))
        self.index_path = self.memory_dir / MEMORY_INDEX_FILE

    # -- Index operations ------------------------------------------------------

    def read_index(self) -> str:
        """Read MEMORY.md content, truncated if needed."""
        content = read_file_safe(self.index_path) or ""
        return self._truncate_index(content)

    def add_to_index(self, title: str, filename: str, hook: str) -> None:
        """Add or update an entry in MEMORY.md.

        Args:
            title: Display title (e.g., "张三的角色档案")
            filename: The .md file name (e.g., "char-zhang-san.md")
            hook: One-line description (~150 chars max)
        """
        content = read_file_safe(self.index_path) or ""
        line = f"- [{title}]({filename}) — {hook}"

        # Check if entry for this file already exists
        if f"({filename})" in content:
            # Replace existing line
            lines = content.strip().split("\n")
            new_lines = []
            for l in lines:
                if f"({filename})" in l:
                    new_lines.append(line)
                else:
                    new_lines.append(l)
            content = "\n".join(new_lines)
        else:
            # Append new entry
            content = content.strip() + "\n" + line if content.strip() else line

        write_file_atomic(self.index_path, content.strip() + "\n")

    def remove_from_index(self, filename: str) -> None:
        """Remove an entry from MEMORY.md."""
        content = read_file_safe(self.index_path)
        if not content:
            return
        lines = content.strip().split("\n")
        new_lines = [l for l in lines if f"({filename})" not in l]
        write_file_atomic(self.index_path, "\n".join(new_lines).strip() + "\n")

    # -- Detail file operations ------------------------------------------------

    def write_memory(
        self,
        filename: str,
        frontmatter: dict[str, str],
        content: str,
    ) -> Path:
        """Write a memory detail file with frontmatter.

        Args:
            filename: File name (e.g., "char-zhang-san.md")
            frontmatter: Dict with name, description, type fields
            content: Memory body content

        Returns:
            Path to the written file.
        """
        filepath = self.memory_dir / filename

        # Build frontmatter
        fm_lines = ["---"]
        for key in ["name", "description", "type"]:
            if key in frontmatter:
                fm_lines.append(f"{key}: {frontmatter[key]}")
        if "related" in frontmatter and frontmatter["related"]:
            related = frontmatter["related"]
            if isinstance(related, list):
                fm_lines.append(f"related: [{', '.join(related)}]")
            else:
                fm_lines.append(f"related: {related}")
        fm_lines.append("---")

        full_content = "\n".join(fm_lines) + "\n\n" + content.strip() + "\n"
        write_file_atomic(filepath, full_content)
        return filepath

    def read_memory(self, filename: str) -> Optional[str]:
        """Read a memory detail file. Returns None if not found."""
        filepath = self.memory_dir / filename
        return read_file_safe(filepath)

    def delete_memory(self, filename: str) -> bool:
        """Delete a memory detail file. Returns True if deleted."""
        filepath = self.memory_dir / filename
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def list_memory_files(self) -> list[Path]:
        """List all memory detail files (excluding MEMORY.md)."""
        files = []
        for p in self.memory_dir.glob("*.md"):
            if p.name != MEMORY_INDEX_FILE:
                files.append(p)
        return sorted(files)

    def scan_memory_headers(self) -> list[dict[str, str]]:
        """Scan all memory files and extract frontmatter headers.

        Returns list of {"filename": ..., "name": ..., "description": ..., "type": ...}
        Used by recall to select relevant memories.
        """
        results = []
        for filepath in self.list_memory_files():
            content = read_file_safe(filepath)
            if not content or not content.startswith("---"):
                continue

            # Parse YAML frontmatter (simple, no pyyaml needed)
            fm = self._parse_simple_frontmatter(content)
            results.append({
                "filename": filepath.name,
                "name": fm.get("name", filepath.stem),
                "description": fm.get("description", ""),
                "type": fm.get("type", ""),
                "mtime": filepath.stat().st_mtime if filepath.exists() else 0,
            })

        return results

    # -- Helpers ---------------------------------------------------------------

    @staticmethod
    def _truncate_index(content: str) -> str:
        """Truncate index to line and byte limits."""
        trimmed = content.strip()
        lines = trimmed.split("\n")
        line_count = len(lines)
        byte_count = len(trimmed)

        if line_count <= MAX_INDEX_LINES and byte_count <= MAX_INDEX_BYTES:
            return trimmed

        truncated = "\n".join(lines[:MAX_INDEX_LINES])
        if len(truncated) > MAX_INDEX_BYTES:
            cut_at = truncated.rfind("\n", 0, MAX_INDEX_BYTES)
            truncated = truncated[:cut_at] if cut_at > 0 else truncated[:MAX_INDEX_BYTES]

        return truncated + (
            f"\n\n> WARNING: MEMORY.md is too large ({line_count} lines, {byte_count} bytes). "
            f"Only part of it was loaded. Keep index entries to one line under ~150 chars."
        )

    @staticmethod
    def _parse_simple_frontmatter(content: str) -> dict[str, str]:
        """Parse simple YAML frontmatter without pyyaml dependency."""
        if not content.startswith("---"):
            return {}

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}

        fm = {}
        for line in parts[1].strip().split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                fm[key] = value

        return fm
