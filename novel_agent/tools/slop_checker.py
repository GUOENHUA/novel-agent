"""AI writing pattern detection — mechanical regex + LLM judge.

Borrowed from autonovel `evaluate.py` + `ANTI-SLOP.md`.

Two layers:
  Layer 1 (mechanical): regex scans, zero LLM cost
  Layer 2 (LLM judge): deep quality analysis, called only when Layer 1 flags issues
"""

from __future__ import annotations

import re
from typing import Any

from novel_agent.tools.registry import registry, tool_error, tool_result

# -- Tier 1: Kill on sight ----------------------------------------------------
TIER1_PATTERNS = [
    (r"深入探讨", "直接讨论/分析"),
    (r"利用(?!率|器|品)", "用（当可用时）"),
    (r"促进", "帮助/让"),
    (r"踏上.*旅程", "开始/出发"),
    (r"宛如画卷|画卷般", "(删掉，描写实际景象)"),
    (r"见证了.*的变化", "显示了/证明了"),
    (r"协同效应", "(删掉重写)"),
    (r"催化(?!剂|器|酶)|作为.*催化剂", "触发/引发"),
    (r"并列|并置", "对比/放在一起"),
    (r"细腻入微的", "(删掉，展示具体细节)"),
]

# -- Tier 2: Suspicious in clusters -------------------------------------------
TIER2_PATTERNS = [
    r"强大的|稳健的",
    r"全面的",
    r"无缝的|无缝地",
    r"尖端的|前沿的",
    r"创新的",
    r"赋能",
    r"增强",
    r"优化",
    r"关键的",
    r"复杂的|错综复杂的",
    r"深刻的",
    r"共鸣",
    r"驾驭",
    r"巩固",
    r"基石",
]

# -- Fiction-specific AI patterns ---------------------------------------------
FICTION_PATTERNS = [
    (r"一阵.{1,10}涌上心头", "用身体反应展示情绪"),
    (r"心中涌起一股", "用动作或对话展示"),
    (r"内心深处感到", "描写具体感受"),
    (r"不禁感到", "删除'不禁'"),
    (r"眼睛瞪大了|瞪大了眼睛", "用更具体的动作替代"),
    (r"瞳孔骤然收缩", "极少人物能观察到瞳孔变化"),
    (r"呼吸一滞|呼吸急促起来", "用具体的身体感受"),
    (r"脊背发凉|背脊一凉", "找新的表达"),
    (r"如瀑的长发|长发如瀑", "陈词滥调"),
    (r"深邃的眼眸|犀利的目光", "描写具体的眼神"),
    (r"会心一笑|嘴角勾起一抹笑意", "避免使用"),
    (r"眼中闪过一丝", "你3章前就用过了"),
    (r"淡淡道|淡淡地说", "用语境暗示语气"),
    (r"轻声说道|低声道", "同上"),
    (r"冷哼一声", "找角色特有的表达"),
]

# -- Structural patterns ------------------------------------------------------
STRUCTURAL_PATTERNS = [
    (r"不是.{1,20}而是", "不是X而是Y结构，每章≤2次"),
    (r"然而[^，。,：]", "过渡词开头，每10段≤3个"),
    (r"此外[^，]", "同上"),
    (r"与此同时[^，]", "同上"),
]


def mechanical_scan(text: str) -> dict[str, Any]:
    """Layer 1: Run all mechanical regex scans. Zero LLM cost.

    Returns a dict with scan results and a pass/fail score.
    """
    tier1_hits = []
    for pattern, suggestion in TIER1_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            tier1_hits.append({"match": m if isinstance(m, str) else str(m), "suggestion": suggestion})

    tier2_hits = []
    for pattern in TIER2_PATTERNS:
        matches = re.findall(pattern, text)
        tier2_hits.extend(matches)

    fiction_hits = []
    for pattern, suggestion in FICTION_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            fiction_hits.append({"match": m if isinstance(m, str) else str(m), "suggestion": suggestion})

    # Em-dash density
    em_dashes = text.count("——") + text.count("—")
    em_dash_density = em_dashes / (len(text) / 1000) if text else 0

    # "不是X而是Y" density
    not_but_count = len(re.findall(r"不是.{1,20}而是", text))

    # Sentence length variance (rough: split by Chinese punctuation)
    sentences = re.split(r"[。！？；\n]", text)
    sentence_lengths = [len(s) for s in sentences if len(s) > 5]
    avg_len = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0

    # Scoring (starts at 10, deductions)
    score = 10.0
    score -= len(tier1_hits) * 0.5
    if len(tier2_hits) >= 3:
        score -= (len(tier2_hits) - 2) * 0.1
    score -= len(fiction_hits) * 0.3
    if em_dash_density > 3:
        score -= (em_dash_density - 3) * 0.1
    if not_but_count > 2:
        score -= (not_but_count - 2) * 0.2
    score = max(0, min(10, score))

    return {
        "score": round(score, 1),
        "passed": score >= 7.0,
        "tier1_hits": tier1_hits,
        "tier2_cluster": len(tier2_hits) >= 3,
        "tier2_count": len(tier2_hits),
        "fiction_hits": fiction_hits,
        "em_dash_density": round(em_dash_density, 1),
        "not_but_count": not_but_count,
        "avg_sentence_length": round(avg_len, 1),
        "needs_llm_review": score < 7.0,
        "summary": f"Score: {score:.1f}/10 | Tier1: {len(tier1_hits)} | Fiction: {len(fiction_hits)} | Em-dash: {em_dash_density:.1f}/k",
    }


def slop_tool_handler(args: dict[str, Any], **kwargs) -> str:
    """Handle slop checking requests."""
    action = args.get("action", "check")
    text = args.get("text", "")

    if not text:
        return tool_error("text is required.")

    if action == "check" or action == "mechanical":
        result = mechanical_scan(text)
        return tool_result(result)
    elif action == "llm_review":
        # Layer 2: Build directive for LLM deep review
        mechanical = mechanical_scan(text)
        if not mechanical["needs_llm_review"] and not args.get("force", False):
            return tool_result(
                **mechanical,
                llm_review_skipped=True,
                message="Mechanical scan passed. LLM review not needed unless force=True.",
            )
        return _build_llm_review_directive(text, mechanical)
    else:
        return tool_error(f"Unknown action: {action}")


def _build_llm_review_directive(text: str, mechanical: dict) -> str:
    """Build LLM deep review directive for flagged text."""
    directive = f"""## AI 痕迹深度审查

机械扫描结果:
- 评分: {mechanical['score']}/10
- Tier1 禁用词: {mechanical['tier1_hits']}
- 小说 AI 模式: {mechanical['fiction_hits']}
- 破折号密度: {mechanical['em_dash_density']}/千字

请审查以下文本的：
1. 散文质量 — 是否有 AI 特有的"稳定性"倾向（太干净、太安全）？
2. 声音一致性 — 情感强度是否有变化？
3. 角色区别度 — 对话是否各有特色？
4. 具体性 — "松鸦"还是"鸟"？

对发现问题的段落给出具体改写建议。

### 待审查文本
{text[:8000]}
"""
    return tool_result(
        success=True,
        action="llm_review",
        directive=directive,
        mechanical_score=mechanical["score"],
    )


# -- Tool schema ---------------------------------------------------------------

SLOP_TOOL_SCHEMA = {
    "name": "check_slop",
    "description": (
        "检测 AI 写作痕迹。两层机制：\n"
        "- check/mechanical: 机械正则扫描（无 LLM 成本）——Tier1 禁词、"
        "小说 AI 模式、破折号密度、句长变异、'不是X而是Y'密度\n"
        "- llm_review: LLM 深度审查（仅在机械扫描分数 < 7.0 时调用）\n\n"
        "返回分数（0-10）、命中的模式、是否通过（≥7.0）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check", "mechanical", "llm_review"],
                "description": "操作类型。check/mechanical=机械扫描，llm_review=LLM深度审查。"
            },
            "text": {
                "type": "string",
                "description": "要检测的文本内容。"
            },
            "force": {
                "type": "boolean",
                "description": "强制进行 LLM 审查（即使机械扫描通过）。"
            },
        },
        "required": ["action", "text"],
    },
}

registry.register(
    name="check_slop",
    toolset="evaluation",
    schema=SLOP_TOOL_SCHEMA,
    handler=slop_tool_handler,
    description="检测 AI 写作痕迹（机械正则 + LLM 深度审查）",
    emoji="🔍",
)
