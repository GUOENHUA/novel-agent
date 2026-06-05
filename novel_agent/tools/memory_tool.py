"""Memory tool — registers with ToolRegistry.

The actual handler is in BuiltinProvider. This module provides
the registry registration and a standalone handler for direct use.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from novel_agent.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# The actual implementation lives in BuiltinProvider.
# This module provides a thin adapter for ToolRegistry registration.

_provider: Optional[Any] = None  # BuiltinProvider instance


def set_provider(provider: Any) -> None:
    """Set the builtin provider instance to use for tool dispatch."""
    global _provider
    _provider = provider


def memory_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Tool handler for memory CRUD. Delegates to the builtin provider."""
    if _provider is None:
        return tool_error("Memory system is not initialized.")
    return _provider.handle_tool_call("memory", args)


# Register with the tool registry
MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "管理持久小说记忆，跨会话保持。四种类型：character（角色）、"
        "world（世界观）、plot（情节）、style（风格）。\n\n"
        "何时保存（主动做，不要等被要求）：\n"
        "- 引入新角色或揭示角色的重要信息时\n"
        "- 建立新的世界观设定时\n"
        "- 规划情节或调整走向时\n"
        "- 用户给出风格反馈或确认偏好时\n\n"
        "操作：add（新建，需要 type/name/description/content）、"
        "update（更新已有，需要 type/name/content）、"
        "delete（删除，需要 name 或 filename）、"
        "search（搜索，可选 type 和 query）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "delete", "search"],
                "description": "要执行的操作。add=新建记忆，update=更新已有记忆，delete=删除，search=搜索。"
            },
            "type": {
                "type": "string",
                "enum": ["character", "world", "plot", "style"],
                "description": "记忆类型。add 和 update 时需要。"
            },
            "name": {
                "type": "string",
                "description": "记忆名称。不要加类型前缀（系统会自动加）。add/update 时需要。示例：'沈渊'（角色）、'第1章已写'（情节）、'灵气寄生规则'（世界观）。"
            },
            "description": {
                "type": "string",
                "description": "一行描述，用于未来判断相关性。add 时需要，update 可选。"
            },
            "content": {
                "type": "string",
                "description": "记忆正文内容。add/update 时需要。"
            },
            "filename": {
                "type": "string",
                "description": "直接指定文件名。delete 时可用。"
            },
            "query": {
                "type": "string",
                "description": "搜索关键词，匹配名称和描述。search 时使用。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=memory_tool_handler,
    check_fn=lambda: _provider is not None,
    description="管理持久小说记忆（角色/世界观/情节/风格）",
    emoji="🧠",
)
