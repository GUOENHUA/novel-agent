# novel-agent

Dual-mode AI novel writing agent — conversational + autonomous.

```
novel-agent   《剑道独尊》   deepseek-v4-pro[1m]
1/800 chapters   3,194 words   latest: ch_01

chapter2 > write chapter 2
```

## Quick Start

```bash
# Install
pip install -e .

# Set API key (DeepSeek Anthropic-compatible endpoint)
echo 'ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic' > .env
echo 'ANTHROPIC_AUTH_TOKEN=sk-...' >> .env

# Create a project
novel-agent init ./my-novel --title "剑道独尊" --total-chapters 800 --chapter-words 3000

# Conversational mode
novel-agent write --project ./my-novel

# Auto-generate 5 chapters
novel-agent auto --project ./my-novel --count 5

# Resume a session
novel-agent resume ./my-novel
```

## Architecture

```
Outer: ReAct (conversational REPL)
  └── Inner: Plan+Execute (auto pipeline / tool internals)

Context: 3-layer loading
  Stable    → identity + CRAFT.md + ANTI_SLOP.md + memory system + skills (~15K tokens)
  Dynamic   → progress + previous chapter ending + outline + characters + hooks + recent chapters + style + user instruction (~2-3K tokens)
  On-demand → tool queries (full chapters, character details, world lore, TOC, web search)
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Create a new novel project |
| `write` | Enter conversational REPL mode (ReAct) |
| `auto --count N` | Auto-generate N chapters (Plan+Execute) |
| `auto --to-complete` | Generate until outline is complete |
| `resume` | Resume a previous session |
| `revise --chapter N` | Focus on revising a specific chapter |
| `status` | Show project statistics |
| `doctor` | Check configuration and API connectivity |

### In-REPL commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/auto N` | Auto-generate N chapters |
| `/status` | Show progress |
| `/cost` | Show token usage |
| `/title` | Show/change novel title |
| `/quit` | Exit |

## Tools

| Tool | Description |
|------|-------------|
| `memory` | Persistent memory CRUD (character/world/plot/style) |
| `track_hooks` | Foreshadowing ledger (upsert/mention/resolve/defer) |
| `write_chapter` | Two-phase chapter writing (creative + settlement) |
| `develop_character` | Character development (3 sliders, W/W/N/L chain) |
| `outline_plot` | Plot structure planning (3-act, beat sheets) |
| `check_consistency` | Consistency checking (mechanical + LLM) |
| `check_slop` | AI writing pattern detection (regex + LLM) |
| `search_lore` | Cross-source world-building search |
| `web_search` | Internet search for research |
| `web_fetch` | Web page content extraction |
| `skills_list` / `skill_view` | Writing methodology skills |
| `export_chapter` | Export chapters/manuscript/stats |

## Skills

Bundled writing methodology skills (view with `skill_view`):

| Skill | Description |
|-------|-------------|
| `character_dev` | 3-slider model, Wound/Want/Need/Lie chain, dialogue distinctiveness |
| `world_building` | 3 pillars, iceberg principle, Sanderson's Laws of Magic |
| `plot_structure` | 3-act structure, Save the Cat beat sheet, scene propulsion |
| `dialogue_writing` | Voice differentiation, subtext, action tags, rhythm |
| `scene_construction` | Purpose check, enter/exit timing, sensory anchoring |

## Configuration

All via `.env`:

```bash
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic   # API endpoint
ANTHROPIC_AUTH_TOKEN=sk-...                              # API key
NOVEL_AGENT_MODEL=deepseek-v4-pro[1m]                   # Model name
NOVEL_AGENT_CONTEXT_LENGTH=1000000                       # Context window size
NOVEL_AGENT_COMPRESS_THRESHOLD=0.70                      # Compression trigger ratio
```

## Inspired By

- **claude-code** — typed memory taxonomy, MEMORY.md index, skill progressive disclosure
- **hermes-agent** — MemoryProvider ABC, ContextCompressor, ToolRegistry
- **autonovel** — CRAFT.md education, ANTI-SLOP detection, layer propagation rules
- **inkos** — Hook-Ledger, JSON truth files, two-phase chapter writing

## License

Apache 2.0 — 允许商用、修改、分发，保留版权声明即可。
