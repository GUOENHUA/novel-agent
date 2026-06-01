# Novel-Agent 架构设计计划 v1（初版）

> 基于 claude-code 和 hermes-agent 的分析，在对比 autonovel/inkos 之前的初版设计

## Context

novel-agent 是一个基于 LLM 的小说写作辅助 agent。核心理念：借鉴 claude-code（TypeScript/Bun）和 hermes-agent（Python）的成熟架构模式，为小说写作场景构建一个具有持久记忆、长文本管理、可插拔写作方法论的 agent。

## 技术选型

| 项目 | 选择 |
|------|------|
| 语言 | Python 3.12+ |
| LLM | Anthropic API（Claude Opus/Sonnet） |
| CLI 框架 | Click（简洁，社区成熟） |
| Schema 验证 | Pydantic v2 |
| 异步 | asyncio + httpx |
| 包管理 | uv / pyproject.toml |
| 交互 | CLI 优先，Web 后续 |

## 架构总览

### 关键设计借鉴

| 机制 | 借鉴来源 | 核心思路 |
|------|----------|----------|
| 记忆系统 | claude-code `memdir/` + hermes `MemoryProvider` | 四类型小说记忆分类法 + 可插拔 provider 架构 |
| 上下文管理 | hermes `ContextCompressor` | 结构化压缩模板（适配小说），迭代式增量更新 |
| Skills | claude-code `skills/` + hermes `skills_tool.py` | SKILL.md 渐进式披露，写作方法论具象化为 skill |
| Tools | hermes `registry.py` | ToolRegistry 中央注册表，自注册模式 |
| Agent Loop | hermes `conversation_loop.py` | turn-based 对话循环，内存管理/压缩 hook |

### 项目结构

```
novel-agent/
├── novel_agent/
│   ├── __init__.py
│   ├── cli.py
│   ├── agent.py
│   ├── conversation_loop.py
│   │
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── memory_provider.py    # MemoryProvider ABC
│   │   ├── memory_manager.py     # MemoryManager 编排器
│   │   ├── memory_store.py       # 文件持久化存储
│   │   ├── memory_types.py       # 小说四类型定义 + prompt 模板
│   │   ├── builtin_provider.py   # 内置文件记忆 provider
│   │   └── recall.py             # 记忆召回
│   │
│   ├── context/
│   │   ├── __init__.py
│   │   ├── context_engine.py     # ContextEngine ABC
│   │   ├── compressor.py         # 小说适配压缩器
│   │   ├── prompt_builder.py     # 系统提示构建器
│   │   └── token_counter.py      # Token 估算与追踪
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py           # ToolRegistry 单例
│   │   ├── memory_tool.py        # 记忆 CRUD
│   │   ├── skill_tool.py         # Skill 查看/管理
│   │   ├── chapter_tool.py       # 章节撰写
│   │   ├── character_tool.py     # 角色发展
│   │   ├── plot_tool.py          # 情节大纲
│   │   ├── consistency_tool.py   # 一致性审查
│   │   └── lore_tool.py          # 世界观搜索
│   │
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   └── bundled/
│   │       ├── character_dev/SKILL.md
│   │       ├── world_building/SKILL.md
│   │       ├── plot_structure/SKILL.md
│   │       ├── dialogue_writing/SKILL.md
│   │       └── scene_construction/SKILL.md
│   │
│   └── utils/
│       ├── __init__.py
│       ├── constants.py
│       └── files.py
│
├── templates/
│   ├── character_template.md
│   ├── world_template.md
│   └── plot_template.md
│
├── pyproject.toml
└── README.md
```

## 详细模块设计

### 1. 记忆系统（最高优先级）

#### 1.1 小说四类型记忆分类法

借鉴 claude-code `memoryTypes.ts` 的精确定义方式：

| 类型 | 用途 | when_to_save | 文件命名约定 |
|------|------|-------------|-------------|
| `character` | 角色：性格、外貌、背景故事、动机弧线、关系网、语言习惯 | 引入新角色/揭示角色新信息时 | `char-{name}.md` |
| `world` | 世界观：地理、历史年代、社会结构、魔法/科技体系、文化习俗、势力派系 | 构建/扩展世界观设定时 | `world-{topic}.md` |
| `plot` | 情节：主线/支线、章节纲要、伏笔追踪、时间线、关键事件 | 规划章节/揭示情节点时 | `plot-{arc-name}.md` |
| `style` | 风格：叙事语气、人称视角、节奏偏好、修辞习惯、对话风格 | 用户反馈/确认风格偏好时 | `style-{aspect}.md` |

每条记忆格式：
```markdown
---
name: char-zhang-san
description: 主角张三 — 少年剑客，复仇动机，表面冷漠内心热忱
type: character
related: [char-li-si, plot-main-arc, world-jianghu]
---
content...
```

#### 1.2 MemoryProvider 架构

```python
class MemoryProvider(ABC):
    """可插拔记忆提供者基类"""
    @property
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None: ...
    def system_prompt_block(self) -> str: ...
    def prefetch(self, query: str) -> str: ...
    def sync_turn(self, user_msg, assistant_msg) -> None: ...
    @abstractmethod
    def get_tool_schemas(self) -> list[dict]: ...
    def shutdown(self) -> None: ...
```

#### 1.3 智能记忆召回

- 扫描所有记忆文件的 frontmatter（name + description）
- 用 side query 让 Claude 选择最多 5 条最相关的
- 结果注入系统提示或用户上下文

### 2. 上下文管理（次高优先级）

#### 2.1 小说适配的压缩器

五阶段算法（借鉴 hermes ContextCompressor），总结模板适配小说：

```
## 📖 当前写作进度
## 🎬 当前场景状态
## 👥 活跃角色状态
## 📝 已完成内容
## 🔮 伏笔与未解线索
## ⚠️ 一致性约束
## 📋 待写内容
## 🎯 用户最新指令
```

#### 2.2 关键机制
- Head 保护：系统提示 + 最近几条消息
- Token 预算 tail 保护：保留最近 ~20% 上下文窗口
- 迭代式更新：二次压缩时增量更新总结
- 防抖：追踪压缩效率，低效时暂停

### 3. 工具系统

ToolRegistry 单例 + 自注册模式（借鉴 hermes `registry.py`）：

```python
from novel_agent.tools.registry import registry

registry.register(
    name="write_chapter",
    toolset="writing",
    schema={...},
    handler=write_chapter_handler,
    check_fn=check_requirements,
    emoji="✍️",
)
```

第一版工具：memory, skill_view, write_chapter, develop_character, outline_plot, check_consistency, search_lore, track_foreshadowing

### 4. Skills 系统

- 加载优先级：内置 skills → 项目级 `.novel-agent/skills/` → 用户级 `~/.novel-agent/skills/`
- SKILL.md 格式：YAML frontmatter + Markdown 正文
- 渐进式披露：`skill_view(name)` 加载完整内容

### 5. CLI 设计

```bash
novel-agent write --project ./my-novel
novel-agent resume
novel-agent memory list --type character
novel-agent skills list
novel-agent init ./my-novel
```

## 实现阶段

### Phase 1：核心框架
- 项目骨架搭建、CLI 入口、Agent 主循环、Anthropic API 集成

### Phase 2：记忆系统
- MemoryProvider ABC + MemoryManager + BuiltinProvider + 记忆召回 + memory 工具

### Phase 3：上下文管理
- ContextEngine ABC + ContextCompressor + 小说适配压缩模板

### Phase 4：写作工具 + Skills
- 核心写作工具 + Skill loader + 5 个内置 skills

### Phase 5：打磨
- 模板系统、错误处理、Prompt 优化
