# Novel-Agent 架构设计计划 v2（四项目对比后修订）

> 基于 claude-code + hermes-agent + autonovel + inkos 的深度源码分析

## Context

novel-agent 是一个基于 LLM 的**对话式**小说写作 agent。通过深度分析四个项目的源码，精炼各家长处：

| 项目 | 语言 | 本质 | 核心优势 |
|------|------|------|----------|
| claude-code | TypeScript/Bun | 通用软件工程 agent | 类型化记忆分类法、Skill 渐进式披露、MEMORY.md 索引 |
| hermes-agent | Python | 通用软件工程 agent | MemoryProvider 可插拔架构、ContextCompressor 五阶段压缩、ToolRegistry |
| autonovel | Python | 全自动小说流水线 | CRAFT.md 工艺教育、ANTI-SLOP 检测、五层小说栈、多层评估、keep/discard 循环 |
| inkos | TypeScript/Node | 工业化小说生产系统 | 10-agent 分工、Hook-Ledger 伏笔账本、JSON truth files、33 维审计、TUI/Studio |

**定位：** 取 claude-code/hermes 的 agent 架构 + autonovel 的写作工艺 + inkos 的数据管理 = 一个**可对话、可记忆、可持续创作**的小说写作 agent。

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
| 记忆系统 | claude-code `memdir/` | hermes `MemoryProvider` | 四类型小说记忆分类 + 可插拔 provider |
| 上下文管理 | hermes `ContextCompressor` | — | 五阶段压缩 + 小说适配模板 |
| Skills | claude-code `skills/` | hermes `skills_tool.py` | 渐进式披露，写作方法论具象化 |
| Tools | hermes `registry.py` | — | 中央注册表 + 自注册模式 |
| Agent Loop | hermes `conversation_loop.py` | — | turn-based 对话循环 |
| **写作工艺** | **autonovel `CRAFT.md`** | — | 系统性写作教育，始终加载 |
| **AI 检测** | **autonovel `ANTI-SLOP.md`** | — | 机械正则 + LLM 双层检测 |
| **伏笔管理** | **inkos Hook-Ledger** | — | upsert/mention/resolve/defer 生命周期 |
| **状态存储** | **inkos JSON truth files** | hermes MemoryStore | JSON 结构化 + markdown 可读双存储 |
| **写章流程** | **inkos 两阶段** | autonovel 评估循环 | 创意阶段(高温) + 沉淀阶段(低温) |

## 项目结构

```
novel-agent/
├── novel_agent/
│   ├── __init__.py
│   ├── cli.py
│   ├── agent.py
│   ├── conversation_loop.py
│   │
│   ├── memory/                   # 记忆子系统
│   │   ├── memory_provider.py    # MemoryProvider ABC（借鉴 hermes）
│   │   ├── memory_manager.py     # MemoryManager 编排器
│   │   ├── memory_store.py       # 文件持久化（借鉴 hermes MemoryStore）
│   │   ├── memory_types.py       # 四类型定义 + 系统提示模板
│   │   ├── builtin_provider.py   # 内置文件记忆 provider
│   │   └── recall.py             # 智能记忆召回（借鉴 claude-code）
│   │
│   ├── context/                  # 上下文管理子系统
│   │   ├── context_engine.py     # ContextEngine ABC（借鉴 hermes）
│   │   ├── compressor.py         # 五阶段压缩器（借鉴 hermes）
│   │   ├── prompt_builder.py     # 系统提示构建器
│   │   └── token_counter.py      # Token 估算
│   │
│   ├── tools/                    # 工具子系统
│   │   ├── registry.py           # ToolRegistry 单例（借鉴 hermes）
│   │   ├── memory_tool.py        # 记忆 CRUD
│   │   ├── skill_tool.py         # Skill 查看/管理
│   │   ├── chapter_tool.py       # 章节撰写（两阶段，借鉴 inkos）
│   │   ├── character_tool.py     # 角色发展（借鉴 autonovel CRAFT）
│   │   ├── plot_tool.py          # 情节大纲
│   │   ├── hook_tool.py          # 伏笔账本（借鉴 inkos Hook-Ledger）
│   │   ├── consistency_tool.py   # 一致性审查（双层，借鉴 autonovel）
│   │   ├── lore_tool.py          # 世界观搜索
│   │   └── slop_checker.py       # AI 痕迹检测（借鉴 autonovel ANTI-SLOP）
│   │
│   ├── skills/                   # Skills 子系统
│   │   ├── loader.py             # Skill 发现与加载
│   │   └── bundled/              # 内置写作 skills
│   │       ├── character_dev/SKILL.md
│   │       ├── world_building/SKILL.md
│   │       ├── plot_structure/SKILL.md
│   │       ├── dialogue_writing/SKILL.md
│   │       └── scene_construction/SKILL.md
│   │
│   ├── craft/                    # 写作工艺（★ 借鉴 autonovel）
│   │   ├── CRAFT.md              # 核心工艺教育，始终在系统提示中
│   │   └── ANTI_SLOP.md          # AI 痕迹检测参考，始终在系统提示中
│   │
│   ├── state/                    # 小说状态管理（★ 借鉴 inkos）
│   │   ├── truth_files.py        # JSON + markdown 双存储
│   │   ├── hook_ledger.py        # 伏笔生命周期管理
│   │   └── schemas.py            # Pydantic 状态模型
│   │
│   └── utils/
│
├── templates/                    # 小说模板
│   ├── character_template.md
│   ├── world_template.md
│   ├── outline_template.md
│   └── seed_prompt.md
│
├── plan/                         # 设计文档
│   ├── architecture-v1.md
│   ├── architecture-v2.md
│   └── framework-analysis.md
│
├── pyproject.toml
└── README.md
```

## 关键新增模块详解（v1→v2 变化）

### craft/ — 写作工艺教育（★ 借鉴 autonovel）

autonovel 最大的创新之一，v1 plan 原缺失。

- `CRAFT.md`（~17KB）：
  - 情节结构：Save the Cat 节拍表、Dan Harmon 故事圈、Sanderson Promise/Progress/Payoff、MICE 商数
  - 角色工艺：三滑块模型、Wound/Want/Need/Lie 因果链、对话区别度 8 维度
  - 世界观：Sanderson 魔法三定律、冰山原则
  - 伏笔规则 + Show Don't Tell 操作定义 + 散文工艺 + 稳定性陷阱对策
- `ANTI_SLOP.md`：AI 痕迹检测参考 — Tier 1-3 禁词、小说特有 AI 标志、结构模式
- **加载策略**：始终在系统提示中（~8K tokens），作为 agent 的"必读教材"

### state/ — 小说状态管理（★ 借鉴 inkos）

inkos 的 truth files 是 JSON+markdown 双存储，比 autonovel 的纯 markdown 更结构化。

- `truth_files.py`：current_state / pending_hooks / chapter_summaries / character_matrix
- `hook_ledger.py`：伏笔操作（upsert/mention/resolve/defer）+ 状态机验证
- `schemas.py`：Pydantic 模型，支持 JSON 序列化 + markdown 投射

### tools/slop_checker.py — AI 痕迹检测（★ 借鉴 autonovel）

双免疫系统（已被 autonovel 生产验证）：

- 第一层：机械正则扫描（无 LLM，零成本）— 禁词、小说模式、破折号密度、句长变异系数
- 第二层：LLM 深度评判 — 仅在第一层标记段落后调用

### tools/hook_tool.py — 伏笔账本（★ 借鉴 inkos）

inkos Hook-Ledger 的 Python 移植：
- 操作：upsert / mention / resolve / defer
- 每条伏笔记录：id、种植章节、类型、状态、预期回收章节、关联角色
- 一致性检查：未回收列表、缺失种植的回收、过期未回收

## 写作流程设计

### 每章两阶段流程（借鉴 inkos + autonovel）

```
用户触发 "写第3章"
    │
    ▼
Phase 1: 创意写作 (temperature ~0.8)
  加载上下文：CRAFT.md + voice + world + characters + 大纲
  + 前一章末 ~1000 字 + 下一章大纲 + 活跃伏笔列表
  → Writer 生成章节正文
  → slop_checker 机械扫描
    │
    ▼
Phase 2: 状态沉淀 (temperature ~0.3)
  提取新事实 → 更新 truth files
  提取伏笔变更 → 更新 hook_ledger
  更新角色状态 → 更新 character_matrix
  追加章节摘要 → chapter_summaries
    │
    ▼
Phase 3: 质量审查（用户触发）
  check_consistency（机械 + LLM 双层）
  check_slop（AI 痕迹深度检测）
  用户决定 keep/discard/revise
```

### 层间传播规则（借鉴 autonovel）

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
| `export_chapter` | 导出章节 | — |

## 实现阶段

### Phase 1：核心框架
- 项目骨架 + CLI 入口 + Agent 主循环 + Anthropic API 集成
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

### Phase 5：上下文管理 + Skills
- ContextCompressor + Skill loader + 5 个内置 skills

### Phase 6：打磨
- 错误处理、Prompt 优化、导出

## 有意的取舍

| 不做 | 原因 |
|------|------|
| ❌ 全自动批处理流水线 | 保持对话式交互，人在循环中 |
| ❌ 10-agent 固定流水线 | 过度工程化，1 agent + 工具更灵活 |
| ❌ 纯 markdown 存储 | 改用 JSON truth + markdown 投射 |
| ❌ TUI/Web Studio（第一版） | CLI 优先，后续再加 |
| ❌ 多 LLM provider（第一版） | 专注 Anthropic API |
