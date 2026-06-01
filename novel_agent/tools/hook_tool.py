"""Hook tracking tool — foreshadowing ledger CRUD.

Borrowed from inkos Hook-Ledger system.

Operations: upsert, mention, resolve, defer, list, report.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from novel_agent.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_ledger: Optional[Any] = None  # HookLedger instance


def set_hook_ledger(ledger: Any) -> None:
    """Set the HookLedger instance for tool dispatch."""
    global _ledger
    _ledger = ledger


def hook_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle hook tool calls."""
    if _ledger is None:
        return tool_error("Hook ledger is not initialized.")

    action = args.get("action", "list")

    try:
        if action == "upsert":
            return _handle_upsert(args)
        elif action == "mention":
            return _handle_mention(args)
        elif action == "resolve":
            return _handle_resolve(args)
        elif action == "defer":
            return _handle_defer(args)
        elif action == "list":
            return _handle_list(args)
        elif action == "report":
            return _handle_report(args)
        else:
            return tool_error(f"Unknown action: {action}")
    except Exception as e:
        logger.exception("Hook tool error")
        return tool_error(str(e))


def _handle_upsert(args: dict[str, Any]) -> str:
    hook = _ledger.upsert(
        hook_id=args["hook_id"],
        description=args["description"],
        planted_chapter=args.get("planted_chapter", 0),
        hook_type=args.get("hook_type", "direct"),
        target_chapter=args.get("target_chapter"),
        related_characters=args.get("related_characters"),
        related_hooks=args.get("related_hooks"),
    )
    return tool_result(success=True, hook=hook.model_dump())


def _handle_mention(args: dict[str, Any]) -> str:
    hook = _ledger.mention(args["hook_id"], args.get("chapter_num", 0))
    if hook:
        return tool_result(success=True, message=f"Hook '{args['hook_id']}' mentioned.")
    return tool_error(f"Hook '{args['hook_id']}' not found.")


def _handle_resolve(args: dict[str, Any]) -> str:
    hook = _ledger.resolve(args["hook_id"], args.get("chapter_num", 0))
    if hook:
        return tool_result(success=True, message=f"Hook '{args['hook_id']}' resolved.")
    return tool_error(f"Hook '{args['hook_id']}' not found.")


def _handle_defer(args: dict[str, Any]) -> str:
    hook = _ledger.defer(args["hook_id"], args["new_target_chapter"])
    if hook:
        return tool_result(success=True, message=f"Hook '{args['hook_id']}' deferred.")
    return tool_error(f"Hook '{args['hook_id']}' not found.")


def _handle_list(args: dict[str, Any]) -> str:
    mode = args.get("mode", "active")
    if mode == "active":
        hooks = _ledger.get_active()
    elif mode == "resolved":
        hooks = _ledger.get_resolved()
    elif mode == "all":
        hooks = _ledger.load_all()
    elif mode == "overdue":
        hooks = _ledger.get_overdue(args.get("current_chapter", 0))
    else:
        hooks = _ledger.get_active()

    return tool_result(
        success=True,
        hooks=[h.model_dump() for h in hooks],
        count=len(hooks),
        mode=mode,
    )


def _handle_report(args: dict[str, Any]) -> str:
    current_ch = args.get("current_chapter", 0)
    report = _ledger.build_report(current_ch)
    return tool_result(success=True, report=report)


# -- Tool schema ---------------------------------------------------------------

HOOK_TOOL_SCHEMA = {
    "name": "track_hooks",
    "description": (
        "管理小说伏笔和线索（Hook Ledger）。追踪情节中的伏笔种植、提及、回收和推迟。\n\n"
        "操作说明：\n"
        "- upsert: 种植新伏笔或更新已有伏笔。需要 hook_id、description。\n"
        "- mention: 记录某个伏笔在章节中被提及。\n"
        "- resolve: 标记某个伏笔已回收。\n"
        "- defer: 将伏笔的预期回收推迟到更后的章节。\n"
        "- list: 列出伏笔（可按 active/resolved/all/overdue 筛选）。\n"
        "- report: 生成伏笔状态报告。\n\n"
        "伏笔类型：direct（直接）、symbolic（象征）、dialogue（对话）、action（行动）、naming（命名）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upsert", "mention", "resolve", "defer", "list", "report"],
                "description": "要执行的操作。"
            },
            "hook_id": {
                "type": "string",
                "description": "伏笔唯一标识（如 'hook-003'）。upsert/mention/resolve/defer 时需要。"
            },
            "description": {
                "type": "string",
                "description": "伏笔描述。upsert 时需要。"
            },
            "planted_chapter": {
                "type": "integer",
                "description": "种植该伏笔的章节号。"
            },
            "hook_type": {
                "type": "string",
                "enum": ["direct", "symbolic", "dialogue", "action", "naming"],
                "description": "伏笔类型。默认 direct。"
            },
            "target_chapter": {
                "type": "integer",
                "description": "预期回收章节。"
            },
            "chapter_num": {
                "type": "integer",
                "description": "mention/resolve 时的当前章节号。"
            },
            "new_target_chapter": {
                "type": "integer",
                "description": "defer 时的新的目标章节。"
            },
            "current_chapter": {
                "type": "integer",
                "description": "report/list 时的当前章节，用于 overdue 检测。"
            },
            "mode": {
                "type": "string",
                "enum": ["active", "resolved", "all", "overdue"],
                "description": "list 的筛选模式。默认 active。"
            },
            "related_characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关联角色名称列表。"
            },
            "related_hooks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关联伏笔 ID 列表。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="track_hooks",
    toolset="writing",
    schema=HOOK_TOOL_SCHEMA,
    handler=hook_tool_handler,
    check_fn=lambda: _ledger is not None,
    description="管理小说伏笔（种植/提及/回收/推迟/查询）",
    emoji="🔮",
)
