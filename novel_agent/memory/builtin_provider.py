"""Built-in file-based memory provider.

Borrowed from hermes-agent MemoryProvider + claude-code memdir/.

Uses MemoryStore for file persistence, MEMORY.md as index.
Provides the `memory` tool for CRUD operations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from novel_agent.memory.memory_provider import MemoryProvider
from novel_agent.memory.memory_store import MemoryStore
from novel_agent.memory.memory_types import (
    MEMORY_TYPES,
    FRONTMATTER_EXAMPLE,
    TYPES_SECTION,
    WHAT_NOT_TO_SAVE,
    WHEN_TO_ACCESS,
    TRUSTING_RECALL,
    HOW_TO_SAVE,
)

logger = logging.getLogger(__name__)


class BuiltinProvider(MemoryProvider):
    """Built-in file-based memory provider.

    Stores memories as individual .md files with YAML frontmatter,
    indexed by MEMORY.md. No external dependencies required.
    """

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True  # Always available — file-based, no deps

    def initialize(self, memory_dir: str, **kwargs) -> None:
        self.store = MemoryStore(memory_dir)
        logger.info("Builtin memory provider initialized: %s", memory_dir)

    def system_prompt_block(self) -> str:
        """Build memory system prompt with types, index, and instructions."""
        index_content = self.store.read_index()

        parts = [
            f"## 持久记忆系统",
            "",
            f"记忆存储目录: `{self.store.memory_dir}`",
            "",
            TYPES_SECTION,
            "",
            WHAT_NOT_TO_SAVE,
            "",
            HOW_TO_SAVE.format(frontmatter=FRONTMATTER_EXAMPLE),
            "",
            WHEN_TO_ACCESS,
            "",
            TRUSTING_RECALL,
        ]

        if index_content:
            parts.extend(["", f"## MEMORY.md (记忆索引)", "", index_content])
        else:
            parts.extend(["", f"## MEMORY.md", "", "当前索引为空。保存新记忆后将出现在这里。"])

        return "\n".join(parts)

    def prefetch(self, query: str) -> str:
        """Return current index content as base context.

        Smart recall via side-query is handled by recall.py, called
        from the agent level, not inside the provider.
        """
        index = self.store.read_index()
        return index if index else ""

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [MEMORY_TOOL_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        if tool_name == "memory":
            return self._handle_memory(args)
        raise NotImplementedError(f"BuiltinProvider does not handle {tool_name}")

    def shutdown(self) -> None:
        pass  # Nothing to flush — file writes are synchronous

    # -- Tool implementation ---------------------------------------------------

    def _handle_memory(self, args: dict[str, Any]) -> str:
        """Handle memory tool calls: add, update, delete, search."""
        action = args.get("action", "")
        memory_type = args.get("type", "character")

        if memory_type not in MEMORY_TYPES:
            return json.dumps({
                "success": False,
                "error": f"Invalid type '{memory_type}'. Valid: {', '.join(MEMORY_TYPES)}",
            }, ensure_ascii=False)

        if action == "add":
            return self._memory_add(memory_type, args)
        elif action == "update":
            return self._memory_update(memory_type, args)
        elif action == "delete":
            return self._memory_delete(args)
        elif action == "search":
            return self._memory_search(args)
        else:
            return json.dumps({
                "success": False,
                "error": f"Unknown action '{action}'. Use: add, update, delete, search",
            }, ensure_ascii=False)

    def _memory_add(self, memory_type: str, args: dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        content = args.get("content", "").strip()
        tier = args.get("tier", "").strip()

        if not name or not description or not content:
            return json.dumps({
                "success": False,
                "error": "name, description, and content are all required for add.",
            }, ensure_ascii=False)

        # Build filename from name
        filename = self._name_to_filename(name, memory_type)

        # Check for duplicates
        existing = self.store.read_memory(filename)
        if existing:
            return json.dumps({
                "success": False,
                "error": f"Memory '{name}' already exists at {filename}. Use update to modify.",
            }, ensure_ascii=False)

        # Build frontmatter — include tier for character types
        fm = {
            "name": name,
            "description": description,
            "type": memory_type,
        }
        if tier:
            fm["tier"] = tier

        # Write memory file
        self.store.write_memory(
            filename=filename,
            frontmatter=fm,
            content=content,
        )

        # For minor/cameo characters, skip MEMORY.md index —
        # they are "即用即丢" (use-and-discard) and should not
        # participate in memory search.
        if memory_type != "character" or tier not in ("minor", "cameo"):
            self.store.add_to_index(
                title=name,
                filename=filename,
                hook=description[:150],
            )

        return json.dumps({
            "success": True,
            "message": f"Memory '{name}' saved to {filename}.",
            "filename": filename,
            "type": memory_type,
            "tier": tier or None,
        }, ensure_ascii=False)

    def _memory_update(self, memory_type: str, args: dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        content = args.get("content", "").strip()

        if not name:
            return json.dumps({
                "success": False,
                "error": "name is required for update.",
            }, ensure_ascii=False)

        filename = args.get("filename") or self._name_to_filename(name, memory_type)
        existing = self.store.read_memory(filename)

        if not existing:
            # Try to find by name
            headers = self.store.scan_memory_headers()
            match = next((h for h in headers if h["name"] == name), None)
            if match:
                filename = match["filename"]
                existing = self.store.read_memory(filename)

        if not existing:
            return json.dumps({
                "success": False,
                "error": f"Memory '{name}' not found. Use add to create.",
            }, ensure_ascii=False)

        # Update content (preserve existing fields if not provided)
        fm = self.store._parse_simple_frontmatter(existing)
        if not description:
            description = fm.get("description", "")

        self.store.write_memory(
            filename=filename,
            frontmatter={
                "name": name,
                "description": description,
                "type": memory_type,
            },
            content=content,
        )

        self.store.add_to_index(title=name, filename=filename, hook=description[:150])

        return json.dumps({
            "success": True,
            "message": f"Memory '{name}' updated.",
            "filename": filename,
        }, ensure_ascii=False)

    def _memory_delete(self, args: dict[str, Any]) -> str:
        name = args.get("name", "").strip()
        filename = args.get("filename", "")

        if not name and not filename:
            return json.dumps({
                "success": False,
                "error": "name or filename is required for delete.",
            }, ensure_ascii=False)

        if filename:
            target = filename
        else:
            headers = self.store.scan_memory_headers()
            match = next((h for h in headers if h["name"] == name), None)
            if not match:
                return json.dumps({
                    "success": False,
                    "error": f"Memory '{name}' not found.",
                }, ensure_ascii=False)
            target = match["filename"]

        deleted = self.store.delete_memory(target)
        if deleted:
            self.store.remove_from_index(target)
            return json.dumps({
                "success": True,
                "message": f"Memory '{target}' deleted.",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "error": f"File '{target}' not found on disk.",
            }, ensure_ascii=False)

    def _memory_search(self, args: dict[str, Any]) -> str:
        query = args.get("query", "").lower().strip()
        memory_type = args.get("type", "")

        headers = self.store.scan_memory_headers()
        if memory_type:
            headers = [h for h in headers if h["type"] == memory_type]

        # Exclude minor/cameo characters from search — they are
        # "即用即丢" (use-and-discard). Only major/supporting characters
        # participate in memory search. Minor characters live in chapters,
        # not in the persistent memory index.
        if memory_type == "character":
            headers = [h for h in headers if h.get("tier", "major") not in ("minor", "cameo")]

        if query:
            headers = [
                h for h in headers
                if query in h["name"].lower()
                or query in h["description"].lower()
            ]

        return json.dumps({
            "success": True,
            "memories": headers,
            "count": len(headers),
        }, ensure_ascii=False)

    @staticmethod
    def _name_to_filename(name: str, memory_type: str) -> str:
        """Convert a memory name to a safe filename."""
        prefix = {"character": "char", "world": "world", "plot": "plot", "style": "style"}
        pfx = prefix.get(memory_type, "mem")
        # Strip prefix if LLM already included it in the name
        if name.startswith(f"{pfx}-"):
            name = name[len(pfx) + 1:]
        safe = name.lower().replace(" ", "-").replace("_", "-")
        # Remove non-alphanumeric chars (keep Chinese, hyphens)
        safe = "".join(c for c in safe if c.isalnum() or c in "-_一-鿿")
        return f"{pfx}-{safe}.md"


# -- Tool schema ---------------------------------------------------------------

MEMORY_TOOL_SCHEMA = {
    "name": "memory",
    "description": (
        "管理持久小说记忆，跨会话保持。四种类型：character（角色）、"
        "world（世界观）、plot（情节）、style（风格）。\n\n"
        "何时保存（主动做，不要等被要求）：\n"
        "- 引入新角色或揭示角色的重要信息时\n"
        "- 建立新的世界观设定时\n"
        "- 规划情节或调整走向时\n"
        "- 用户给出风格反馈或确认偏好时\n\n"
        "四个操作：add（新建）、update（更新已有）、delete（删除）、search（搜索）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "delete", "search"],
                "description": "要执行的操作。"
            },
            "type": {
                "type": "string",
                "enum": ["character", "world", "plot", "style"],
                "description": "记忆类型。add 和 update 时需要。"
            },
            "name": {
                "type": "string",
                "description": "记忆名称（如 '张三的角色档案'）。add/update 时需要。delete/search 可选。"
            },
            "description": {
                "type": "string",
                "description": "一行描述，用于未来的相关性判断。add 时需要。update 可选。"
            },
            "content": {
                "type": "string",
                "description": "记忆正文。add/update 时需要。"
            },
            "filename": {
                "type": "string",
                "description": "直接指定文件名（delete 或 update 时）。"
            },
            "query": {
                "type": "string",
                "description": "搜索关键词（search 时使用）。"
            },
        },
        "required": ["action"],
    },
}
