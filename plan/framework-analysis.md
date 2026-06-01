# 四框架优点总结 & 采纳分析

## 1. claude-code（TypeScript/Bun — 通用软件工程 Agent）

**源码位置：** `D:\learn\claude-code\src\`

| 模块 | 核心设计 | 优点 | novel-agent 采纳 |
|------|----------|------|-----------------|
| `memdir/memoryTypes.ts` | 四类型记忆（user/feedback/project/reference），每种有详细的 when_to_save/how_to_use/examples | 精确的记忆分类法 + 系统提示模板，教会 LLM 正确使用记忆 | ✅ 改编为小说四类型（character/world/plot/style） |
| `memdir/memdir.ts` | MEMORY.md 索引文件，200 行/25KB 限制，每个记忆独立 .md 文件 | 索引机制防止上下文膨胀 | ✅ 采用 MEMORY.md 索引模式 |
| `memdir/findRelevantMemories.ts` | Side query 用 Sonnet 选最多 5 条最相关记忆 | 智能召回，不浪费上下文 | ✅ 采用 side query 召回 |
| `skills/bundledSkills.ts` | 内置 skill 的程序化注册 + 懒加载提取 | 编译时 skill，运行时按需加载 | ✅ 改编为 bundled skills |
| `skills/loadSkillsDir.ts` | 多层级 skill 发现（managed→user→project→additional），路径去重，条件激活 | 灵活的 skill 加载层级 | ✅ 采用多层级加载 |
| `tools.ts` | Feature-flagged 工具，dead code elimination | 条件编译减少体积 | ❌ Python 不适用 |
| `context.ts` | 双层上下文（system + user），memoized 整个会话 | 高效上下文缓存 | ✅ 采用 memoization 模式 |
| `services/compact/` | 丰富的 compaction 钩子（pre/post hooks） | 压缩生命周期管理 | ⚠️ 简化为 compressor 内置 |

**总体评价：** 记忆系统和 Skills 系统是 claude-code 的最大贡献。记忆分类法的细节程度（每种类型的 when_to_save/how_to_use/examples/body_structure）远超普通实现。

---

## 2. hermes-agent（Python — 通用软件工程 Agent）

**源码位置：** `D:\learn\hermes-agent\`

| 模块 | 核心设计 | 优点 | novel-agent 采纳 |
|------|----------|------|-----------------|
| `agent/memory_provider.py` | MemoryProvider ABC，完整生命周期钩子（initialize→system_prompt_block→prefetch→sync_turn→shutdown） | 可插拔架构，清晰的接口约定 | ✅ 直接作为架构基础 |
| `agent/memory_manager.py` | 编排器：内置 provider + 最多一个外部 provider，一个失败不影响其他 | 防止 schema 膨胀，容错设计 | ✅ 采用 Manager 模式 |
| `tools/memory_tool.py` | MemoryStore：§ 分隔条目，冻结快照，原子写入，外部漂移检测 | 前缀缓存稳定性 + 数据安全 | ✅ 采用冻结快照 + 原子写入 |
| `agent/context_compressor.py` | 五阶段压缩：剪枝→保护 head→token 预算 tail→LLM 总结→修复工具对 | 最完善的压缩实现之一 | ✅ 作为 compressor 的核心算法 |
| `agent/context_engine.py` | ContextEngine ABC，可替换的上下文管理 | 插件化上下文引擎 | ✅ 采用 ABC 模式 |
| `tools/registry.py` | ToolRegistry 单例：线程安全、generation counter、AST 自动发现、check_fn TTL 缓存 | 优雅的工具注册架构 | ✅ 作为 ToolRegistry 的核心设计 |
| `tools/skills_tool.py` | 三级渐进式披露：skills_list→skill_view→linked files | Token 高效的 skill 发现 | ✅ 采用渐进式披露 |
| `tools/skill_manager_tool.py` | Agent 可创建/编辑/删除 skill | Skill 成为"程序性记忆" | ⚠️ 后续版本加入 |
| `conversation_loop.py` | Turn-based 对话循环，集成内存/压缩/skill | 完整的 agent 循环实现 | ✅ 作为 conversation_loop 参考 |

**总体评价：** hermes-agent 提供了最完善的 agent 架构参考。MemoryProvider、ContextCompressor、ToolRegistry 三个 ABC/单例模式是 novel-agent 的核心骨架。

---

## 3. autonovel（Python — 全自动小说流水线）

**源码位置：** `D:\learn\autonovel\`

| 模块 | 核心设计 | 优点 | novel-agent 采纳 |
|------|----------|------|-----------------|
| `CRAFT.md` | ~17KB 系统性写作工艺教育：情节/角色/世界观/散文/伏笔 | 一套完整的、可操作的写作方法论 | ✅ 作为 `craft/CRAFT.md`，始终在系统提示中 |
| `ANTI-SLOP.md` | AI 写作痕迹检测：Tier 1-3 禁词、小说特有模式、结构标志 | 被生产验证有效的 AI 检测系统 | ✅ 作为 `craft/ANTI_SLOP.md` + `slop_checker` 工具 |
| `ANTI-PATTERNS.md` | 结构层面 AI 模式检测（"不是 X，而是 Y"等） | 补充词汇检测的不足 | ⚠️ 合并入 ANTI_SLOP.md |
| 五层小说栈 | voice→world→characters→outline→chapters + cross-cutting canon | 清晰的层间关系和传播规则 | ✅ 转化为记忆类型 + 传播规则 |
| `evaluate.py` | 双层评估：机械 slop 扫描（无 LLM）+ LLM 深度评判 | 低成本高质量评估 | ✅ 转化为 consistency_tool + slop_checker |
| `adversarial_edit.py` | "删 500 字"对抗性编辑 → 分类裁切（OVER-EXPLAIN/REDUNDANT 等） | 发现评分盲区的实际弱点 | ⚠️ 后续版本加入 |
| `reader_panel.py` | 4-persona 读者评审团，共识/分歧驱动决策 | 多维度评估 | ⚠️ 后续版本加入 |
| `review.py` | Opus 双角色评审 + 自动停止条件 | 高质量终审 | ⚠️ 后续版本加入 |
| seed.py → gen_* → draft → revise 流水线 | 完整的从种子到完稿的自动化 | 端到端流程参考 | ❌ 不做全自动，保持人在循环中 |
| `state.json` + git keep/discard | 状态追踪 + git 作为实验日志 | 简单的状态管理 | ✅ state/ 模块借鉴 |

**总体评价：** autonovel 的最大贡献是写作工艺知识（CRAFT.md + ANTI-SLOP.md）和双层评估系统。它是唯一一个真正"写出一本 79k 字小说"的项目，其工艺知识的实战验证价值无可替代。但全自动批处理模式不适合 novel-agent 的对话式定位。

---

## 4. inkos（TypeScript/Node — 工业化小说生产系统）

**源码位置：** `D:\learn\inkos\packages\`

| 模块 | 核心设计 | 优点 | novel-agent 采纳 |
|------|----------|------|-----------------|
| 10-agent pipeline | Radar→Planner→Composer→Architect→Writer→Observer→Reflector→Normalizer→Auditor→Reviser | 高度分工，每个 agent 职责明确 | ❌ 不做 10-agent，保持 1 agent + 工具 |
| Hook-Ledger | 伏笔生命周期：upsert/mention/resolve/defer + 健康分析 | 成熟的伏笔管理系统 | ✅ 作为 `hook_ledger.py` + `hook_tool` |
| JSON truth files + SQLite MemoryDB | 结构化状态 + markdown 投射 + 时态数据库 | 数据完整性 + 人类可读 | ✅ JSON truth + markdown（不加 SQLite，第一版够用） |
| Input Governance | ChapterIntent→ChapterMemo→ContextPackage→RuleStack, L4→L3 override | 精细的上下文控制 | ⚠️ 简化为 prompt_builder 的一部分 |
| 33-dimension audit | 涵盖 OOC/时间线/传说冲突/力量体系/节奏/风格/词汇疲劳等 | 极其全面的质量审计 | ⚠️ 简化为 10-15 维度 |
| 两阶段写章 | 创意阶段(temp 0.7) + 状态沉淀(temp 0.3) | 分离创意与结构化 | ✅ 作为 write_chapter 的核心流程 |
| 多模型路由 | 不同 agent 用不同模型（writer→Sonnet, auditor→GPT-4o 等） | 质量与成本平衡 | ❌ 第一版专注 Anthropic |
| Context budgeting | 每部分上下文有字符预算（storyBible=14K, currentState=7K 等） | 精确的上下文控制 | ✅ 在 prompt_builder 中实现 |
| 3 交互表面共享内核 | CLI + TUI + Studio 共用 interaction runtime | 架构清晰 | ⚠️ CLI 优先，架构预留扩展点 |
| 40+ LLM provider | 内置大量 provider 预设 | 广泛的兼容性 | ❌ 第一版只做 Anthropic |
| AI 反检测 | Reviser 有 `anti-detect` 模式，专门降低 AI 可检测性 | 实用的质量提升 | ⚠️ 合并入 slop_checker |
| Long-span fatigue detection | 多章跨度检测词汇/结构/节奏疲劳 | 长篇写作特有需求 | ⚠️ 后续版本加入 |

**总体评价：** inkos 是最成熟的工业化小说生产系统，但其复杂度也最高。Hook-Ledger、JSON truth files、两阶段写章流程是最值得借鉴的三个设计。10-agent 架构不适合 novel-agent——我们选择 1 agent + 工具的方式，更灵活、更容易扩展。

---

## 采纳优先级汇总

### 必须采纳（Phase 1-3）
1. **hermes MemoryProvider + MemoryManager** — 记忆系统骨架
2. **claude-code 四类型记忆分类** — 改编为 character/world/plot/style
3. **autonovel CRAFT.md + ANTI_SLOP.md** — 写作工艺教育 + AI 检测
4. **hermes ToolRegistry** — 工具系统骨架
5. **hermes ContextCompressor 五阶段算法** — 上下文管理核心
6. **inkos Hook-Ledger** — 伏笔管理
7. **inkos JSON truth files** — 结构化状态存储
8. **inkos 两阶段写章流程** — 创意+沉淀分离

### 后续版本加入（Phase 4+）
9. **autonovel adversarial_edit** — 对抗性编辑
10. **autonovel reader_panel** — 多角色评审
11. **inkos multi-model routing** — 不同 agent 用不同模型
12. **inkos long-span fatigue detection** — 长篇疲劳检测

### 不做
13. autonovel 全自动流水线（保持人在循环中）
14. inkos 10-agent 固定 pipeline（1 agent + 工具更灵活）
15. inkos TUI/Studio（CLI 优先）
16. inkos 40+ provider（先专注 Anthropic）
