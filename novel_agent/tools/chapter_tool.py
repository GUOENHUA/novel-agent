"""Chapter writing tool — two-phase (creative + settlement).

Borrowed from inkos Writer+Settler pattern and autonovel draft_chapter.py.

Phase 1 (Creative, temp ~0.8): Generate chapter prose with context
Phase 2 (Settlement, temp ~0.3): Extract facts, update state

The actual LLM calls are made by the agent (this tool provides the
prompt assembly and state extraction logic).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from novel_agent.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def chapter_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle chapter writing requests.

    This tool returns prompts and context for the agent to use in LLM calls.
    The agent is responsible for making the actual Anthropic API calls.
    """
    action = args.get("action", "write")
    chapter_num = args.get("chapter_number", 0)
    if not chapter_num:
        return tool_error("chapter_number is required.")

    if action == "write":
        return _handle_write(args, kwargs)
    elif action == "write_and_save":
        return _handle_write_and_save(args, kwargs)
    elif action == "save":
        return _handle_save(args, kwargs)
    elif action == "edit":
        return _handle_edit(args, kwargs)
    elif action == "settle":
        return _handle_settle(args, kwargs)
    elif action == "read":
        return _handle_read(args, kwargs)
    else:
        return tool_error(f"Unknown action: {action}")


def _handle_write(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build the writing context and prompt for a chapter.

    The agent will use this to make the LLM call.
    """
    chapter_num = args["chapter_number"]
    words_target = args.get("words_target", 3000)
    outline_entry = args.get("outline_entry", "")
    previous_chapter = args.get("previous_chapter_end", "")
    next_chapter_outline = args.get("next_chapter_outline", "")
    active_hooks = args.get("active_hooks", [])
    character_states = args.get("character_states", {})

    # Build writing directive
    directive = f"""## 章节写作指令: 第{chapter_num}章

目标字数: ~{words_target} 字
温度: 0.8 (创意阶段)

### 写作要求
1. 参考 CRAFT.md 中的所有工艺标准
2. 遵守 ANTI_SLOP.md 中的 AI 痕迹检测规则
3. 第一句话必须有吸引力——直接进入场景，避免环境描写开头
4. 本章结束时要有"yes-but"或"no-and"的推动——读者必须想继续看下一章
5. 情感强度要有变化——不要一条直线
6. 对话要有角色区分度——去掉对话标签也能分辨谁在说话
7. 具体胜过抽象——用感官细节而非抽象形容词"""

    if outline_entry:
        directive += f"\n\n### 本章大纲\n{outline_entry}"

    if previous_chapter:
        directive += f"\n\n### 前一章结尾 (用于连续性)\n{previous_chapter[-1000:]}"

    if next_chapter_outline:
        directive += f"\n\n### 下一章大纲 (用于铺垫)\n{next_chapter_outline}"

    if active_hooks:
        directive += "\n\n### 活跃伏笔 (需要在写作中考虑)\n"
        for h in active_hooks:
            directive += f"- [{h.get('id', '?')}] {h.get('description', '')}\n"

    if character_states:
        directive += "\n\n### 角色当前状态\n"
        for name, state in character_states.items():
            directive += f"- {name}: {state}\n"

    return tool_result(
        success=True,
        action="write",
        chapter=chapter_num,
        directive=directive,
        temperature=0.8,
        max_tokens=int(words_target * 3),  # Rough token estimate for Chinese
        hint="Use the agent's call_llm() with this directive to generate the chapter. Then call chapter_tool action=settle to extract state.",
    )


def _handle_settle(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Build the settlement prompt to extract facts from a completed chapter.

    Returns a directive for a low-temperature LLM call to extract:
    - New facts established
    - Hook changes (planted/mentioned/resolved)
    - Character state changes
    - Chapter summary
    """
    chapter_num = args["chapter_number"]
    chapter_content = args.get("chapter_content", "")

    if not chapter_content:
        return tool_error("chapter_content is required for settle.")

    directive = f"""## 状态沉淀指令: 第{chapter_num}章

温度: 0.3 (沉淀阶段，需要精确性)

请从以下章节正文中提取结构化信息，以 JSON 格式返回。只提取可以确证的信息，
不确定的不要编造。

返回格式:
```json
{{
  "new_facts": ["事实1", "事实2", ...],
  "character_changes": {{
    "角色名": {{"location": "新位置", "emotional_state": "情绪", "goal": "当前目标"}}
  }},
  "hooks_planted": [
    {{"id": "hook-xxx", "description": "...", "type": "direct|symbolic|dialogue|action|naming", "target_chapter": null}}
  ],
  "hooks_mentioned": ["hook-id-1", "hook-id-2"],
  "hooks_resolved": ["hook-id-3"],
  "chapter_summary": "2-3句摘要，包含关键事件",
  "key_events": ["事件1", "事件2"],
  "characters_appearing": ["角色1", "角色2"],
  "mood": "tense|hopeful|tragic|mysterious|romantic|dark|whimsical"
}}
```

### 章节正文
{chapter_content[:20000]}"""

    return tool_result(
        success=True,
        action="settle",
        chapter=chapter_num,
        directive=directive,
        temperature=0.3,
        max_tokens=2048,
        hint="Use the agent's call_llm() with this directive to extract state. Parse the JSON response and update truth files and hook ledger.",
    )


def _handle_save(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Save chapter content to disk."""
    chapter_num = args.get("chapter_number", 0)
    content = args.get("content", "")
    title = args.get("title", "")
    if not chapter_num or not content:
        return tool_error("chapter_number and content are required.")
    chapters_dir = kwargs.get("chapters_dir", ".")
    from pathlib import Path as P
    dir_path = P(chapters_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    # Generate slug from title
    slug = ""
    if title:
        slug = "_" + "".join(c for c in title.lower().replace(" ", "-") if c.isalnum() or c in "-_")[:40]
    path = dir_path / f"ch_{chapter_num:03d}{slug}.md"
    final = f"# 第{chapter_num}章: {title}\n\n{content}" if title else content
    path.write_text(final, encoding="utf-8")
    return tool_result(success=True, path=str(path), chars=len(content))


def _handle_write_and_save(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Save chapter content to disk and return it for display."""
    chapter_num = args.get("chapter_number", 0)
    content = args.get("content", "")
    title = args.get("title", "")
    if not chapter_num or not content:
        return tool_error("chapter_number and content are required.")

    from pathlib import Path as P
    chapters_dir = kwargs.get("chapters_dir", ".")
    dir_path = P(chapters_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    slug = ""
    if title:
        slug = "_" + "".join(c for c in title.lower().replace(" ", "-") if c.isalnum() or c in "-_")[:40]
    path = dir_path / f"ch_{chapter_num:03d}{slug}.md"
    final = f"# 第{chapter_num}章: {title}\n\n{content}" if title else content
    path.write_text(final, encoding="utf-8")
    return tool_result(success=True, path=str(path), chapter=chapter_num, title=title, chars=len(content),
                       display_content=content, hint="Content saved and displayed above.")


def _handle_edit(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Edit an existing chapter. Supports exact string replacement (like CC Edit)
    and instruction-based editing."""
    chapter_num = args.get("chapter_number", 0)
    old_str = args.get("old_string", "")
    new_str = args.get("new_string", "")
    chapters_dir = kwargs.get("chapters_dir", ".")
    from pathlib import Path as P
    dir_path = P(chapters_dir)
    existing = list(dir_path.glob(f"ch_{chapter_num:03d}_*.md"))
    if not existing:
        return tool_error(f"Chapter {chapter_num} not found.")
    path = existing[0]
    current = path.read_text(encoding="utf-8")

    # Exact replacement mode (CC-style)
    if old_str:
        count = current.count(old_str)
        if count == 0:
            return tool_error(f"old_string not found in chapter {chapter_num}.")
        if count > 1:
            return tool_error(f"old_string matches {count} times — must be unique. Add more context to make it match exactly once.")
        edited = current.replace(old_str, new_str)
        path.write_text(edited, encoding="utf-8")
        return tool_result(
            success=True, chapter=chapter_num, action="edit",
            replaced=True, word_count=len(edited),
        )

    # Instruction mode (fallback)
    instruction = args.get("instruction", "")
    if not instruction:
        return tool_error("edit requires old_string+new_string or instruction.")
    return tool_result(
        success=True, chapter=chapter_num, action="edit",
        current_content=current, instruction=instruction,
        hint="Rewrite based on instruction and call write_chapter(action=save, ...) with the edited version.",
    )


def _handle_read(args: dict[str, Any], kwargs: dict[str, Any]) -> str:
    """Read a chapter from disk. chapter_number=0 returns TOC."""
    chapter_num = args["chapter_number"]
    chapters_dir = kwargs.get("chapters_dir", ".")

    # TOC mode: return all chapter summaries
    if chapter_num == 0:
        from pathlib import Path as P
        import re
        chapters_path = P(chapters_dir)
        toc = []
        for path in sorted(chapters_path.glob("ch_*_*.md")):
            content = path.read_text(encoding="utf-8")
            # Extract title and first ~200 chars as summary
            first_line = content.split("\n")[0] if content else ""
            title = first_line.replace("# ", "").strip() if first_line.startswith("#") else path.stem
            body = content[content.find("\n\n")+2:] if "\n\n" in content else content
            preview = body[:200].replace("\n", " ")
            toc.append({
                "chapter": path.stem,
                "title": title,
                "words": len(content),
                "preview": preview + ("..." if len(body) > 200 else ""),
            })
        return tool_result(success=True, toc=toc, count=len(toc))

    # Find chapter file by glob (actual naming: ch_001_title.md)
    chapters_path = Path(chapters_dir)
    matches = list(chapters_path.glob(f"ch_{chapter_num:03d}_*.md"))
    if matches:
        path = matches[0]
        content = path.read_text(encoding="utf-8")
        return tool_result(
            success=True,
            chapter=chapter_num,
            content=content,
            word_count=len(content),
        )
    else:
        return tool_result(
            success=False,
            chapter=chapter_num,
            error=f"Chapter {chapter_num} not found",
        )


# -- Tool schema ---------------------------------------------------------------

CHAPTER_TOOL_SCHEMA = {
    "name": "write_chapter",
    "description": (
        "撰写、保存或修改小说章节。\n"
        "- write: 生成章节写作指令\n"
        "- save: 保存章节到磁盘（chapter_number, content, title）\n"
        "- edit: 精确修改章节——给定 old_string（必须唯一），替换为 new_string\n"
        "- settle: 提取结构化状态信息\n"
        "- read: 读取已有章节（chapter_number=0返回目录）"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "write_and_save", "save", "edit", "settle", "read"],
                "description": "操作类型。edit=精确替换（old_string→new_string）。"
            },
            "chapter_number": {
                "type": "integer",
                "description": "章节号。所有操作都需要。"
            },
            "words_target": {
                "type": "integer",
                "description": "目标字数（write 时使用，默认 3000）。"
            },
            "outline_entry": {
                "type": "string",
                "description": "本章的大纲条目（write 时使用）。"
            },
            "previous_chapter_end": {
                "type": "string",
                "description": "前一章结尾内容，用于连续性（write 时使用）。"
            },
            "next_chapter_outline": {
                "type": "string",
                "description": "下一章大纲，用于铺垫（write 时使用）。"
            },
            "active_hooks": {
                "type": "array",
                "items": {"type": "object"},
                "description": "活跃伏笔列表（write 时使用）。"
            },
            "character_states": {
                "type": "object",
                "description": "角色当前状态映射（write 时使用）。"
            },
            "title": {
                "type": "string",
                "description": "章节标题（save 时使用）。"
            },
            "content": {
                "type": "string",
                "description": "章节完整正文（save 时需要）。"
            },
            "chapter_content": {
                "type": "string",
                "description": "章节正文内容（settle 时需要）。"
            },
            "old_string": {
                "type": "string",
                "description": "要替换的原文本（edit 时使用，必须唯一匹配）。"
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新文本（edit 时使用）。"
            },
            "instruction": {
                "type": "string",
                "description": "修改指令（edit 时，不用 old_string 时使用）。"
            },
        },
        "required": ["action", "chapter_number"],
    },
}

registry.register(
    name="write_chapter",
    toolset="writing",
    schema=CHAPTER_TOOL_SCHEMA,
    handler=chapter_tool_handler,
    description="撰写/修改/读取小说章节（两阶段：创意+沉淀）",
    emoji="✍️",
)
