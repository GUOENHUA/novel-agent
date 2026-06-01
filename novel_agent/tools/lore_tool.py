"""World-building search tool — search across all lore and character info.

Searches across memory files (character/world/plot/style), truth files,
and chapter summaries to find relevant world-building information.
"""

from __future__ import annotations

import json
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result


def lore_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle lore/world-building search requests."""
    action = args.get("action", "search")
    query = args.get("query", "").strip()

    if action == "search":
        return _handle_search(args, kwargs)
    elif action == "world_status":
        return _handle_world_status(args, kwargs)
    else:
        return tool_error(f"Unknown action: {action}")


def _handle_search(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build a directive for searching world-building information.

    The agent should search through:
    1. Memory files (character/world/plot/style types)
    2. Truth files (current_state, chapter_summaries, character_matrix)
    3. Hook ledger
    4. Chapter files
    """
    query = args["query"]
    category = args.get("category", "all")

    category_hint = ""
    if category == "character":
        category_hint = "\n优先搜索角色记忆（type=character）和角色状态。"
    elif category == "world":
        category_hint = "\n优先搜索世界观记忆（type=world）和设定文档。"
    elif category == "plot":
        category_hint = "\n优先搜索情节记忆（type=plot）、伏笔账本和章节摘要。"

    directive = f"""## 世界观搜索: {query}{category_hint}

请搜索以下信息来源获取相关信息：

1. 记忆系统 — 使用 memory search 查找 relevant memories
2. 状态文件 — 检查 current_state.json 和 chapter_summaries
3. 伏笔账本 — 检查 hooks.json 中是否有相关信息
4. 章节文件 — 如果需要，搜索 chapters/ 目录

搜索后汇总所有相关信息，标注来源。如果信息之间存在矛盾，明确指出。
如果找不到相关信息，说明原因。

搜索关键词: {query}
"""

    return tool_result(
        success=True,
        action="search",
        query=query,
        category=category,
        directive=directive,
        hint=(
            "Use memory tool with action=search to find relevant memories. "
            "Use truth_files context to check current state. "
            "Use hook_ledger to check related hooks. "
            "Read chapter files if needed."
        ),
    )


def _handle_world_status(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build a world-building status overview directive."""
    directive = """## 世界观构建状态总览

请检查并汇总：

### 设定完整性
- 地理/地域: 是否有足够的具体地点描述？
- 历史/年代: 关键历史事件是否清晰？
- 社会/政治: 权力结构和势力关系是否明确？
- 文化/习俗: 是否有独特的文化细节（不仅仅是通用设定）？
- 魔法/科技 (如适用): 规则体系是否清晰？能力与限制是否平衡？

### 深度检查
- 每个已建立的设定是否至少有 2-3 个具体细节？
- 设定之间是否相互关联？（魔法影响政治、地理塑造文化）
- 是否有"冰山"——暗示了但未完全解释的更深系统？
- 是否有设定矛盾？（两个不同章节对同一事物的不同描述）

### 薄弱点
列出在构建中最薄弱的 3 个领域，每个给出加强建议。
"""

    return tool_result(
        success=True,
        action="world_status",
        directive=directive,
    )


# -- Tool schema ---------------------------------------------------------------

LORE_TOOL_SCHEMA = {
    "name": "search_lore",
    "description": (
        "搜索小说世界观、角色、情节相关信息。跨记忆系统、状态文件、"
        "伏笔账本和章节文件进行综合搜索。\n\n"
        "操作：search（搜索特定信息）、world_status（世界观构建总览）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "world_status"],
                "description": "操作类型。search=搜索，world_status=世界观状态总览。"
            },
            "query": {
                "type": "string",
                "description": "搜索关键词或问题（search 时使用）。"
            },
            "category": {
                "type": "string",
                "enum": ["all", "character", "world", "plot"],
                "description": "搜索范围限定。默认 all。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="search_lore",
    toolset="writing",
    schema=LORE_TOOL_SCHEMA,
    handler=lore_tool_handler,
    description="搜索小说世界观/角色/情节信息（跨记忆/状态/章节综合搜索）",
    emoji="📚",
)
