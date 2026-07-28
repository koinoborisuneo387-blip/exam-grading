# -*- coding: utf-8 -*-
"""题型定义与分数处理。

**本系统只批主观题**（解答、简答、计算、作文……）。
选择题、判断题、填空题这类客观题老师另外处理，不进系统逐题批 ——
如果需要算总分，在批改台顶部填一个「客观题得分」合计就行。

纯函数，不碰数据库，方便单测。
"""
from __future__ import annotations

# 全部是主观题。类型只影响统计口径和 AI 提示词的措辞，判分方式都一样：老师给分。
QTYPES = ("essay", "short", "calc", "composition", "discuss", "other")
QTYPE_LABELS = {
    "essay": "解答题",
    "short": "简答题",
    "calc": "计算题",
    "composition": "作文",
    "discuss": "论述题",
    "other": "其他",
}
DEFAULT_QTYPE = "essay"

# 给 AI 出建议分时，不同题型的口吻不一样
AI_STYLE = {
    "composition": "作文",
    "discuss": "论述题",
    "calc": "计算题",
    "short": "简答题",
    "essay": "解答题",
    "other": "主观题",
}


def normalize_qtype(qtype) -> str:
    q = str(qtype or "").strip()
    return q if q in QTYPES else DEFAULT_QTYPE


def round_score(value):
    """分数统一保留两位小数，顺手把 -0.0 抹掉。"""
    if value is None:
        return None
    v = round(float(value) + 0.0, 2)
    return 0.0 if v == 0 else v


def clamp_score(value, max_score):
    """老师手输的分限制在 [0, 满分] 之间。

    传 None 或空字符串表示「清空这题的分」（回到未批状态），返回 None。
    """
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    try:
        full = float(max_score or 0)
    except (TypeError, ValueError):
        full = 0.0
    if v < 0:
        v = 0.0
    if full > 0 and v > full:
        v = full
    return round_score(v)


def to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def paper_total(question_scores, objective_score=0.0) -> float:
    """总分 = 各主观题得分之和 + 客观题合计分。没批的题按 0 算。"""
    total = 0.0
    for s in question_scores:
        if s is not None:
            total += to_float(s, 0.0)
    total += to_float(objective_score, 0.0)
    return round_score(total)


def is_fully_graded(scores) -> bool:
    """所有主观题都给过分了才算批完。"""
    scores = list(scores)
    if not scores:
        return False
    return all(s is not None for s in scores)
