"""Conversational REPL loop — ReAct pattern (outer loop).

The conversational mode uses ReAct: user input → agent thinks → tool calls →
observe results → think more → respond to user.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

from rich.console import Console

from novel_agent.agent import AIAgent
from novel_agent.auto_pipeline import AutoPipeline
from novel_agent.tools.registry import registry

# Import tool modules to trigger registry registration
import novel_agent.tools.memory_tool  # noqa: F401
import novel_agent.tools.hook_tool  # noqa: F401
import novel_agent.tools.skill_tool  # noqa: F401

logger = logging.getLogger(__name__)

PROMPT = "novel-agent > "
console = Console(force_terminal=True, legacy_windows=False) if __import__('sys').platform == 'win32' else Console()

# Global abort flag for Ctrl+C
_abort_flag = False


def _on_sigint(signum, frame):
    global _abort_flag
    _abort_flag = True
    print("\n  Interrupted — returning to prompt...")


def safe_print(text: str) -> None:
    """Print text, falling back to ascii on encoding errors."""
    try:
        console.print(text)
    except UnicodeEncodeError:
        safe = text.encode('ascii', errors='replace').decode('ascii')
        print(safe)


class ConversationLoop:
    """ReAct-style REPL for conversational novel writing."""

    def __init__(self, agent: AIAgent):
        self.agent = agent
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def run(self) -> None:
        """Enter the interactive REPL loop."""
        global _abort_flag
        _abort_flag = False
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

        try:
            self._run_loop()
        finally:
            signal.signal(signal.SIGINT, original_handler)

    def _run_loop(self) -> None:
        """Internal REPL loop."""
        global _abort_flag
        existing = list(self.agent.chapters_dir.glob("ch_*_*.md"))
        total_written = sum(len(p.read_text(encoding="utf-8")) for p in existing)

        # Header
        safe_print(f"\n  [bold]novel-agent[/bold]   {self.agent.novel_title}   {self.agent.model}")
        if existing:
            last_ch = sorted(p.stem for p in existing)[-1]
            safe_print(f"  {len(existing)}/{self.agent.total_chapters} chapters   {total_written:,} words   latest: [bold]{last_ch}[/bold]")
            next_ch = len(existing) + 1
            safe_print(f"  [dim]write chapter {next_ch} or ask me anything[/dim]")
        else:
            safe_print("  no chapters yet")
            outline_path = self.agent.project_dir / "outline.md"
            has_memory = list(self.agent.memory_dir.glob("*.md"))
            if not outline_path.exists() and len(has_memory) <= 1:
                safe_print(f"\n  [bold]你好！让我们从零创作《{self.agent.novel_title}》。[/bold]")
                safe_print(f"  建议顺序：大纲 → 角色 → 世界观 → 开始写作")
                safe_print(f"  输入 '帮我写大纲' 开始第一步。")
            elif not existing:
                safe_print(f"  [dim]大纲已就绪，输入 '写第一章' 开始。[/dim]")
        safe_print("")

        # Show previous session if resuming
        if self.agent.conversation_history:
            last_pair = self.agent.conversation_history[-2:]
            if last_pair:
                safe_print(f"  " + "─" * 50)
                for msg in last_pair:
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text = " ".join(
                            b.get("text", "") if isinstance(b, dict) else
                            (b.text if hasattr(b, "text") else str(b)[:200])
                            for b in content
                        )
                    else:
                        text = str(content)
                    if role == "user":
                        safe_print(f"  [bold cyan]>[/bold cyan] {text}")
                    else:
                        safe_print(f"  [dim]{text}[/dim]")
                safe_print(f"  " + "─" * 50)
                safe_print("")

        # REPL
        while True:
            if _abort_flag:
                _abort_flag = False
                safe_print("  [dim](interrupted)[/dim]")

            try:
                user_input = input(PROMPT).strip()
            except EOFError:
                safe_print("\nGoodbye!")
                break
            except KeyboardInterrupt:
                safe_print("\n  [dim](interrupted)[/dim]")
                continue

            if not user_input:
                continue

            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    continue
                else:
                    break

            self._process_turn(user_input)

    def _process_turn(self, user_message: str) -> None:
        """Process one turn: build context → agent → tools → response → persist."""
        global _abort_flag
        _abort_flag = False

        # Build novel context
        novel_context = self.agent.build_novel_context(user_message)
        augmented_message = (
            f"{novel_context}\n\n---\n\n"
            f"基于以上状态处理用户指令。章节正文会由系统自动保存，无需手动调工具。\n"
            f"大纲/角色/世界观内容请使用对应工具：outline_plot save, memory add, track_hooks。"
        )

        messages = self.agent.conversation_history + [
            {"role": "user", "content": augmented_message}
        ]

        tools = registry.get_definitions()
        # Add display_message tool so model can show text to user
        display_tool = {
            "name": "display_message",
            "description": "Display a message to the user. Use this for ALL text output — conversation, explanations, questions, chapter content. Never output raw text — always use this tool.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The text to display to the user"},
                    "message_type": {"type": "string", "enum": ["conversation", "chapter", "outline", "code", "planning"], "description": "Type of content", "default": "conversation"},
                },
                "required": ["message"],
            },
        }
        all_tools = (tools or []) + [display_tool]

        turn_input_tokens = 0
        turn_output_tokens = 0

        safe_print("")

        try:
            safe_print("  [dim]Thinking...[/dim]")
            # Force tool use: model must always call a tool, never output raw text
            response = self.agent.call_llm(
                messages=messages,
                tools=all_tools,
                tool_choice={"type": "any"},
            )
            # Display display_message content
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_use" and block.name == "display_message":
                    if isinstance(block.input, dict):
                        safe_print(block.input.get("message", ""))
                        safe_print("")
            safe_print("")

            if _abort_flag:
                safe_print("  [dim](interrupted)[/dim]")
                return

            if hasattr(response, "usage") and response.usage:
                turn_input_tokens += response.usage.input_tokens
                turn_output_tokens += response.usage.output_tokens

            # Tool call loop — all output goes through tools
            tool_rounds = 0
            while response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input if isinstance(block.input, dict) else {}

                        if tool_name == "display_message":
                            msg = tool_input.get("message", "")
                            msg_type = tool_input.get("message_type", "conversation")
                            safe_print(msg)
                            # Auto-save if it's a chapter
                            if msg_type == "chapter" and len(msg) > 500:
                                existing = list(self.agent.chapters_dir.glob("ch_*_*.md"))
                                ch_num = len(existing) + 1
                                try:
                                    from novel_agent.auto_pipeline import AutoPipeline
                                    pipeline = AutoPipeline(self.agent)
                                    title = pipeline._generate_title(msg, ch_num)
                                    path = self.agent.chapter_path(ch_num, title)
                                    path.parent.mkdir(parents=True, exist_ok=True)
                                    path.write_text(f"# 第{ch_num}章: {title}\n\n{msg}", encoding="utf-8")
                                    safe_print(f"  [dim]saved: {title}[/dim]")
                                except Exception:
                                    pass
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": "displayed"})
                        else:
                            safe_print(f"  [dim][{tool_name}][/dim] {json.dumps(tool_input, ensure_ascii=False)[:100]}")
                            result = registry.dispatch(
                                tool_name, tool_input,
                                chapters_dir=str(self.agent.chapters_dir),
                                project_dir=str(self.agent.project_dir),
                            )
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                tool_rounds += 1
                with console.status(f"Thinking... (tool round {tool_rounds})", spinner="dots") as status:
                    t0 = time.time()
                    response = self.agent.call_llm(messages=messages, tools=all_tools, tool_choice={"type": "any"})
                    elapsed = time.time() - t0
                    if hasattr(response, "usage") and response.usage:
                        status.update(f"Thinking... ({elapsed:.1f}s, {response.usage.input_tokens}+{response.usage.output_tokens} tk)")

                if hasattr(response, "usage") and response.usage:
                    turn_input_tokens += response.usage.input_tokens
                    turn_output_tokens += response.usage.output_tokens

            # Token summary
            self.total_input_tokens += turn_input_tokens
            self.total_output_tokens += turn_output_tokens
            if turn_input_tokens:
                safe_print(f"  [dim]turn: {turn_input_tokens:,}+{turn_output_tokens:,} tk | total: {self.total_input_tokens:,}+{self.total_output_tokens:,} tk[/dim]\n")

            # Update history
            self.agent.conversation_history.append({"role": "user", "content": user_message})
            self.agent.conversation_history.append({"role": "assistant", "content": response.content})

            # Compression check
            self._maybe_compress(turn_input_tokens)

            # Persist
            self.agent.save_session()
            assistant_text = self.agent.extract_text(response.content)
            self.agent.sync_memories(user_message, assistant_text or "")

        except KeyboardInterrupt:
            safe_print("\n  [dim](interrupted)[/dim]\n")
        except Exception as e:
            logger.exception("Turn processing failed")
            safe_print(f"  [red][ERROR][/red] {e}\n")

    def _maybe_compress(self, last_turn_tokens: int) -> None:
        """Compress old conversation turns when history grows too large."""
        from novel_agent.context.token_counter import estimate_messages_tokens

        history = self.agent.conversation_history
        if len(history) < 12:
            return

        est = estimate_messages_tokens(history)
        threshold = self.agent.context_engine.threshold_tokens or 700000
        if est < threshold:
            return

        keep_count = 0
        tail_tokens = 0
        for i in range(len(history) - 1, -1, -1):
            content = history[i].get("content", "")
            text = str(content) if isinstance(content, str) else " ".join(
                getattr(b, "text", "") if hasattr(b, "text") else str(b) for b in content
            ) if isinstance(content, list) else ""
            tail_tokens += len(text) // 2 + 20
            keep_count += 1
            if tail_tokens > 100000 or keep_count >= 20:
                break

        if keep_count >= len(history):
            return

        old_turns = history[:-keep_count]
        if len(old_turns) < 6:
            return

        summary = self._summarize_turns(old_turns)
        if not summary:
            return

        compressed = [
            {"role": "user", "content": f"[CONTEXT SUMMARY] Earlier turns compacted:\n\n{summary}"},
        ]
        self.agent.conversation_history = compressed + history[-keep_count:]
        self.agent.clear_prompt_cache()
        saved = est - estimate_messages_tokens(compressed) if compressed else 0
        safe_print(f"  [dim]Compressed: {len(history)} → {len(self.agent.conversation_history)} messages (~{saved} tokens saved)[/dim]")

    def _summarize_turns(self, turns: list[dict]) -> str:
        """Summarize old conversation turns."""
        try:
            serialized = []
            for msg in turns:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                text = str(content) if isinstance(content, str) else " ".join(
                    getattr(b, "text", "") if hasattr(b, "text") else str(b)[:200]
                    for b in content
                ) if isinstance(content, list) else ""
                serialized.append(f"[{role}]: {text[:500]}")
            body = "\n\n".join(serialized[-20:])

            resp = self.agent.call_llm(
                messages=[{"role": "user", "content": (
                    "Summarize these novel-writing conversation turns in Chinese. "
                    "Focus on: key decisions, chapters written, character developments, "
                    "plot directions. Keep under 500 chars.\n\n" + body
                )}],
                max_tokens=400, temperature=0.3,
            )
            return self.agent.extract_text(resp.content).strip()
        except Exception:
            return ""

    def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands."""
        parts = cmd.split()
        command = parts[0].lower()

        if command in ("/quit", "/exit"):
            safe_print("Goodbye!")
            return False
        elif command == "/help":
            safe_print("""
  /help         Show help
  /auto N       Auto-generate N chapters
  /status       Show progress
  /cost         Show token usage
  /title         Show/change title
  /clear        Reset session
  /quit         Exit
            """)
        elif command.startswith("/title"):
            new_title = cmd[7:].strip() if len(cmd) > 7 else ""
            if new_title:
                self._set_title(new_title)
                safe_print(f"  Title: 《{new_title}》")
            else:
                safe_print(f"  Current: 《{self.agent.novel_title}》")
        elif command == "/auto":
            try:
                count = int(parts[1]) if len(parts) > 1 else 1
            except ValueError:
                count = 1
            safe_print(f"  Auto mode: generating {count} chapters...")
            pipeline = AutoPipeline(self.agent)
            pipeline.run(start_chapter=self._next_chapter(), count=count)
        elif command == "/status":
            chapters = list(self.agent.chapters_dir.glob("ch_*_*.md"))
            total_w = sum(len(p.read_text(encoding="utf-8")) for p in chapters)
            safe_print(f"  {self.agent.novel_title}")
            safe_print(f"  Chapters: {len(chapters)}/{self.agent.total_chapters} | {total_w:,} words")
        elif command == "/cost":
            safe_print(f"  Input: {self.total_input_tokens:,} | Output: {self.total_output_tokens:,} | Total: {self.total_input_tokens + self.total_output_tokens:,}")
        elif command in ("/clear", "/new"):
            self.agent.conversation_history = []
            self.agent.save_session()
            safe_print("  Session cleared.")
        else:
            safe_print(f"  Unknown: {command}")
        return True

    def _next_chapter(self) -> int:
        existing = list(self.agent.chapters_dir.glob("ch_*_*.md"))
        if not existing:
            return 1
        nums = []
        for p in existing:
            try:
                nums.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                pass
        return max(nums) + 1 if nums else 1

    def _set_title(self, title: str) -> None:
        import json
        self.agent.novel_title = title
        config_path = self.agent.project_dir / "novel.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["title"] = title
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
