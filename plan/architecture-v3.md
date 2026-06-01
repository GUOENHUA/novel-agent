# Novel-Agent 架构设计计划 v3（双模式交互）

> v1: claude-code + hermes-agent 分析后初版
> v2: 加入 autonovel + inkos 对比后修订
> **v3: 新增双模式交互（对话式 + 自动式）**

## Context

novel-agent 是一个基于 LLM 的**双模式**小说写作 agent。通过深度分析四个项目的源码，精炼各家长处：

| 项目 | 语言 | 本质 | 核心优势 |
|------|------|------|----------|
| claude-code | TypeScript/Bun | 通用软件工程 agent | 类型化记忆分类法、Skill 渐进式披露、MEMORY.md 索引 |
| hermes-agent | Python | 通用软件工程 agent | MemoryProvider 可插拔架构、ContextCompressor 五阶段压缩、ToolRegistry |
| autonovel | Python | 全自动小说流水线 | CRAFT.md 工艺教育、ANTI-SLOP 检测、五层小说栈、多层评估、keep/discard 循环 |
| inkos | TypeScript/Node | 工业化小说生产系统 | 10-agent 分工、Hook-Ledger 伏笔账本、JSON truth files、33 维审计、TUI/Studio |

**定位：** 取 claude-code/hermes 的 agent 架构 + autonovel 的写作工艺 + inkos 的数据管理 = 一个**双模式（对话 + 自动切换）、可记忆、可持续创作**的小说写作 agent。

## 双模式交互设计（★ v3 核心新增）

novel-agent 不是 autonovel 那样的纯自动流水线，也不是 claude-code 那样的纯对话 agent——**两者兼有，同一个 agent 实例，随时切换**。

### 模式一：对话式写作（Conversational Mode）

像 claude-code 一样，通过 REPL 交互。用户给出指令，agent 执行并展示结果。

```
$ novel-agent write --project ./my-novel

📖 novel-agent> 写第3章，张三和李四第一次见面，制造紧张感

  [Agent 加载上下文，两阶段写章，展示进度...]

✅ 第3章 已完成（3247字）| slop ✅ | 伏笔 +2（李四身份疑点、玉佩线索）

📖 novel-agent> 李四的对话太生硬了，改自然一点

  [Agent 定位第3章对话段落，重写...]

✅ 已修改 3 处对话段落

📖 novel-agent> 帮我检查一下第1-3章的伏笔有没有断的

  [Agent 扫描 hook_ledger...]

✅ 伏笔账本报告：已种 8 个 | 已回收 2 个 | 待回收 6 个（预计在第5/7/10/12/15/18章）
   ⚠️  第2章的"老乞丐预言"伏笔尚未关联到回收计划，建议确认
```

### 模式二：自动生成（Auto Mode）

借鉴 autonovel 的自动化循环 + inkos 的两阶段写章，用户可设置生成规模。

```
$ novel-agent auto --project ./my-novel --count 5 --words 3000

  [自动模式] 连续生成 5 章，每章 ~3000 字

  第4章 ████████████ ✅ 3156字 | slop ✅ | 伏笔 +2
  第5章 ████████████ ✅ 2987字 | slop ✅ | 伏笔 +1, 回收 +1
  第6章 ████████████ ✅ 3102字 | slop ⚠️  | 伏笔 +1
  第7章 ████████████ ✅ 3041字 | slop ✅ | 伏笔 +2
  第8章 ████████████ ✅ 3218字 | slop ✅ | 伏笔 +1, 回收 +1

  ✅ 5 章全部完成，总计 15504 字
  ⚠️  第6章有 3 个 Tier 2 警告：[comprehensive, seamless, resonate]，建议复查
```

自动模式的核心 loop：

```
for chapter in range(start, start + count):
    1. write_chapter(chapter)          # 两阶段写章
    2. slop_checker.mechanical()       # 机械扫描
    3. if slop_score < threshold:
         retry (max 3 times)
    4. settle_state(chapter)           # 沉淀状态
    5. if consistency_score < threshold:
         retry
    6. commit chapter, advance hook_ledger
    7. check interrupted flag → 优雅退出
```

### 模式切换（核心能力）

```
                ┌──────────────┐
    novel-agent │              │  "自动生成后  "继续自动"
    write ─────▶│  对话模式    │◀─ 续5章"      │
                │  (REPL)      │──────────────▶│
                └──────┬───────┘               │
                       │                       │
                  Ctrl+C 中断                   │
                       │                       │
                       ▼                       ▼
                ┌──────────────────────────────┐
                │         自动模式              │
                │  write → settle → evaluate   │
                │       → keep / retry         │
                │                              │
                │  每章检查 interrupted 标志    │
                └──────────────────────────────┘
```

关键原则：
- **同一个 `AIAgent` 实例**，两种入口方法：`run_conversation()` 和 `run_auto_pipeline()`
- 对话模式中可以说"帮我自动生成后续 5 章"直接触发自动模式
- 自动模式中 Ctrl+C → 优雅退出（完成当前章节后停下）→ 回到对话模式
- 状态完全共享（memory、hook_ledger、truth_files）——无缝衔接
- 中断信号只设置 `agent.interrupted = True`，当前章不会被打断

### 生成规模控制

| 参数 | 用途 | 持久化位置 |
|------|------|-----------|
| `total_words` | 整部小说目标总字数（如 80000） | `novel.json` |
| `total_chapters` | 目标章节数（如 24） | `novel.json` |
| `chapter_words` | 每章目标字数（如 3000） | `novel.json` |

| CLI 命令 | 用途 |
|----------|------|
| `novel-agent auto --count 5` | 自动生成后续 5 章 |
| `novel-agent auto --to-complete` | 自动生成直到大纲完成 |
| `novel-agent auto --count 3 --words 5000` | 生成 3 章，每章 ~5000 字 |
| `novel-agent revise --chapter 3` | 进入对话模式，聚焦修改第 3 章 |
| `novel-agent revise --chapters 2-5` | 聚焦修改第 2-5 章 |

## 技术选型

| 项目 | 选择 |
|------|------|
| 语言 | Python 3.12+ |
| LLM | Anthropic API（Claude Opus/Sonnet） |
| CLI 框架 | Click |
| Schema 验证 | Pydantic v2 |
| 异步 | asyncio + httpx |
| 包管理 | uv / pyproject.toml |

## 架构总览（多源融合）

### 关键设计借鉴矩阵

| 机制 | 主要借鉴 | 辅助借鉴 | 核心思路 |
|------|----------|----------|----------|
| **双模式交互** | **autonovel 自动化 + claude-code REPL** | hermes Agent Loop | 对话/自动共享一个 agent，随时切换 |
| 记忆系统 | claude-code `memdir/` | hermes `MemoryProvider` | 四类型小说记忆分类 + 可插拔 provider |
| 上下文管理 | hermes `ContextCompressor` | — | 五阶段压缩 + 小说适配模板 |
| Skills | claude-code `skills/` | hermes `skills_tool.py` | 渐进式披露，写作方法论具象化 |
| Tools | hermes `registry.py` | — | 中央注册表 + 自注册模式 |
| Agent Loop | hermes `conversation_loop.py` | — | turn-based 对话循环 |
| 写作工艺 | autonovel `CRAFT.md` | — | 系统性写作教育，始终加载 |
| AI 检测 | autonovel `ANTI-SLOP.md` | — | 机械正则 + LLM 双层检测 |
| 伏笔管理 | inkos Hook-Ledger | — | upsert/mention/resolve/defer 生命周期 |
| 状态存储 | inkos JSON truth files | hermes MemoryStore | JSON 结构化 + markdown 可读双存储 |
| 写章流程 | inkos 两阶段 | autonovel 评估循环 | 创意阶段(高温) + 沉淀阶段(低温) |

## 项目结构（v3 更新）

```
novel-agent/
├── novel_agent/
│   ├── __init__.py
│   ├── cli.py                    # CLI 入口 (Click)
│   ├── agent.py                  # 主 agent 类
│   ├── conversation_loop.py      # 对话模式 REPL（借鉴 hermes）
│   ├── auto_pipeline.py          # 自动模式循环（★ v3 新增）
│   │
│   ├── memory/                   # 记忆子系统
│   │   ├── memory_provider.py
│   │   ├── memory_manager.py
│   │   ├── memory_store.py
│   │   ├── memory_types.py
│   │   ├── builtin_provider.py
│   │   └── recall.py
│   │
│   ├── context/                  # 上下文管理子系统
│   │   ├── context_engine.py
│   │   ├── compressor.py
│   │   ├── prompt_builder.py
│   │   └── token_counter.py
│   │
│   ├── tools/                    # 工具子系统
│   │   ├── registry.py
│   │   ├── memory_tool.py
│   │   ├── skill_tool.py
│   │   ├── chapter_tool.py       # 两阶段写章
│   │   ├── character_tool.py
│   │   ├── plot_tool.py
│   │   ├── hook_tool.py
│   │   ├── consistency_tool.py
│   │   ├── lore_tool.py
│   │   ├── web_search_tool.py    # 联网搜索（★ v3 新增）
│   │   ├── web_fetch_tool.py     # 网页内容提取（★ v3 新增）
│   │   └── slop_checker.py
│   │
│   ├── skills/                   # Skills 子系统
│   │   ├── loader.py
│   │   └── bundled/
│   │
│   ├── craft/                    # 写作工艺
│   │   ├── CRAFT.md
│   │   └── ANTI_SLOP.md
│   │
│   ├── state/                    # 小说状态管理
│   │   ├── truth_files.py
│   │   ├── hook_ledger.py
│   │   └── schemas.py
│   │
│   └── utils/
│
├── templates/                    # 小说模板
│   ├── character_template.md
│   ├── world_template.md
│   ├── outline_template.md
│   └── seed_prompt.md
│
├── plan/
│   ├── architecture-v1.md
│   ├── architecture-v2.md
│   ├── architecture-v3.md
│   └── framework-analysis.md
│
├── pyproject.toml
└── README.md
```

## CLI 命令总览

```bash
# ── 项目管理 ──
novel-agent init ./my-novel --total-words 80000 --total-chapters 24 --chapter-words 3000
novel-agent resume ./my-novel    # 恢复上次会话

# ── 对话模式 ──
novel-agent write --project ./my-novel          # 进入 REPL 对话
novel-agent revise --chapter 3 --project ./my-novel  # 进入对话，聚焦修改某章

# ── 自动模式 ──
novel-agent auto --count 5 --project ./my-novel             # 自动生成后续 5 章
novel-agent auto --to-complete --project ./my-novel         # 自动生成到完稿
novel-agent auto --count 3 --words 5000 --project ./my-novel # 指定字数和章数

# ── 记忆管理 ──
novel-agent memory list --type character
novel-agent memory add --type character --name "张三"
novel-agent memory search "李四"

# ── Skills ──
novel-agent skills list
novel-agent skills view character_dev

# ── 状态与诊断 ──
novel-agent status              # 查看项目进度、字数统计
novel-agent doctor              # 检查配置和 API 连通性
```

## 关键模块详解

### craft/ — 写作工艺教育（★ 借鉴 autonovel）

- `CRAFT.md`（~17KB）：情节结构、角色工艺、世界观、伏笔规则、Show Don't Tell、散文工艺、稳定性陷阱
- `ANTI_SLOP.md`：AI 痕迹检测参考 — Tier 1-3 禁词、小说特有 AI 标志、结构模式
- **加载策略**：始终在系统提示中（~8K tokens），作为 agent 的"必读教材"

### state/ — 小说状态管理（★ 借鉴 inkos）

- `truth_files.py`：current_state / pending_hooks / chapter_summaries / character_matrix
- `hook_ledger.py`：伏笔操作（upsert/mention/resolve/defer）+ 状态机验证
- `schemas.py`：Pydantic 模型，支持 JSON 序列化 + markdown 投射

### auto_pipeline.py — 自动模式引擎（★ v3 新增）

```
class AutoPipeline:
    """自动生成引擎，封装 autonovel 风格的 keep/discard 循环"""

    def run(self, agent, start_chapter: int, count: int, words_per_chapter: int):
        for ch in range(start_chapter, start_chapter + count):
            if agent.interrupted:
                break
            for attempt in range(3):
                chapter = agent.write_chapter(ch, words_per_chapter)
                slop_result = agent.check_slop_mechanical(chapter)
                if slop_result.score >= threshold:
                    agent.settle_state(chapter)
                    agent.commit_chapter(ch)
                    break
                else:
                    log(f"第{ch}章 slop 不通过，重试 {attempt+1}/3")
            # 检查一致性
            consistency = agent.check_consistency(ch)
            if consistency.score < threshold:
                log(f"⚠️ 第{ch}章一致性评分偏低，建议人工复查")
```

## 写作流程设计

### 每章两阶段流程（对话模式 & 自动模式共用）

```
Phase 1: 创意写作 (temperature ~0.8)
  加载上下文：CRAFT.md + voice + world + characters + 大纲
  + 前一章末 ~1000 字 + 下一章大纲 + 活跃伏笔列表
  → Writer 生成章节正文
  → slop_checker 机械扫描

Phase 2: 状态沉淀 (temperature ~0.3)
  提取新事实 → 更新 truth files
  提取伏笔变更 → 更新 hook_ledger
  更新角色状态 → 更新 character_matrix
  追加章节摘要 → chapter_summaries

Phase 3: 质量审查（用户触发 / 自动模式自动执行）
  check_consistency（机械 + LLM 双层）
  check_slop（AI 痕迹深度检测）
  → 对话模式：用户决定 keep/discard/revise
  → 自动模式：分数 < 阈值 → 自动重试
```

### 层间传播规则

```
voice 变更    → 重评估所有章节的声音一致性
world 变更    → 检查所有章节的设定一致性
character 变更 → 检查涉及角色的章节对话/行为
outline 变更  → 重评估受影响章节的节拍覆盖
chapter 变更  → 检查伏笔账本、邻接章节连续性
```

## 工具清单

| 工具 | 功能 | 核心借鉴 |
|------|------|----------|
| `memory` | 记忆增删改查 | hermes memory_tool |
| `skill_view` | 加载写作方法论 | hermes skills_tool |
| `write_chapter` | 两阶段章节撰写 | inkos Writer+Settler |
| `develop_character` | 角色发展（三滑块、W/W/N/L） | autonovel CRAFT |
| `outline_plot` | 情节结构规划 | autonovel CRAFT |
| `track_hooks` | 伏笔账本 CRUD | inkos Hook-Ledger |
| `check_consistency` | 双层一致性审查 | autonovel evaluate.py |
| `check_slop` | AI 痕迹检测 | autonovel ANTI-SLOP |
| `search_lore` | 搜索世界观/角色/情节 | — |
| `web_search` | 联网搜索（查资料、考据、参考） | hermes web_tools.py |
| `web_fetch` | 提取网页内容为 markdown | hermes web_tools.py |
| `export_chapter` | 导出章节 | — |

## 实现阶段

### Phase 1：核心框架 + 双模式骨架
- 项目骨架 + CLI 入口（init/write/auto/resume/revise）
- Agent 主循环 + Anthropic API 集成
- `conversation_loop.py`（对话 REPL）
- `auto_pipeline.py`（自动循环骨架）
- `craft/CRAFT.md` + `craft/ANTI_SLOP.md` 编写

### Phase 2：记忆系统
- MemoryProvider ABC + MemoryManager + BuiltinProvider
- `memory_types.py` + 记忆召回 + `memory` 工具

### Phase 3：状态管理 + 伏笔
- `state/` 模块（truth_files + hook_ledger + schemas）
- 小说模板 + `track_hooks` 工具

### Phase 4：写作工具
- `write_chapter`（两阶段）+ `develop_character` + `outline_plot`
- `check_consistency` + `check_slop` + `search_lore`
- 自动模式的完整 keep/discard 循环

### Phase 5：上下文管理 + Skills
- ContextCompressor（小说适配版）
- Skill loader + 5 个内置 skills

### Phase 6：打磨
- 错误处理与重试、Prompt 优化、导出功能

## 有意的取舍

| 不做 | 原因 |
|------|------|
| ❌ 纯自动无人值守流水线 | 自动模式是**可中断、可切换**的，人在循环中是核心设计 |
| ❌ 10-agent 固定流水线 | 过度工程化，1 agent + 工具 + 双模式更灵活 |
| ❌ 纯 markdown 存储 | 改用 JSON truth + markdown 投射 |
| ❌ TUI/Web Studio（第一版） | CLI 优先，后续再加 |
| ❌ 多 LLM provider（第一版） | 专注 Anthropic API |
