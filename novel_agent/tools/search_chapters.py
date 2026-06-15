"""Chapter content search tool — grep across all chapter files.

Borrowed pattern from claude-code's GrepTool for content search.
Supports keyword grep with context snippets, chapter filtering,
and is designed for future embedding search extension.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def search_chapters_handler(args: dict[str, Any], **kwargs) -> str:
    """Search chapter files for a keyword, returning matches with context."""
    query = args.get("query", "").strip()
    if not query:
        return tool_error("query is required.")

    max_results = args.get("max_results", 10)
    chapter_filter = args.get("chapter_filter")

    chapters_dir = kwargs.get("chapters_dir", ".")
    chapters_path = Path(chapters_dir)

    if chapter_filter:
        files = sorted(chapters_path.glob(f"ch_{chapter_filter:03d}_*.md"))
    else:
        files = sorted(chapters_path.glob("ch_*_*.md"))

    if not files:
        return tool_result(success=True, query=query, matches=[],
                           hint="No chapter files found.")

    matches = []
    for path in files:
        if len(matches) >= max_results:
            break
        ch_num = _extract_chapter_num(path)
        try:
            lines = path.read_text(encoding="utf-8").split("\n")
        except (OSError, UnicodeDecodeError):
            logger.warning("Failed to read %s", path)
            continue

        q_lower = query.lower()
        for i, line in enumerate(lines):
            if len(matches) >= max_results:
                break
            if q_lower in line.lower():
                ctx_start = max(0, i - 1)
                ctx_end = min(len(lines), i + 2)
                snippet_lines = []
                for j in range(ctx_start, ctx_end):
                    marker = ">>>" if j == i else "   "
                    snippet_lines.append(f"{j + 1:>5} {marker} {lines[j]}")
                matches.append({
                    "chapter": ch_num,
                    "file": path.name,
                    "line": i + 1,
                    "snippet": "\n".join(snippet_lines),
                })

    return tool_result(
        success=True,
        query=query,
        matches=matches,
        total_matches=len(matches),
        searched_files=len(files),
        hint=(
            f"Found {len(matches)} match(es) across {len(files)} file(s). "
            "Use write_chapter(action=read, chapter_number=N, offset=M) "
            "to read the full surrounding context."
        ),
    )


def _extract_chapter_num(path: Path) -> int:
    m = re.match(r"ch_(\d+)", path.stem)
    return int(m.group(1)) if m else 0


# -- Tool schema ---------------------------------------------------------------

SEARCH_CHAPTERS_SCHEMA = {
    "name": "search_chapters",
    "description": (
        "在所有章节文件中搜索关键词，返回匹配章节号及上下文片段。\n"
        "用于查找角色对话、场景细节、伏笔原文等已在正文中出现过的内容。\n"
        "仅匹配章节正文，不搜索记忆或状态文件。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（支持中文）。不区分大小写子字符串匹配。"
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "最大返回匹配数，默认 10。"
            },
            "chapter_filter": {
                "type": "integer",
                "description": "限定只搜索某个章节（按章节号）。不填则搜索全部。"
            },
        },
        "required": ["query"],
    },
}

registry.register(
    name="search_chapters",
    toolset="writing",
    schema=SEARCH_CHAPTERS_SCHEMA,
    handler=search_chapters_handler,
    description="在章节正文中搜索关键词（返回行号+上下文片段）",
    emoji="🔍",
)
