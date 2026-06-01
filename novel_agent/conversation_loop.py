"""Conversational REPL loop — ReAct pattern (outer loop).

The conversational mode uses ReAct: user input → agent thinks → tool calls →
observe results → think more → respond to user.

Borrows patterns from hermes-agent `conversation_loop.py`.
"""

from __future__ import annotations

import json
import logging
import signal
from typing import Any

from novel_agent.agent import AIAgent
from novel_agent.tools.registry import registry

logger = logging.getLogger(__name__)

PROMPT = "📖 novel-agent> "


class ConversationLoop:
    """ReAct-style REPL for conversational novel writing."""

    def __init__(self, agent: AIAgent):
        self.agent = agent

    def run(self) -> None:
        """Enter the interactive REPL loop."""
        print(f"\n📖 novel-agent (project: {self.agent.project_dir.name})")
        print(f"   模型: {self.agent.model}")
        print(f"   目标: {self.agent.total_chapters} 章, ~{self.agent.total_words} 字")
        print(f"   输入 /help 查看帮助, /quit 退出\n")

        while True:
            try:
                user_input = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见！")
                break

            if not user_input:
                continue

            # Handle slash commands
            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    continue
                else:
                    break  # /quit

            # Handle natural language → delegate to agent
            self._process_turn(user_input)

    def _process_turn(self, user_message: str) -> None:
        """Process one turn: user message → agent → tools → response."""
        messages = self.agent.conversation_history + [
            {"role": "user", "content": user_message}
        ]

        tools = registry.get_definitions()
        tool_names = registry.get_all_tool_names()

        print()  # spacing

        try:
            response = self.agent.call_llm(
                messages=messages,
                tools=tools if tools else None,
            )

            # ReAct loop: handle tool calls
            while response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input if isinstance(block.input, dict) else {}

                        print(f"  🔧 {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:120]})")

                        result = registry.dispatch(tool_name, tool_input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Append assistant + tool results to messages
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                response = self.agent.call_llm(
                    messages=messages,
                    tools=tools if tools else None,
                )

            # Display text response
            for block in response.content:
                if block.type == "text":
                    print(block.text)
                    print()

            # Update history
            self.agent.conversation_history.append(
                {"role": "user", "content": user_message}
            )
            self.agent.conversation_history.append(
                {"role": "assistant", "content": response.content}
            )

        except Exception as e:
            logger.exception("Turn processing failed")
            print(f"  ❌ 处理失败: {e}\n")

    def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns True to continue, False to quit."""
        parts = cmd.split()
        command = parts[0].lower()

        if command == "/quit" or command == "/exit":
            print("👋 再见！")
            return False
        elif command == "/help":
            print("""
  命令:
    /help         显示帮助
    /auto N       自动生成后续 N 章
    /status       查看项目状态
    /quit         退出

  自然语言:
    直接告诉我要做什么即可，例如:
    - "写第3章"
    - "修改第2章的战斗场景"
    - "帮我检查一下伏笔"
    - "自动生成后续5章"
            """)
        elif command == "/auto":
            try:
                count = int(parts[1]) if len(parts) > 1 else 1
            except ValueError:
                count = 1
            print(f"  🔄 切换到自动模式，生成 {count} 章...")
            from novel_agent.auto_pipeline import AutoPipeline
            pipeline = AutoPipeline(self.agent)
            pipeline.run(start_chapter=self._next_chapter(), count=count)
        elif command == "/status":
            chapters = list(self.agent.chapters_dir.glob("ch_*.md"))
            print(f"  项目: {self.agent.project_dir.name}")
            print(f"  模型: {self.agent.model}")
            print(f"  已写章节: {len(chapters)}/{self.agent.total_chapters}")
            print(f"  目标字数: {self.agent.total_words}")
        else:
            print(f"  未知命令: {command}")

        return True

    def _next_chapter(self) -> int:
        """Determine the next chapter number to write."""
        existing = list(self.agent.chapters_dir.glob("ch_*.md"))
        if not existing:
            return 1
        nums = []
        for p in existing:
            try:
                nums.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return max(nums) + 1 if nums else 1
