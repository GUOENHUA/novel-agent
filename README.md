# novel-agent

Dual-mode AI novel writing agent — conversational + autonomous.

## Architecture

```
Outer: ReAct (conversational REPL)
  └── Inner: Plan+Execute (auto pipeline / tool internals)

Subsystems:
  memory/     — typed file-based persistence (character/world/plot/style)
  context/    — context compression with novel-specific summary template
  tools/      — 12 registered tools (writing, evaluation, web, export)
  skills/     — 5 bundled writing methodology skills
  craft/      — CRAFT.md education + ANTI_SLOP.md detection
  state/      — JSON truth files + hook ledger
```

## Quick Start

```bash
# Install
pip install -e .

# Set API key
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# Create a project
novel-agent init ./my-novel --total-words 80000 --total-chapters 24

# Conversational mode
novel-agent write --project ./my-novel

# Auto-generate 5 chapters
novel-agent auto --project ./my-novel --count 5 --words 3000
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Create a new novel project |
| `write` | Enter conversational REPL mode (ReAct) |
| `auto` | Run automatic generation pipeline (Plan+Execute) |
| `resume` | Resume a previous session |
| `revise --chapter N` | Enter conversational mode focused on a chapter |
| `status` | Show project statistics |
| `doctor` | Check configuration and API connectivity |

## Tools

| Tool | Description |
|------|-------------|
| `memory` | Persistent memory CRUD (4 types: character/world/plot/style) |
| `track_hooks` | Foreshadowing ledger (upsert/mention/resolve/defer) |
| `write_chapter` | Two-phase chapter writing (creative high-temp + settlement low-temp) |
| `develop_character` | Character development (3 sliders, W/W/N/L chain) |
| `outline_plot` | Plot structure planning (3-act, beat sheet) |
| `check_consistency` | Dual-layer consistency check (mechanical + LLM) |
| `check_slop` | AI writing pattern detection (regex + LLM) |
| `search_lore` | Cross-source world-building search |
| `web_search` | Internet search for research and reference |
| `web_fetch` | Web page content extraction |
| `skills_list` / `skill_view` | Writing methodology skills |
| `export_chapter` | Export chapters/manuscript/stats |

## Inspired By

- **claude-code** — typed memory taxonomy, MEMORY.md index, skill progressive disclosure
- **hermes-agent** — MemoryProvider ABC, ContextCompressor, ToolRegistry
- **autonovel** — CRAFT.md education, ANTI-SLOP detection, layer propagation rules
- **inkos** — Hook-Ledger, JSON truth files, two-phase chapter writing

## License

MIT
