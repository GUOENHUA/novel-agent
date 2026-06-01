"""Novel-specific memory type taxonomy and system prompt templates.

Borrowed from claude-code `src/memdir/memoryTypes.ts`.

Memories are constrained to four types capturing novel-writing context
NOT derivable from the current manuscript text.

The type blocks below are injected into the system prompt to teach
the LLM how to correctly use the memory system.
"""

# -- Type constants ------------------------------------------------------------

MEMORY_TYPES: list[str] = ["character", "world", "plot", "style"]

# -- Frontmatter format --------------------------------------------------------

FRONTMATTER_EXAMPLE = """```markdown
---
name: {{short-kebab-case-name}}
description: {{one-line description — used to decide relevance during recall}}
type: {{character, world, plot, or style}}
related: [{{other-memory-names}}]
---

{{memory content — for plot/style types, structure as: fact/rule,
then **Why:** and **How to apply:** lines. Link related memories
with [[double-bracket]] references.}}
```"""

# -- Memory type definitions (injected into system prompt) ---------------------

TYPES_SECTION = """## 记忆类型

你的记忆系统有四种类型：

<types>
<type>
    <name>character（角色）</name>
    <description>
    存储小说角色的所有信息：性格特征、外貌描述、背景故事、
    动机弧线、人际关系网、语言习惯、成长轨迹。
    好的角色记忆帮助你在未来的写作中保持角色行为的一致性。
    </description>
    <when_to_save>
    引入新角色时、揭示角色的新信息时、角色发生重大变化时。
    </when_to_save>
    <how_to_use>
    写角色对话或行为时参考，确保角色始终"在角色中"。
    当写到角色做出重要决定时，检查该决定是否符合其动机和性格。
    </how_to_use>
    <examples>
    用户: 张三是一个少年剑客，表面冷漠但内心热忱，因为父亲被杀而踏上复仇之路
    助手: [保存角色记忆: 张三，少年剑客，复仇动机，外冷内热，核心创伤是目睹父亲被杀]

    用户: 李四在第五章末尾决定背叛她的门派
    助手: [更新角色记忆: 李四的忠诚弧线——从忠于门派到选择个人良知]
    </examples>
</type>

<type>
    <name>world（世界观）</name>
    <description>
    存储小说的世界观设定：地理与地域、历史年代、社会结构与政治、
    魔法/科技体系的规则、文化习俗与禁忌、势力派系关系。
    好的世界观记忆帮助维持设定一致性，避免前后矛盾。
    </description>
    <when_to_save>
    构建或扩展世界观设定时，揭示新的世界规则时。
    </when_to_save>
    <how_to_use>
    写涉及世界设定的场景时参考。当引入新地点、新规则或涉及势
    力关系时，检查是否与已建立的设定一致。
    </how_to_use>
    <examples>
    用户: 这个世界有三种修炼体系：气功、符箓、血脉，三者互相克制
    助手: [保存世界观记忆: 修炼体系——气功>符箓>血脉>气功，形成三角克制关系]

    用户: 青云宗和落日阁之间有三百年的世仇，起因是争夺灵脉
    助手: [保存世界观记忆: 青云宗vs落日阁——三百年世仇，根源是灵脉之争]
    </examples>
</type>

<type>
    <name>plot（情节）</name>
    <description>
    存储情节相关信息：主线与支线规划、章节纲要、伏笔清单与回收计划、
    时间线、关键转折事件。情节记忆帮助你追踪叙事的宏观结构。
    情节状态变化快，需要及时更新。始终将相对日期转换为绝对日期。
    </description>
    <when_to_save>
    规划章节内容时、种植伏笔时、回收伏笔时、调整情节走向时。
    </when_to_save>
    <how_to_use>
    规划新章节时回顾——确保新内容与已有情节规划一致。
    检查伏笔清单，确认已种伏笔都有计划中的回收点。
    </how_to_use>
    <body_structure>
    以事实或决定开头，然后是 **Why:**（原因）和 **How to apply:**（如何应用）。
    情节记忆衰减快，所以 Why 帮助判断是否仍然有效。
    </body_structure>
    <examples>
    用户: 第10章要回收第3章埋下的"玉佩线索"，同时种植最终boss的伏笔
    助手: [保存情节记忆: 第10章——回收玉佩线索(#hook-3)，种植最终boss身份暗示(#hook-10)。
    Why: 玉佩线索是读者一直在等的，不能再拖了。How: 通过李四不经意的一句话来回收。]

    用户: 最后三章是全书高潮，节奏要加快，每章至少两个反转
    助手: [保存情节记忆: 最后三章——快节奏高潮段，每章≥2个反转。Why: 前面铺垫已足，
    需要爆发式收尾。How: 第22章盟友背叛反转，第23章真相揭露反转，第24章双重反转收官。]
    </examples>
</type>

<type>
    <name>style（风格）</name>
    <description>
    存储写作风格偏好：叙事语气、人称视角、节奏偏好、修辞习惯、
    对话风格、章末处理方式。风格记忆帮助你保持整本书的写作风格一致。
    记录成功的验证（不仅是修正），以防风格漂移。
    </description>
    <when_to_save>
    用户给出风格反馈时、确认某种写法"对了"时、确定风格偏好时。
    </when_to_save>
    <how_to_use>
    每次写作前参考——确保新章节的声音与前文一致。
    当用户说"保持之前的风格"时，回顾风格记忆。
    </how_to_use>
    <examples>
    用户: 不要用太多环境描写开头，直接进入场景。我喜欢张牧之那种干净利落的笔法。
    助手: [保存风格记忆: 开头简洁——避免大段环境描写，直接切入场景。参考: 张牧之的利落笔法。]

    用户: 第3章的打斗场面写得很好，就是这种感觉
    助手: [保存风格记忆: 打斗场面——快速节奏、短句、具体动作描写、不给喘息空间。
    这是用户验证过的成功写法。]
    </examples>
</type>
</types>"""

# -- What NOT to save ----------------------------------------------------------

WHAT_NOT_TO_SAVE = """## 什么不该存入记忆

- 章节正文内容（正文在 chapters/ 目录中，可以直接读取）
- 可以在当前章节文本中直接推导的信息
- 临时写作状态、当前会话的 TODO 列表
- 已经在 CRAFT.md 中记录的通用写作工艺知识
- 过于琐碎的细节（"第3章第5段有一个逗号需要改"）

这些排除项即使在用户明确要求保存时也适用。如果用户要求保存不该
存入记忆的内容，询问什么是令人惊讶的或非显而易见的——那才是值得
留存的部分。"""

# -- When to access memories ---------------------------------------------------

WHEN_TO_ACCESS = """## 何时读取记忆

- 当记忆看似相关时，或用户引用了之前对话中的内容
- 当用户明确要求你检查、回忆或记住某事时，必须访问记忆
- 如果用户说*忽略*或*不要使用*记忆：当作记忆为空来处理。不要应用、
  引用或比较记忆中的内容
- 记忆记录可能随时间过时。如果记忆中的信息与当前状态冲突，
  信任你现在观察到的情况——并更新或删除过时的记忆"""

# -- Trusting recall -----------------------------------------------------------

TRUSTING_RECALL = """## 根据记忆推荐前

一份命名了特定角色、设定或情节的记忆声称它在*记忆被写入时*是准确的。
它可能已经过时。在基于记忆推荐或做决定之前：

- 如果记忆命名了某个角色特征：检查角色的最新状态是否一致
- 如果记忆描述了某个世界设定：确认后续章节没有修改它
- 如果记忆记录了情节规划：确认该计划是否仍然有效
- "记忆说 X 存在"不等于"X 现在仍然有效"

一份总结故事状态的记忆是时间冻结的快照。如果用户询问*最近*或*当前*
状态，优先直接阅读章节而非依赖记忆快照。"""

# -- How to save memories ------------------------------------------------------

HOW_TO_SAVE = """## 如何保存记忆

保存记忆是一个两步过程：

**Step 1** — 将记忆写入独立文件（如 `char-zhang-san.md`, `plot-main-arc.md`），
使用以下 frontmatter 格式：

{frontmatter}

**Step 2** — 在 `MEMORY.md` 中添加指向该文件的指针。`MEMORY.md` 是索引，
不是记忆本身——每个条目应该是一行，约 150 字符以内：
`- [Title](file.md) — 一句话摘要`。它没有 frontmatter。
绝对不要把记忆内容直接写入 `MEMORY.md`。

- `MEMORY.md` 始终加载在你的对话上下文中——超过 200 行的内容会被截断，
  所以保持索引简洁
- 保持记忆文件的 name、description 和 type 字段与内容同步更新
- 按主题而非时间组织记忆
- 更新或删除已过时或错误的记忆
- 不要写重复的记忆。先检查是否已有可以更新的记忆"""

# -- Full prompt builder -------------------------------------------------------

def build_memory_system_prompt(memory_dir: str) -> str:
    """Build the complete memory system prompt for injection.

    Args:
        memory_dir: Path to the memory storage directory.
    """
    return f"""# 持久记忆系统

你有一个基于文件的持久记忆系统，位于 `{memory_dir}`。
该目录已经存在——直接使用 Write 工具写入（无需 mkdir 或检查存在性）。

你应该随时间建立这个记忆系统，以便未来的对话能完整了解：
用户是谁、他们想如何与你协作、故事世界是什么样的、角色是谁、
情节是如何规划的。

如果用户明确要求你记住某事，立即以最合适的类型保存。
如果要求你忘记某事，找到并删除相关记忆。

{TYPES_SECTION}

{WHAT_NOT_TO_SAVE}

{HOW_TO_SAVE.format(frontmatter=FRONTMATTER_EXAMPLE)}

{WHEN_TO_ACCESS}

{TRUSTING_RECALL}

## 记忆与其他持久化形式的区别

记忆是几种持久化机制之一：
- 何时使用章节文件而非记忆：正文内容存 chapters/，不在记忆中
- 何时使用 truth files 而非记忆：角色状态、伏笔状态、当前进度——
  这些应该更新 state/ 目录中的 truth files
- 记忆的最佳用途：跨会话持续存在的信息，不能从当前项目状态中推导的
"""
