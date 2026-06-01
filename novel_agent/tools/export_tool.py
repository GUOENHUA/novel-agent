"""Export tool — export chapters to various formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result


def export_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle export requests."""
    action = args.get("action", "chapter")
    chapters_dir = Path(kwargs.get("chapters_dir", "."))

    if action == "chapter":
        return _export_chapter(args, chapters_dir)
    elif action == "manuscript":
        return _export_manuscript(args, chapters_dir)
    elif action == "stats":
        return _export_stats(args, chapters_dir)
    else:
        return tool_error(f"Unknown action: {action}")


def _export_chapter(args: dict[str, Any], chapters_dir: Path) -> str:
    chapter_num = args.get("chapter_number", 0)
    fmt = args.get("format", "md")

    path = chapters_dir / f"ch_{chapter_num:02d}.md"
    if not path.exists():
        return tool_error(f"Chapter {chapter_num} not found.")

    content = path.read_text(encoding="utf-8")
    word_count = len(content)

    if fmt == "txt":
        # Strip markdown headers for plain text
        import re
        content = re.sub(r"^#.*$", "", content, flags=re.MULTILINE)

    return tool_result(
        success=True,
        chapter=chapter_num,
        format=fmt,
        content=content,
        word_count=word_count,
    )


def _export_manuscript(args: dict[str, Any], chapters_dir: Path) -> str:
    """Export all chapters as a single manuscript."""
    chapters = sorted(chapters_dir.glob("ch_*.md"))
    if not chapters:
        return tool_error("No chapters found.")

    parts = []
    total_words = 0
    for ch in chapters:
        content = ch.read_text(encoding="utf-8")
        parts.append(content)
        total_words += len(content)
        parts.append("\n\n---\n\n")

    manuscript = "\n".join(parts)
    output_path = chapters_dir.parent / "manuscript.md"
    output_path.write_text(manuscript, encoding="utf-8")

    return tool_result(
        success=True,
        chapters=len(chapters),
        total_words=total_words,
        output_path=str(output_path),
    )


def _export_stats(args: dict[str, Any], chapters_dir: Path) -> str:
    """Export writing statistics."""
    chapters = sorted(chapters_dir.glob("ch_*.md"))
    stats = []
    total = 0

    for ch in chapters:
        content = ch.read_text(encoding="utf-8")
        wc = len(content)
        total += wc
        stats.append({
            "chapter": ch.stem,
            "words": wc,
        })

    avg = total // len(chapters) if chapters else 0

    return tool_result(
        success=True,
        total_chapters=len(chapters),
        total_words=total,
        average_words=avg,
        per_chapter=stats,
    )


EXPORT_TOOL_SCHEMA = {
    "name": "export_chapter",
    "description": (
        "导出章节或整部手稿。支持：\n"
        "- chapter: 导出单章（md/txt 格式）\n"
        "- manuscript: 导出全部章节为单个手稿文件\n"
        "- stats: 导出写作统计（字数、章节数、平均值）"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["chapter", "manuscript", "stats"],
                "description": "导出操作。"
            },
            "chapter_number": {
                "type": "integer",
                "description": "导出的章节号（chapter 时使用）。"
            },
            "format": {
                "type": "string",
                "enum": ["md", "txt"],
                "description": "导出格式（chapter 时使用，默认 md）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="export_chapter",
    toolset="export",
    schema=EXPORT_TOOL_SCHEMA,
    handler=export_tool_handler,
    description="导出章节/手稿/统计",
    emoji="📦",
)
