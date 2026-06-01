"""Character development tool.

Borrowed from autonovel CRAFT.md character frameworks:
- Three Sliders (proactivity, likability, competence)
- Wound/Want/Need/Lie causal chain
- Dialogue distinctiveness (8 dimensions)
"""

from __future__ import annotations

import json
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result


def character_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle character development requests."""
    action = args.get("action", "develop")

    if action == "develop":
        return _handle_develop(args)
    elif action == "analyze":
        return _handle_analyze(args)
    elif action == "list":
        return _handle_list(args, kwargs)
    else:
        return tool_error(f"Unknown action: {action}")


def _handle_develop(args: dict[str, Any]) -> str:
    """Generate a character development prompt."""
    name = args.get("name", "")
    if not name:
        return tool_error("name is required.")

    directive = f"""## 角色发展: {name}

请基于以下框架发展这个角色。填写所有维度，越具体越好。

### 三滑块模型 (0-10分)
- 主动性 (Proactivity): 角色推动情节还是被动反应？
- 讨喜度 (Likability): 读者共情程度？
- 能力 (Competence): 角色擅长什么？

### Wound/Want/Need/Lie 因果链
- 幽灵（过去的创伤事件）:
- 伤口（持续的情感伤害）:
- 谎言（错误的核心信念）:
- 想要（由谎言驱动的外部目标）:
- 需要（真正能治愈角色的内部真理）:

### 弧线类型
- 正面弧线（谎言→真理）/ 负面弧线（真理→谎言）/ 扁平弧线（改变世界）

### 对话区别度 (8 个维度)
1. 词汇水平 (教育程度反映):
2. 句长和结构 (简洁 vs 冗长):
3. 正式度 (敬语/语气词使用):
4. 口头禅和语言习惯:
5. 提问 vs 陈述比例:
6. 打断模式:
7. 比喻域 (从角色的职业/背景出发):
8. 直接 vs 迂回:

### 外貌与存在感
- 标志性外貌特征（具体、独特、可辨认）:
- 说话时的身体语言:
- 沉默时的存在感:
- 至少一个秘密 (读者不会立即知道):

### 关系
- 与主角的关系和张力:
- 与其他角色的关系网:
"""

    # If there's existing character info, load it
    memory_content = args.get("memory_content", "")
    if memory_content:
        directive += f"\n\n### 已有角色记忆\n{memory_content}"

    return tool_result(
        success=True,
        action="develop",
        name=name,
        directive=directive,
        hint="Use the agent's call_llm() to generate the character development. Save results with the memory tool (type=character).",
    )


def _handle_analyze(args: dict[str, Any]) -> str:
    """Analyze a character for consistency and depth."""
    name = args.get("name", "")
    chapter_text = args.get("chapter_text", "")
    memory_content = args.get("memory_content", "")

    directive = f"""## 角色分析: {name}

请分析该角色在以下文本中的表现：

### 一致性检查
- 行为是否符合已建立的性格和动机？
- 对话是否保持独特的说话方式？
- 情感反应是否合理？

### 深度评估
- 角色是否展示了多个维度（不仅仅是"好人"或"坏人"）？
- 是否有潜文本——角色想的和说的不同的时刻？
- 角色是否有真正的改变或揭示？

### 角色信息
{memory_content if memory_content else '无已有角色信息'}

### 待分析文本
{chapter_text[:5000] if chapter_text else '无文本提供'}
"""

    return tool_result(
        success=True,
        action="analyze",
        name=name,
        directive=directive,
    )


def _handle_list(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """List character memories."""
    # This will be populated by the agent from memory system
    return tool_result(
        success=True,
        message="Use the memory tool with action=search and type=character to list characters.",
    )


# -- Tool schema ---------------------------------------------------------------

CHARACTER_TOOL_SCHEMA = {
    "name": "develop_character",
    "description": (
        "发展或分析小说角色。基于三滑块模型（主动性/讨喜度/能力）、"
        "Wound/Want/Need/Lie 因果链、8维度对话区别度等框架。\n\n"
        "操作：develop（生成角色发展指令）、analyze（分析角色一致性和深度）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["develop", "analyze"],
                "description": "操作类型。develop=发展角色，analyze=分析角色。"
            },
            "name": {
                "type": "string",
                "description": "角色名称。develop 时需要。"
            },
            "memory_content": {
                "type": "string",
                "description": "已有的角色记忆内容（如有）。"
            },
            "chapter_text": {
                "type": "string",
                "description": "要分析的章节文本（analyze 时使用）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="develop_character",
    toolset="writing",
    schema=CHARACTER_TOOL_SCHEMA,
    handler=character_tool_handler,
    description="发展或分析小说角色（三滑块、W/W/N/L、对话区别度）",
    emoji="👤",
)
