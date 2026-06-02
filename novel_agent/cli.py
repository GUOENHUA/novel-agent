"""CLI entry point — Click-based command router.

Commands:
    init      Create a new novel project
    write     Enter conversational REPL mode
    auto      Run automatic generation pipeline
    resume    Resume a previous session
    revise    Enter conversational mode focused on a specific chapter
    status    Show project status
    doctor    Check configuration and API connectivity
"""

from __future__ import annotations

import logging
import sys
import os
from pathlib import Path

import click
import dotenv

# Fix Unicode issues on Windows GBK terminals
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from novel_agent.utils.constants import (
    DEFAULT_CHAPTER_WORDS,
    DEFAULT_TOTAL_CHAPTERS,
    DEFAULT_TOTAL_WORDS,
)

dotenv.load_dotenv()

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(version="0.1.0", prog_name="novel-agent")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, verbose: bool = False):
    """novel-agent — Dual-mode AI novel writing agent.

    Conversational mode: \n
        novel-agent write --project ./my-novel

    Auto mode: \n
        novel-agent auto --project ./my-novel --count 5
    """
    _setup_logging(verbose)
    ctx.ensure_object(dict)


@main.command()
@click.argument("project_dir", type=click.Path())
@click.option("--title", type=str, default=None,
              help="Novel title (default: directory name)")
@click.option("--total-chapters", type=int, default=DEFAULT_TOTAL_CHAPTERS,
              help=f"Target chapter count (default: {DEFAULT_TOTAL_CHAPTERS})")
@click.option("--chapter-words", type=int, default=DEFAULT_CHAPTER_WORDS,
              help=f"Target words per chapter (default: {DEFAULT_CHAPTER_WORDS})")
@click.option("--total-words", type=int, default=None,
              help="Override total word count (default: chapters * words-per-chapter)")
def init(project_dir: str, title: str | None, total_chapters: int, chapter_words: int, total_words: int | None):
    """Initialize a new novel writing project."""
    import json

    if total_words is None:
        total_words = total_chapters * chapter_words

    project_path = Path(project_dir).resolve()
    novel_title = title or project_path.name

    if project_path.exists() and list(project_path.glob("*")):
        click.echo(f"[ERROR] {project_path} 已存在且非空")
        sys.exit(1)

    # Create directories
    (project_path / "chapters").mkdir(parents=True, exist_ok=True)
    (project_path / "memory").mkdir(parents=True, exist_ok=True)
    (project_path / "state").mkdir(parents=True, exist_ok=True)

    # Create project config
    config = {
        "version": "0.1.0",
        "title": novel_title,
        "project_name": project_path.name,
        "total_words": total_words,
        "total_chapters": total_chapters,
        "chapter_words": chapter_words,
    }
    config_path = project_path / "novel.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    click.echo(f"[OK] 《{novel_title}》已创建: {project_path}")
    click.echo(f"   目标: {total_chapters} 章 × ~{chapter_words} 字 = {total_words:,} 字")
    click.echo(f"   novel-agent write --project {project_path}")


@main.command()
@click.option("--project", "project_dir", type=click.Path(exists=True), default=".",
              help="Project directory (default: current)")
def write(project_dir: str):
    """Enter conversational REPL mode (ReAct pattern)."""
    from novel_agent.agent import AIAgent
    from novel_agent.conversation_loop import ConversationLoop

    agent = AIAgent(project_dir=project_dir)
    loop = ConversationLoop(agent)
    loop.run()


@main.command()
@click.option("--project", "project_dir", type=click.Path(exists=True), default=".",
              help="Project directory (default: current)")
@click.option("--count", type=int, default=None,
              help="Number of chapters to generate (required unless --to-complete)")
@click.option("--words", type=int, default=None,
              help="Words per chapter (default: from project config)")
@click.option("--to-complete", is_flag=True,
              help="Generate until outline is complete")
def auto(project_dir: str, count: int, words: int | None, to_complete: bool):
    """Run automatic chapter generation (Plan+Execute pattern)."""
    from novel_agent.agent import AIAgent
    from novel_agent.auto_pipeline import AutoPipeline

    if count is None and not to_complete:
        click.echo("[ERROR] --count or --to-complete is required")
        return

    agent = AIAgent(project_dir=project_dir)
    pipeline = AutoPipeline(agent)

    ch_count = count or 0
    if to_complete:
        existing = len(list(agent.chapters_dir.glob("ch_*_*.md")))
        ch_count = agent.total_chapters - existing
        if ch_count <= 0:
            click.echo("[OK] All chapters complete!")
            return
        if ch_count > 10:
            click.echo(f"Will generate {ch_count} chapters — this will take a while and consume significant tokens.")
            if not click.confirm("Continue?"):
                return

    # Determine next chapter to write
    existing_chs = sorted(agent.chapters_dir.glob("ch_*_*.md"))
    next_ch = len(existing_chs) + 1 if existing_chs else 1

    pipeline.run(
        start_chapter=next_ch,
        count=ch_count,
        words_per_chapter=words,
    )


@main.command()
@click.argument("project_dir", type=click.Path(exists=True))
def resume(project_dir: str):
    """Resume a previous session."""
    from novel_agent.agent import AIAgent
    from novel_agent.conversation_loop import ConversationLoop

    agent = AIAgent(project_dir=project_dir)
    click.echo(f"Resume: 《{agent.novel_title}》")
    loop = ConversationLoop(agent)
    loop.run()


@main.command()
@click.option("--project", "project_dir", type=click.Path(exists=True), default=".",
              help="Project directory (default: current)")
@click.option("--chapter", type=int, required=True,
              help="Chapter number to focus on")
def revise(project_dir: str, chapter: int):
    """Enter conversational mode focused on revising a specific chapter."""
    from novel_agent.agent import AIAgent
    from novel_agent.conversation_loop import ConversationLoop

    agent = AIAgent(project_dir=project_dir)

    # Check chapter exists
    chapter_file = agent.chapter_path(chapter)
    if chapter_file.exists():
        click.echo(f"Focus: 第{chapter}章 ({chapter_file})")
        click.echo(f"   字数: {len(chapter_file.read_text(encoding='utf-8'))}")
    else:
        click.echo(f"[WARN] 第{chapter}章尚不存在，将作为新章节创建")

    click.echo(f"\n   输入修改指令, 如:")
    click.echo(f"   - '把第{chapter}章的战斗场景改得更紧张'")
    click.echo(f"   - '检查第{chapter}章的对话是否自然'")
    click.echo()

    loop = ConversationLoop(agent)
    loop.run()


@main.command()
@click.option("--project", "project_dir", type=click.Path(exists=True), default=".",
              help="Project directory (default: current)")
def status(project_dir: str):
    """Show project status and statistics."""
    project_path = Path(project_dir).resolve()
    chapters_path = project_path / "chapters"

    chapters = sorted(chapters_path.glob("ch_*_*.md")) if chapters_path.exists() else []
    total_written = sum(
        len(p.read_text(encoding="utf-8")) for p in chapters
    )

    click.echo(f"Status: {project_path.name}")
    click.echo(f"   章节: {len(chapters)} 章已完成")
    click.echo(f"   总字数: {total_written}")
    click.echo(f"   目标: {DEFAULT_TOTAL_CHAPTERS} 章 / {DEFAULT_TOTAL_WORDS} 字")

    if chapters:
        click.echo(f"   平均每章: {total_written // len(chapters)} 字")
        click.echo(f"   章节列表:")
        for ch in chapters:
            size = len(ch.read_text(encoding="utf-8"))
            click.echo(f"     {ch.stem}: {size} 字")


@main.command()
def doctor():
    """Check configuration and API connectivity."""
    import os
    import httpx

    click.echo("doctor: novel-agent")

    # Check API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        click.echo(f"[OK] ANTHROPIC_API_KEY: 已设置 ({api_key[:8]}...)")
    else:
        click.echo("[FAIL] ANTHROPIC_API_KEY: 未设置")
        click.echo("   请在 .env 文件中设置: ANTHROPIC_API_KEY=sk-ant-...")
        return

    # Check API connectivity
    click.echo("[...] 测试 API 连接...")
    try:
        resp = httpx.get(
            "https://api.anthropic.com",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
        if resp.status_code < 400:
            click.echo("[OK] Anthropic API: 连接正常")
        else:
            click.echo(f"[WARN] Anthropic API: HTTP {resp.status_code}")
    except Exception as e:
        click.echo(f"[FAIL] Anthropic API: 无法连接 ({e})")


if __name__ == "__main__":
    main()
