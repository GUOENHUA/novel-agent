"""Plot outline tool — structure planning with beat sheets.

Borrowed from autonovel CRAFT.md plot frameworks:
- Save the Cat beat sheet
- Dan Harmon Story Circle
- Sanderson Promise/Progress/Payoff
"""

from __future__ import annotations

from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result


def plot_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle plot/structure planning requests."""
    action = args.get("action", "plan")

    if action == "plan":
        return _handle_plan(args, kwargs)
    elif action == "save":
        return _handle_save(args, kwargs)
    elif action == "edit":
        return _handle_edit(args, kwargs)
    elif action == "check_beats":
        return _handle_check_beats(args)
    else:
        return tool_error(f"Unknown action: {action}")


def _handle_plan(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Generate a plot structure planning prompt."""
    total_chapters = args.get("total_chapters", 0)
    # Read from project config if not provided
    if not total_chapters:
        try:
            import json, os
            project_dir = kwargs.get("project_dir", "") or args.get("project_dir", "") or os.getenv("NOVEL_PROJECT_DIR", "")
            if project_dir:
                config_path = __import__('pathlib').Path(project_dir) / "novel.json"
                if config_path.exists():
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    total_chapters = config.get("total_chapters", 24)
        except Exception:
            pass
    total_chapters = total_chapters or 24
    genre = args.get("genre", "")
    premise = args.get("premise", "")

    # For epics (>100 chapters), use volume-based planning
    if total_chapters > 100:
        vols = max(5, (total_chapters + 79) // 80)  # ~80 chapters per volume, ceil division
        ch_per_vol = total_chapters // vols
        directive = f"""## 超长篇情节结构规划

全书 {total_chapters} 章，分为 {vols} 卷，每卷约 {ch_per_vol} 章。
{f"类型: {genre}" if genre else ""}
{f"前提: {premise}" if premise else ""}

### 卷级规划（先规划每卷的核心内容）
为每卷写 3-5 句描述：
- 本卷的故事目标和主要冲突
- 主角在本卷的成长节点（Wound/Want/Need 推进到哪一步）
- 本卷引入和回收的伏笔
- 本卷的高潮事件

### 三幕结构分配
第一幕 (0-25%): 第1-{max(1, total_chapters // 4)}章, 对应第1-{max(1, vols // 4)}卷
第二幕 (25-75%): 第{max(1, total_chapters // 4)}-{max(1, total_chapters * 3 // 4)}章, 对应第{max(1, vols // 4)}-{max(1, vols * 3 // 4)}卷
第三幕 (75-100%): 第{max(1, total_chapters * 3 // 4)}-{total_chapters}章, 对应第{max(1, vols * 3 // 4)}-{vols}卷

### 生成格式
先输出卷级规划，再对第一卷输出每章的 1-2 句大纲。
后续卷的详细大纲可以在写到时再展开。

请生成全书 {vols} 卷的卷级规划 + 第一卷 {ch_per_vol} 章的章节大纲。"""
    else:
        directive = f"""## 情节结构规划

目标章节数: {total_chapters}
{f"类型: {genre}" if genre else ""}
{f"前提: {premise}" if premise else ""}

### 三幕结构
第一幕 (0-25%): 第1-{max(1, total_chapters // 4)}章
第二幕 (25-75%): 第{max(1, total_chapters // 4)}-{max(1, total_chapters * 3 // 4)}章
第三幕 (75-100%): 第{max(1, total_chapters * 3 // 4)}-{total_chapters}章

### 要点
1. 每个场景用"yes-but"或"no-and"结束
2. 至少 3 条伏笔线，每条在 ~3 个章节中被提及
3. 副线在第{total_chapters // 4}章左右引入
4. 高潮前加速——每章至少 2 个转折
5. 结局展示转变后的世界——呼应开头

请生成第1-{total_chapters}章的大纲，每章 2-3 句。"""

    return tool_result(
        success=True,
        action="plan",
        total_chapters=total_chapters,
        directive=directive,
        hint="Use the agent's call_llm() to generate the outline. Save it to the project as outline.md.",
    )


def _handle_edit(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Load existing outline for editing."""
    vol = args.get("volume", 0)
    project_dir = kwargs.get("project_dir", ".")
    out_dir = __import__('pathlib').Path(project_dir) / "outline"
    path = out_dir / f"vol_{vol:02d}.md" if vol else out_dir / "full.md"
    if not path.exists():
        return tool_error(f"Outline not found: {path}")
    current = path.read_text(encoding="utf-8")
    return tool_result(success=True, volume=vol or "full", current_content=current,
                       hint="Rewrite this outline based on instructions, then use outline_plot save.")


def _handle_save(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Save outline content to outline/ directory."""
    content = args.get("content", "")
    vol = args.get("volume", 0)  # 0 = full outline, 1-N = volume
    if not content:
        return tool_error("content is required for save.")
    project_dir = kwargs.get("project_dir", ".")
    out_dir = __import__('pathlib').Path(project_dir) / "outline"
    out_dir.mkdir(parents=True, exist_ok=True)
    if vol:
        out_path = out_dir / f"vol_{vol:02d}.md"
    else:
        out_path = out_dir / "full.md"
    out_path.write_text(content, encoding="utf-8")
    return tool_result(success=True, path=str(out_path), chars=len(content), volume=vol or "full")


def _handle_check_beats(args: dict[str, Any]) -> str:
    """Check if chapter beats align with story structure."""
    chapter_num = args.get("chapter_number", 0)
    total_chapters = args.get("total_chapters", 24)

    # Calculate expected beat position
    position_pct = chapter_num / total_chapters * 100

    beat_info = ""
    if position_pct <= 25:
        beat_info = "第一幕（建置）。应建立正常世界、引入角色、暗示主题。"
        if 10 <= position_pct <= 12:
            beat_info += " 应接近引发事件（Catalyst）。"
    elif position_pct <= 75:
        beat_info = "第二幕（对抗）。角色在新世界中挣扎。"
        if 48 <= position_pct <= 52:
            beat_info += " 应接近中点（虚假胜利或失败）。"
        elif 65 <= position_pct <= 70:
            beat_info += " 应接近一切尽失时刻。"
    else:
        beat_info = "第三幕（解决）。高潮和结局。"
        if 75 <= position_pct <= 80:
            beat_info += " 应出现突破（新领悟/新信息）。"

    return tool_result(
        success=True,
        action="check_beats",
        chapter=chapter_num,
        position_pct=round(position_pct, 1),
        beat_info=beat_info,
    )


# -- Tool schema ---------------------------------------------------------------

PLOT_TOOL_SCHEMA = {
    "name": "outline_plot",
    "description": (
        "规划小说情节结构。基于三幕结构、Save the Cat 节拍表等框架。\n\n"
        "操作：plan（生成情节大纲）、check_beats（检查某章节的节拍位置）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["plan", "check_beats"],
                "description": "操作类型。plan=规划大纲，check_beats=检查节拍位置。"
            },
            "total_chapters": {
                "type": "integer",
                "description": "目标章节总数（plan 时使用）。"
            },
            "genre": {
                "type": "string",
                "description": "小说类型（plan 时使用）。"
            },
            "premise": {
                "type": "string",
                "description": "故事前提/核心概念（plan 时使用）。"
            },
            "chapter_number": {
                "type": "integer",
                "description": "要检查的章节号（check_beats 时使用）。"
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="outline_plot",
    toolset="writing",
    schema=PLOT_TOOL_SCHEMA,
    handler=plot_tool_handler,
    description="规划小说情节结构（三幕/节拍表/故事圈）",
    emoji="📋",
)
