"""Skill tool — view and list available writing skills.

Borrowed from hermes-agent `tools/skills_tool.py`.

Progressive disclosure:
  - skills_list: name + description only (token efficient)
  - skill_view(name): full SKILL.md content + linked files
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from novel_agent.tools.registry import registry, tool_error, tool_result

# Will be set by agent after skill discovery
_loader: Optional[Any] = None


def set_skill_loader(loader: Any) -> None:
    """Set the SkillLoader instance."""
    global _loader
    _loader = loader


def _list_skills(args: dict[str, Any]) -> str:
    """List all available skills."""
    if _loader is None:
        return tool_error("Skill system not initialized.")

    skills = _loader.list_all()
    return tool_result(
        success=True,
        skills=[
            {
                "name": s.name,
                "description": s.description,
                "source": s.source,
            }
            for s in skills
        ],
        count=len(skills),
        hint="Use skill_view(name) to load full content.",
    )


def _view_skill(args: dict[str, Any]) -> str:
    """Load a skill's full content."""
    if _loader is None:
        return tool_error("Skill system not initialized.")

    name = args.get("name", "").strip()
    if not name:
        return tool_error("name is required.")

    skill = _loader.get(name)
    if not skill:
        available = [s.name for s in _loader.list_all()]
        return tool_result(
            success=False,
            error=f"Skill '{name}' not found.",
            available_skills=available[:20],
            hint="Use skills_list to see all available skills.",
        )

    content = skill.load_content()
    if not content:
        return tool_error(f"Failed to load skill '{name}'.")

    return tool_result(
        success=True,
        name=skill.name,
        description=skill.description,
        source=skill.source,
        content=content,
    )


def skill_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle skill tool calls."""
    action = args.get("action", "list")

    if action == "list":
        return _list_skills(args)
    elif action == "view":
        return _view_skill(args)
    else:
        return tool_error(f"Unknown action: {action}")


# -- Tool schemas --------------------------------------------------------------

SKILLS_LIST_SCHEMA = {
    "name": "skills_list",
    "description": "列出所有可用的写作 Skills（名称 + 描述）。使用 skill_view 加载完整内容。",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SKILL_VIEW_SCHEMA = {
    "name": "skill_view",
    "description": (
        "加载写作 Skill 的完整内容。Skills 包含特定写作任务的方法论、"
        "模板和工作流程。先调用 skills_list 查看可用 skills，"
        "再调用 skill_view 加载需要的。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill 名称（使用 skills_list 查看可用的）。",
            },
        },
        "required": ["name"],
    },
}

registry.register(
    name="skills_list",
    toolset="skills",
    schema=SKILLS_LIST_SCHEMA,
    handler=lambda args, **kw: skill_tool_handler({"action": "list"}),
    description="列出所有可用的写作 Skills",
    emoji="📚",
)

registry.register(
    name="skill_view",
    toolset="skills",
    schema=SKILL_VIEW_SCHEMA,
    handler=lambda args, **kw: skill_tool_handler({"action": "view", **args}),
    description="加载写作 Skill 完整内容",
    emoji="📖",
)
