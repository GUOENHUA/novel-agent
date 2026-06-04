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
            outline_path = self.agent.project_dir / "outline" / "full.md"
            has_memory = list(self.agent.memory_dir.glob("*.md"))
            if not outline_path.exists() and len(has_memory) <= 1:
                safe_print(f"\n  [bold]你好！让我们从零创作《{self.agent.novel_title}》。[/bold]")
                safe_print(f"  建议顺序：大纲 → 角色 → 世界观 → 开始写作")
                safe_print(f"  输入 '帮我写大纲' 开始第一步。")
            elif not existing:
                safe_print(f"  [dim]大纲已就绪，输入 '写第一章' 开始。[/dim]")
        safe_print("")

        # Show previous session with interactive browser
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
                    # Truncate long text
                    display = text if len(text) <= 300 else text[:300] + "..."
                    if role == "user":
                        safe_print(f"  [bold cyan]>[/bold cyan] {display}")
                    else:
                        safe_print(f"  [dim]{display}[/dim]")
                    if len(text) > 300:
                        safe_print(f"  [dim]({len(text)} chars total — use 'view last' to expand)[/dim]")
                safe_print(f"  " + "─" * 50)
                safe_print("")
                safe_print(f"  [dim]Previous session: {len(self.agent.conversation_history)} messages. Type 'view last' to expand, or just continue.[/dim]")
                safe_print("")

        # REPL
        while True:
            if _abort_flag:
                _abort_flag = False
                safe_print("  [dim](interrupted)[/dim]")

            try:
                user_input = input(PROMPT).strip()
            except EOFError:
                self.agent.save_session()
                safe_print("\nGoodbye!")
                break
            except KeyboardInterrupt:
                safe_print("\n  [dim](interrupted)[/dim]")
                continue

            if not user_input:
                continue

            if user_input.lower() in ("view last", "view", "show last"):
                self._show_full_history()
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
        # Sanitize: ensure all assistant messages use ContentBlock array format
        for msg in messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                msg["content"] = [{"type": "text", "text": msg["content"]}]

        tools = registry.get_definitions()

        # Preview/confirmation tools — agent must use these before persisting any content
        preview_tools = [
            {
                "name": "preview_chapter",
                "description": "Preview a chapter draft. Call this AFTER you finish writing chapter content. The system will show it to the user for confirmation before saving. Chapter content is passed as a separate text block.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "chapter_number": {"type": "integer", "description": "Chapter number"},
                        "title": {"type": "string", "description": "Suggested title, 4-8 Chinese characters"},
                    },
                    "required": ["chapter_number"],
                },
            },
            {
                "name": "preview_outline",
                "description": "Preview outline content for user confirmation. Call this AFTER writing outline text.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "preview_setting",
                "description": "Preview a character/world setting before saving to memory.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "setting_type": {"type": "string", "enum": ["character", "world", "plot", "style"]},
                        "name": {"type": "string", "description": "Setting name"},
                        "description": {"type": "string", "description": "One-line description"},
                        "content": {"type": "string", "description": "Full setting content"},
                    },
                    "required": ["setting_type", "name", "description", "content"],
                },
            },
            {
                "name": "clarify",
                "description": "Ask the user a clarifying question with options. Always include 'Other...' as a choice for text input.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}, "description": "3-5 options including 'Other (let me explain)'"},
                    },
                    "required": ["question"],
                },
            },
        ]
        all_tools = (tools or []) + preview_tools

        turn_input_tokens = 0
        turn_output_tokens = 0

        safe_print("")

        try:
            # First call — streaming for real-time display
            safe_print("  [dim]Thinking...[/dim]")
            response = self.agent.stream_with_display(
                messages=messages,
                tools=all_tools,
                extra_body={"thinking": {"type": "enabled"}},
            )
            safe_print("")
            # Accumulate text from this turn for preview tools
            self._last_text_output = self.agent.extract_text(response.content) or ""

            if _abort_flag:
                safe_print("  [dim](interrupted)[/dim]")
                return

            if hasattr(response, "usage") and response.usage:
                turn_input_tokens += response.usage.input_tokens
                turn_output_tokens += response.usage.output_tokens

            # Tool call loop
            tool_rounds = 0
            while response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input if isinstance(block.input, dict) else {}

                        if tool_name in ("preview_chapter", "preview_outline", "preview_setting"):
                            result = self._handle_preview(tool_name, tool_input)
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                        elif tool_name == "clarify":
                            question = tool_input.get("question", "")
                            options = tool_input.get("options", [])
                            # Use interactive select if options provided, fallback to text input
                            if options:
                                choice = self._interactive_select(question, options)
                            else:
                                choice = input(f"\n  {question}\n  > ").strip()
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": choice})
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
                    response = self.agent.call_llm(messages=messages, tools=all_tools)
                    elapsed = time.time() - t0
                    if hasattr(response, "usage") and response.usage:
                        status.update(f"Thinking... ({elapsed:.1f}s, {response.usage.input_tokens}+{response.usage.output_tokens} tk)")

                if hasattr(response, "usage") and response.usage:
                    turn_input_tokens += response.usage.input_tokens
                    turn_output_tokens += response.usage.output_tokens
                # Accumulate text across all responses in this turn
                new_text = self.agent.extract_text(response.content) or ""
                self._last_text_output = (self._last_text_output + "\n" + new_text).strip()
                if new_text.strip():
                    safe_print(new_text)
                    safe_print("")

            # Token summary
            self.total_input_tokens += turn_input_tokens
            self.total_output_tokens += turn_output_tokens
            if turn_input_tokens:
                safe_print(f"  [dim]turn: {turn_input_tokens:,}+{turn_output_tokens:,} tk | total: {self.total_input_tokens:,}+{self.total_output_tokens:,} tk[/dim]\n")

            # Update history — only save user + assistant final text (not tool interactions)
            # Tool_use/tool_result pairs break on resume since tool_use_ids don't persist
            self.agent.conversation_history.append({"role": "user", "content": user_message})
            # Build a clean text-only assistant message
            final_text = self.agent.extract_text(response.content) or ""
            self.agent.conversation_history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": final_text}] if final_text else response.content,
            })

            # Compression check
            self._maybe_compress(turn_input_tokens)

            # Persist (always, even after partial failures)
            assistant_text = self.agent.extract_text(response.content)
            self.agent.sync_memories(user_message, assistant_text or "")

        except KeyboardInterrupt:
            safe_print("\n  [dim](interrupted)[/dim]\n")
        except Exception as e:
            logger.exception("Turn processing failed")
            safe_print(f"  [red][ERROR][/red] {e}\n")
        finally:
            # Always save session, even on error
            try:
                self.agent.save_session()
            except Exception:
                pass

    # Track last text output for preview_chapter (content is in streaming text)
    _last_text_output = ""

    def _interactive_select(self, question: str, options: list[str]) -> str:
        """Interactive select: ↑↓ arrows, Enter=confirm, Tab=inline note input."""
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.buffer import Buffer

        idx = [0]
        note = [""]
        editing_note = [False]
        note_buffer = Buffer(multiline=False)

        def get_text():
            lines = [question, ""]
            for i, opt in enumerate(options):
                lines.append(f"  {'> ' if i == idx[0] else '  '}{opt}")
            lines.append("")
            if note[0]:
                lines.append(f"  Note: {note[0]}")
            if editing_note[0]:
                lines.append(f"  > {note_buffer.text}_")
            hint = "Enter=confirm  Tab=add note  Esc=cancel"
            if not editing_note[0]:
                lines.append(hint)
            return "\n".join(lines)

        kb = KeyBindings()

        @kb.add("up")
        def _(event):
            if not editing_note[0]:
                idx[0] = (idx[0] - 1) % len(options)

        @kb.add("down")
        def _(event):
            if not editing_note[0]:
                idx[0] = (idx[0] + 1) % len(options)

        @kb.add("enter")
        def _(event):
            if editing_note[0]:
                note[0] = note_buffer.text
                editing_note[0] = False
            else:
                chosen = options[idx[0]]
                event.app.exit(result=(chosen, note[0]))

        @kb.add(Keys.Tab)
        def _(event):
            if not editing_note[0]:
                editing_note[0] = True
                note_buffer.text = note[0]
            else:
                note[0] = note_buffer.text
                editing_note[0] = False

        @kb.add("escape")
        def _(event):
            if editing_note[0]:
                editing_note[0] = False
                note_buffer.text = ""
            else:
                event.app.exit(result=("", ""))

        @kb.add("<any>", filter=Condition(lambda: editing_note[0]))
        def _(event):
            note_buffer.insert_text(event.data)

        @kb.add("backspace", filter=Condition(lambda: editing_note[0]))
        def _(event):
            note_buffer.delete_before_cursor(1)

        content = FormattedTextControl(text=get_text)
        app = Application(
            layout=Layout(HSplit([Window(content=content, always_hide_cursor=True)])),
            key_bindings=kb, full_screen=False, erase_when_done=True,
        )

        result = app.run()
        if isinstance(result, tuple) and len(result) == 2:
            return result
        if isinstance(result, str) and result:
            return (result, "")
        return ("", "")

    def _handle_preview(self, tool_name: str, args: dict) -> str:
        """Show preview/confirm dialog for chapter, outline, or setting content."""
        import json

        if tool_name == "preview_chapter":
            content = self._last_text_output
            title = args.get("title", "")
            ch_num = args.get("chapter_number", 0)
            label = f"Chapter {ch_num}"
        elif tool_name == "preview_outline":
            content = self._last_text_output or args.get("content", "")
            title = "大纲"
            label = "Outline"
        elif tool_name == "preview_setting":
            content = self._last_text_output or args.get("content", "")
            title = args.get("name", "")
            s_type = args.get("setting_type", "character")
            label = f"{s_type}: {title}"
        else:
            return json.dumps({"status": "unknown_tool"})

        if not content:
            return json.dumps({"status": "no_content", "error": "No content to preview"})

        safe_print(f"\n  {'─' * 50}")
        safe_print(f"  [bold]{label}[/bold]  {len(content)} chars")
        if title:
            safe_print(f"  Title: {title}")
        safe_print(f"  {'─' * 50}")
        # Show preview
        # Show preview; interactive select for save/discard
        if len(content) > 600:
            safe_print(f"  [dim]{content[:250]}...[/dim]")
            action, note = self._interactive_select(
                f"{label} — {len(content)} chars",
                ["Save", "View full chapter", "Discard"],
            )
            if "View" in action:
                safe_print(f"  {'─' * 50}")
                safe_print(content)
                safe_print(f"  {'─' * 50}")
                action, note = self._interactive_select(
                    f"{label} — {len(content)} chars",
                    ["Save", "Discard"],
                )
        else:
            safe_print(f"  [dim]{content}[/dim]")
            action, note = self._interactive_select(
                f"{label} — {len(content)} chars",
                ["Save", "Discard"],
            )
        if not action:
            return json.dumps({"status": "skipped"})  # User pressed Esc

        if "Discard" in action:
            reason = note or input("  Why discard? ").strip()
            return json.dumps({"status": "discarded", "reason": reason or "no reason given"})

        feedback = note if "Save" in action else ""

        # Save
        if tool_name == "preview_chapter":
            if not title:
                from novel_agent.auto_pipeline import AutoPipeline
                p = AutoPipeline(self.agent)
                title = p._generate_title(content, ch_num)
            path = self.agent.chapter_path(ch_num, title or "untitled")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# 第{ch_num}章: {title}\n\n{content}", encoding="utf-8")
            # Update state
            state = self.agent.truth_files.load_state()
            state.current_chapter = ch_num + 1
            self.agent.truth_files.save_state(state)
            safe_print(f"  [green]Saved: {title}[/green]")
            return json.dumps({"status": "saved", "path": str(path), "title": title})

        elif tool_name == "preview_outline":
            out_dir = self.agent.project_dir / "outline"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "full.md"
            path.write_text(content, encoding="utf-8")
            safe_print(f"  [green]Saved: outline/full.md[/green]")
            return json.dumps({"status": "saved", "path": str(path)})

        elif tool_name == "preview_setting":
            s_type = args.get("setting_type", "character")
            name = args.get("name", "")
            desc = args.get("description", "")
            # Use memory tool
            result = registry.dispatch(
                "memory",
                {"action": "add", "type": s_type, "name": name, "description": desc, "content": content},
                chapters_dir=str(self.agent.chapters_dir),
                project_dir=str(self.agent.project_dir),
            )
            safe_print(f"  [green]Saved: {name}[/green]")
            return result

        return json.dumps({"status": "saved"})

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
            self.agent.save_session()
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

    def _show_full_history(self) -> None:
        """Show the full last exchange from previous session."""
        last_pair = self.agent.conversation_history[-2:]
        if not last_pair:
            safe_print("  No history to show.")
            return
        safe_print(f"  " + "─" * 50)
        for msg in last_pair:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    b.get("text", "") if isinstance(b, dict) else
                    (b.text if hasattr(b, "text") else str(b)[:500])
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

    def _set_title(self, title: str) -> None:
        import json
        self.agent.novel_title = title
        config_path = self.agent.project_dir / "novel.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["title"] = title
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
