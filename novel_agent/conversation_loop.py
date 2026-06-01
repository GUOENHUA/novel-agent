"""Conversational REPL loop — ReAct pattern (outer loop).

The conversational mode uses ReAct: user input → agent thinks → tool calls →
observe results → think more → respond to user.

Borrows patterns from hermes-agent `conversation_loop.py`.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from novel_agent.agent import AIAgent
from novel_agent.tools.registry import registry

# Import tool modules to trigger registry registration
import novel_agent.tools.memory_tool  # noqa: F401
import novel_agent.tools.hook_tool  # noqa: F401
import novel_agent.tools.skill_tool  # noqa: F401

logger = logging.getLogger(__name__)

PROMPT = "novel-agent> "
console = Console(force_terminal=True, legacy_windows=False) if __import__('sys').platform == 'win32' else Console()


def safe_print(text: str) -> None:
    """Print text, replacing characters that can't be encoded on Windows GBK terminals."""
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
        safe_print(f"\n[bold]novel-agent[/bold] (project: {self.agent.project_dir.name})")
        safe_print(f"   model: {self.agent.model}")
        safe_print(f"   target: {self.agent.total_chapters} chapters, ~{self.agent.total_words:,} words")

        # Show current novel state
        self._show_novel_state()

        safe_print(f"   type /help for help, /quit to exit\n")

    def _show_novel_state(self) -> None:
        """Print a brief novel state summary on startup."""
        state = self.agent.truth_files.load_state()
        existing = list(self.agent.chapters_dir.glob("ch_*.md"))
        total_written = sum(len(p.read_text(encoding="utf-8")) for p in existing)

        if existing:
            chapters_list = sorted(p.stem for p in existing)
            last_ch = chapters_list[-1] if chapters_list else "?"
            safe_print(f"   [bold]progress:[/bold] {len(existing)}/{self.agent.total_chapters} chapters | {total_written:,} words | latest: {last_ch}")
        else:
            safe_print(f"   [bold]progress:[/bold] no chapters yet — ready to start")

        while True:
            try:
                user_input = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                safe_print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                if self._handle_command(user_input):
                    continue
                else:
                    break

            self._process_turn(user_input)

    def _process_turn(self, user_message: str) -> None:
        """Process one turn: build novel context → agent → tools → response → memory sync."""
        novel_context = self.agent.build_novel_context(user_message)

        augmented_message = (
            f"{novel_context}\n\n"
            f"---\n\n"
            f"Above is a snapshot of your novel's current state. "
            f"Process the user's instruction based on this context."
        )

        messages = self.agent.conversation_history + [
            {"role": "user", "content": augmented_message}
        ]

        tools = registry.get_definitions()

        safe_print("")  # spacing

        assistant_content = ""
        try:
            # --- LLM call with spinner ---
            turn_input_tokens = 0
            turn_output_tokens = 0

            with console.status("[bold yellow]Thinking...", spinner="dots") as status:
                t0 = time.time()
                response = self.agent.call_llm(
                    messages=messages,
                    tools=tools if tools else None,
                )
                elapsed = time.time() - t0
                in_tok = response.usage.input_tokens
                out_tok = response.usage.output_tokens
                status.update(f"[bold yellow]Thinking... ({elapsed:.1f}s, {in_tok}+{out_tok} tk)")

            turn_input_tokens += in_tok
            turn_output_tokens += out_tok
            self.total_input_tokens += in_tok
            self.total_output_tokens += out_tok

            # --- ReAct loop: handle tool calls ---
            tool_rounds = 0
            while response.stop_reason == "tool_use":
                tool_rounds += 1
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input if isinstance(block.input, dict) else {}
                        safe_print(
                            f"  [dim][{tool_name}][/dim] "
                            f"{json.dumps(tool_input, ensure_ascii=False)[:100]}"
                        )

                        result = registry.dispatch(tool_name, tool_input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                with console.status(f"[bold yellow]Thinking... (tool round {tool_rounds})", spinner="dots") as status:
                    t0 = time.time()
                    response = self.agent.call_llm(
                        messages=messages,
                        tools=tools if tools else None,
                    )
                    elapsed = time.time() - t0
                    in_tok = response.usage.input_tokens
                    out_tok = response.usage.output_tokens
                    status.update(
                        f"[bold yellow]Thinking... ({elapsed:.1f}s, {in_tok}+{out_tok} tk)"
                    )

                turn_input_tokens += in_tok
                turn_output_tokens += out_tok
                self.total_input_tokens += in_tok
                self.total_output_tokens += out_tok

            # --- Display response ---
            assistant_content = self.agent.extract_text(response.content)

            # Check if this turn involved chapter writing → ask for confirmation
            chapter_written = self._detect_chapter_write(response, messages)
            if chapter_written and assistant_content:
                content_to_save = self._confirm_chapter(assistant_content, chapter_written)
                if content_to_save is None:
                    # User chose "no" — discard, don't save to history
                    safe_print("  [yellow]Chapter discarded.[/yellow]\n")
                    return  # Skip history update, user retries next turn
                elif content_to_save != assistant_content:
                    # User edited — update and save
                    assistant_content = content_to_save
                    safe_print("\n  [green]Edited version saved.[/green]\n")

            if assistant_content:
                safe_print(assistant_content)
                safe_print("")

            # --- Token summary ---
            safe_print(
                f"  [dim]turn: {turn_input_tokens:,}+{turn_output_tokens:,} tk "
                f"| total: {self.total_input_tokens:,}+{self.total_output_tokens:,} tk[/dim]\n"
            )

            # Update history
            self.agent.conversation_history.append(
                {"role": "user", "content": user_message}
            )
            self.agent.conversation_history.append(
                {"role": "assistant", "content": response.content}
            )

            # Sync memories
            self.agent.sync_memories(user_message, assistant_content)

        except Exception as e:
            logger.exception("Turn processing failed")
            safe_print(f"  [red][ERROR][/red] {e}\n")

    def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns True to continue, False to quit."""
        parts = cmd.split()
        command = parts[0].lower()

        if command in ("/quit", "/exit"):
            safe_print("Goodbye!")
            return False
        elif command == "/help":
            safe_print("""
  Commands:
    /help         Show help
    /auto N       Auto-generate next N chapters
    /status       Show project status
    /cost         Show token usage
    /quit         Exit

  Natural language:
    Just tell me what to do, e.g.:
    - "Write chapter 3"
    - "Revise chapter 2's fight scene"
    - "Check my hooks"
    - "Auto-generate 5 more chapters"
            """)
        elif command == "/auto":
            try:
                count = int(parts[1]) if len(parts) > 1 else 1
            except ValueError:
                count = 1
            safe_print(f"  Switching to auto mode, generating {count} chapters...")
            from novel_agent.auto_pipeline import AutoPipeline
            pipeline = AutoPipeline(self.agent)
            pipeline.run(start_chapter=self._next_chapter(), count=count)
        elif command == "/status":
            chapters = list(self.agent.chapters_dir.glob("ch_*.md"))
            total_w = sum(len(p.read_text(encoding="utf-8")) for p in chapters)
            safe_print(f"  Project: {self.agent.project_dir.name}")
            safe_print(f"  Model: {self.agent.model}")
            safe_print(f"  Chapters: {len(chapters)}/{self.agent.total_chapters} | {total_w:,} words")
        elif command == "/cost":
            safe_print(
                f"  Input: {self.total_input_tokens:,} tokens | "
                f"Output: {self.total_output_tokens:,} tokens | "
                f"Total: {self.total_input_tokens + self.total_output_tokens:,} tokens"
            )
        else:
            safe_print(f"  Unknown command: {command}")

        return True

    def _detect_chapter_write(self, response, messages: list) -> int | None:
        """Check if this turn produced a chapter draft.

        Detection: long prose response (>500 chars) with narrative structure
        (paragraph breaks + Chinese punctuation), not a conversational reply.
        """
        text = self.agent.extract_text(response.content)
        if not text or len(text) < 500:
            return None

        # Count narrative markers: paragraphs, dialogue quotes, chapter endings
        has_paragraphs = text.count("\n\n") >= 2
        has_dialogue = "“" in text or '"' in text or "「" in text
        has_punctuation = text.count("。") >= 10
        narrative_score = has_paragraphs + has_dialogue + has_punctuation

        if narrative_score < 2:
            return None  # Not enough narrative markers — likely conversational

        # Detect chapter completion markers
        chapter_markers = ["（第", "（*第", "*第", "章完", "（未完", "（第一卷"]
        has_chapter_ending = any(m in text[-200:] for m in chapter_markers)

        if not has_chapter_ending and len(text) < 2000:
            return None  # Long prose but no chapter ending — might be a scene fragment

        state = self.agent.truth_files.load_state()
        existing = list(self.agent.chapters_dir.glob("ch_*.md"))
        return state.current_chapter or (len(existing) + 1)

    def _confirm_chapter(self, content: str, chapter_num: int) -> str | None:
        """Ask user to confirm/edit/retry chapter. Returns content to save, or None to discard."""
        safe_print(f"\n  [bold]Chapter {chapter_num} draft ready.[/bold]")
        safe_print(f"  [dim]{len(content)} chars[/dim]")
        safe_print(f"  [Y]es save  [E]dit  [N]o discard")

        choice = input("  > ").strip().lower()

        if choice in ("y", "yes", ""):
            # Save to chapters
            chapter_path = self.agent.chapters_dir / f"ch_{chapter_num:02d}.md"
            chapter_path.write_text(content, encoding="utf-8")
            safe_print(f"  [green]Saved to {chapter_path}[/green]")
            return content

        elif choice in ("e", "edit"):
            safe_print("  Enter edit instructions (or paste replacement text):")
            edit_input = input("  edit> ").strip()
            if edit_input:
                if len(edit_input) > 200:
                    # Assume it's replacement text
                    return edit_input
                else:
                    # Assume it's edit instructions → re-call LLM
                    edit_msg = (
                        f"Here is the current chapter {chapter_num} draft. "
                        f"Apply this edit instruction to it: {edit_input}\n\n"
                        f"CHAPTER:\n{content}\n\n"
                        f"Return the FULL edited chapter. Do not explain — just output the edited text."
                    )
                    safe_print("  [yellow]Applying edits...[/yellow]")
                    resp = self.agent.call_llm(
                        messages=[{"role": "user", "content": edit_msg}],
                        max_tokens=len(content) * 2,
                        temperature=0.5,
                    )
                    edited = self.agent.extract_text(resp.content)
                    safe_print(f"  [green]Edit applied ({len(edited)} chars)[/green]")
                    # Recurse to confirm the edit
                    return self._confirm_chapter(edited, chapter_num)
            return content  # Keep original if no input

        elif choice in ("n", "no"):
            return None

        else:
            safe_print("  [dim]Unknown choice, saving by default[/dim]")
            return content

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
