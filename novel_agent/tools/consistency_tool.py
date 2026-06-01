"""Consistency checker — dual-layer (mechanical + LLM).

Borrowed from autonovel `evaluate.py`.

Checks:
  - Character consistency (name, traits, status)
  - World/lore consistency (rules, geography, history)
  - Timeline consistency (event ordering)
  - Plot thread consistency (hooks planted vs resolved)
"""

from __future__ import annotations

import json
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result


def consistency_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle consistency checking requests."""
    action = args.get("action", "check")

    if action == "check":
        return _handle_check(args, kwargs)
    elif action == "cross_chapter":
        return _handle_cross_chapter(args, kwargs)
    else:
        return tool_error(f"Unknown action: {action}")


def _handle_check(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build a consistency check directive for a chapter."""
    chapter_num = args.get("chapter_number", 0)
    chapter_text = args.get("chapter_text", "")

    directive = f"""## 一致性审查: 第{chapter_num}章

请检查以下维度：

### 角色一致性
- 角色的行为是否符合其已建立的性格和动机？
- 对话风格是否保持不变？
- 角色的状态（位置、情绪、能力）是否连续？
- 已死亡的角色是否意外出现？

### 世界观一致性
- 设定的规则是否被遵守？（魔法/科技/社会规则）
- 地点描述是否与前文一致？
- 文化和习俗是否被正确使用？

### 时间线一致性
- 事件的时间顺序是否合理？
- 如果前一章是晚上，这一章是早上——中间发生了什么？
- 角色之间的年龄和时间关系是否一致？

### 情节一致性
- 是否有伏笔被提及但未在账本中记录？
- 是否有已回收的伏笔被当作未回收使用？

对于每个不一致之处，提供：
- 具体位置（段落/行）
- 冲突内容（应该是什么 vs 现在是什么）
- 建议修复方案

### 章节文本
{chapter_text[:15000]}
"""

    return tool_result(
        success=True,
        action="check",
        chapter=chapter_num,
        directive=directive,
        hint="Use the agent's call_llm() to perform the consistency check. The LLM will identify inconsistencies.",
    )


def _handle_cross_chapter(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build a cross-chapter consistency check directive."""
    chapter_range = args.get("chapter_range", "")

    directive = f"""## 跨章节一致性审查: {chapter_range}

请检查指定章节范围内：

### 跨章节模式
- 是否有重复的描写、比喻、措辞？
- 情感节奏是否合理？（不能一直紧张或一直平淡）
- 角色的成长轨迹是否连贯？
- 伏笔的种植和回收是否合理分布？

### 全局一致性
- 总字数分布是否合理？（每章长度差距不宜过大）
- 角色出场频率是否合理？（重要角色不能长时间消失）
- 关键信息是否在恰当时机揭示？（不能太早或太晚）

请生成审查报告。
"""

    return tool_result(
        success=True,
        action="cross_chapter",
        chapter_range=chapter_range,
        directive=directive,
    )


# -- Tool schema ---------------------------------------------------------------

CONSISTENCY_TOOL_SCHEMA = {
    "name": "check_consistency",
    "description": (
        "检查小说一致性。两层机制：\n"
        "- check: 单章审查（角色/世界观/时间线/情节一致性）\n"
        "- cross_chapter: 跨章审查（模式检测、节奏、全局一致性）\n\n"
        "返回审查指令供 LLM 执行深度分析。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check", "cross_chapter"],
                "description": "操作类型。check=单章审查，cross_chapter=跨章审查。"
            },
            "chapter_number": {
                "type": "integer",
                "description": "要检查的章节号（check 时使用）。"
            },
            "chapter_text": {
                "type": "string",
                "description": "章节文本内容（check 时使用）。"
            },
            "chapter_range": {
                "type": "string",
                "description": "章节范围，如 '1-5'（cross_chapter 时使用）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="check_consistency",
    toolset="evaluation",
    schema=CONSISTENCY_TOOL_SCHEMA,
    handler=consistency_tool_handler,
    description="检查小说一致性（角色/世界观/时间线/情节，单章或跨章）",
    emoji="✅",
)
