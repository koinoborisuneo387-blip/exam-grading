# -*- coding: utf-8 -*-
"""成绩统计与试卷分析。"""
from __future__ import annotations

from . import db
from .grading import QTYPE_LABELS, round_score, to_float


def recalc_paper(paper_id: int) -> dict:
    """重算一份答卷的总分和批改状态。任何一次改分之后都要调。"""
    paper = db.query_one("SELECT * FROM paper WHERE id=?", (paper_id,))
    if not paper:
        return {}
    rows = db.query(
        "SELECT q.id AS qid, q.max_score, s.score "
        "FROM question q LEFT JOIN score s ON s.question_id=q.id AND s.paper_id=? "
        "WHERE q.exam_id=? ORDER BY q.sort_order, q.id",
        (paper_id, paper["exam_id"]),
    )
    subtotal = 0.0
    graded = 0
    for r in rows:
        if r["score"] is not None:
            subtotal += to_float(r["score"])
            graded += 1
    total = round_score(subtotal + to_float(paper["objective_score"]))

    if not rows:
        status = "done" if to_float(paper["objective_score"]) > 0 else "todo"
    elif graded == 0:
        status = "todo"
    elif graded < len(rows):
        status = "doing"
    else:
        status = "done"

    graded_at = paper["graded_at"]
    if status == "done" and not graded_at:
        graded_at = db.now()
    elif status != "done":
        graded_at = ""

    db.execute(
        "UPDATE paper SET total_score=?, status=?, graded_at=?, updated_at=? WHERE id=?",
        (total, status, graded_at, db.now(), paper_id),
    )
    return {
        "paper_id": paper_id,
        "total_score": total,
        "subjective_score": round_score(subtotal),
        "status": status,
        "graded_count": graded,
        "question_count": len(rows),
    }


def _median(values):
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return round_score(s[mid])
    return round_score((s[mid - 1] + s[mid]) / 2.0)


def _stdev(values, mean):
    if len(values) < 2:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return round_score(var ** 0.5)


def _ranked(papers):
    """并列名次：1, 2, 2, 4（同分同名次，下一名跳号）。"""
    ordered = sorted(papers, key=lambda p: -to_float(p["total_score"]))
    ranks = {}
    last_score, last_rank = None, 0
    for i, p in enumerate(ordered):
        score = to_float(p["total_score"])
        if last_score is None or score != last_score:
            last_rank = i + 1
            last_score = score
        ranks[p["id"]] = last_rank
    return ranks


def score_table(exam_id: int) -> dict:
    """成绩总表：每人每题得分 + 总分 + 名次。"""
    exam = db.query_one("SELECT * FROM exam WHERE id=?", (exam_id,))
    if not exam:
        return {}
    questions = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )
    students = db.query(
        "SELECT * FROM student WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )
    papers = db.query("SELECT * FROM paper WHERE exam_id=?", (exam_id,))
    paper_by_student = {p["student_id"]: p for p in papers}
    scores = db.query(
        "SELECT s.* FROM score s JOIN paper p ON p.id=s.paper_id WHERE p.exam_id=?",
        (exam_id,),
    )
    score_map = {}
    for s in scores:
        score_map[(s["paper_id"], s["question_id"])] = s

    done_papers = [p for p in papers if p["status"] == "done"]
    ranks = _ranked(done_papers)

    rows = []
    for stu in students:
        paper = paper_by_student.get(stu["id"])
        cells = []
        for q in questions:
            val = None
            if paper:
                rec = score_map.get((paper["id"], q["id"]))
                if rec:
                    val = rec["score"]
            cells.append(val)
        rows.append({
            "student_id": stu["id"],
            "student_no": stu["student_no"],
            "name": stu["name"],
            "paper_id": paper["id"] if paper else None,
            "status": paper["status"] if paper else "todo",
            "scores": cells,
            "objective_score": paper["objective_score"] if paper else 0,
            "total_score": paper["total_score"] if paper else 0,
            "comment": paper["comment"] if paper else "",
            "rank": ranks.get(paper["id"]) if paper else None,
        })
    return {"exam": exam, "questions": questions, "rows": rows}


def class_stats(exam_id: int) -> dict:
    """班级统计。只统计已经批完的答卷，没批完的不参与，免得平均分虚低。"""
    exam = db.query_one("SELECT * FROM exam WHERE id=?", (exam_id,))
    if not exam:
        return {}
    total_students = db.query_one(
        "SELECT COUNT(*) AS n FROM student WHERE exam_id=?", (exam_id,)
    )["n"]
    papers = db.query(
        "SELECT * FROM paper WHERE exam_id=? AND status='done'", (exam_id,)
    )
    values = [to_float(p["total_score"]) for p in papers]
    full = to_float(exam["full_score"], 100) or 100
    pass_line = to_float(exam["pass_score"])
    good_line = to_float(exam["excellent_score"])

    n = len(values)
    mean = round_score(sum(values) / n) if n else 0.0
    stats = {
        "total_students": total_students,
        "graded_count": n,
        "ungraded_count": max(0, total_students - n),
        "full_score": full,
        "mean": mean,
        "max": round_score(max(values)) if n else 0.0,
        "min": round_score(min(values)) if n else 0.0,
        "median": _median(values),
        "stdev": _stdev(values, mean) if n else 0.0,
        "pass_line": pass_line,
        "good_line": good_line,
        "pass_count": sum(1 for v in values if v >= pass_line) if n else 0,
        "good_count": sum(1 for v in values if v >= good_line) if n else 0,
    }
    stats["pass_rate"] = round_score(stats["pass_count"] * 100.0 / n) if n else 0.0
    stats["good_rate"] = round_score(stats["good_count"] * 100.0 / n) if n else 0.0

    # 按满分的 10% 一档分成 10 段
    step = full / 10.0
    buckets = []
    for i in range(10):
        lo = step * i
        hi = full if i == 9 else step * (i + 1)
        if i == 9:
            count = sum(1 for v in values if lo <= v <= hi)
        else:
            count = sum(1 for v in values if lo <= v < hi)
        buckets.append({
            "label": "%g~%g" % (round(lo, 1), round(hi, 1)),
            "low": round_score(lo),
            "high": round_score(hi),
            "count": count,
            "rate": round_score(count * 100.0 / n) if n else 0.0,
        })
    stats["buckets"] = buckets
    return stats


def question_stats(exam_id: int) -> list:
    """每题的得分率、满分人数、零分人数，按得分率从低到高排 —— 越靠前越该讲评。"""
    questions = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )
    rows = db.query(
        "SELECT s.question_id, s.score FROM score s "
        "JOIN paper p ON p.id=s.paper_id "
        "WHERE p.exam_id=? AND p.status='done' AND s.score IS NOT NULL",
        (exam_id,),
    )
    by_q = {}
    for r in rows:
        by_q.setdefault(r["question_id"], []).append(to_float(r["score"]))

    out = []
    for q in questions:
        vals = by_q.get(q["id"], [])
        full = to_float(q["max_score"])
        n = len(vals)
        mean = round_score(sum(vals) / n) if n else 0.0
        out.append({
            "question_id": q["id"],
            "no_label": q["no_label"],
            "qtype": q["qtype"],
            "qtype_label": QTYPE_LABELS.get(q["qtype"], q["qtype"]),
            "knowledge_point": q["knowledge_point"],
            "max_score": full,
            "graded_count": n,
            "mean": mean,
            "rate": round_score(mean * 100.0 / full) if full else 0.0,
            "full_count": sum(1 for v in vals if full and v >= full),
            "zero_count": sum(1 for v in vals if v <= 0),
        })
    out.sort(key=lambda x: (x["rate"], -x["max_score"]))
    return out


def knowledge_stats(exam_id: int) -> list:
    """按知识点汇总得分率。没填知识点的题归到「未标注」。"""
    qstats = question_stats(exam_id)
    groups = {}
    for q in qstats:
        key = (q["knowledge_point"] or "").strip() or "未标注"
        g = groups.setdefault(key, {"knowledge_point": key, "got": 0.0,
                                    "full": 0.0, "questions": []})
        g["got"] += q["mean"]
        g["full"] += q["max_score"]
        g["questions"].append(q["no_label"] or "?")
    out = []
    for g in groups.values():
        g["rate"] = round_score(g["got"] * 100.0 / g["full"]) if g["full"] else 0.0
        g["got"] = round_score(g["got"])
        g["full"] = round_score(g["full"])
        out.append(g)
    out.sort(key=lambda x: x["rate"])
    return out


def wrong_list(exam_id: int, threshold: float = 60.0) -> list:
    """错题清单：得分率低于 threshold% 的题，按学生归集。"""
    table = score_table(exam_id)
    if not table:
        return []
    questions = table["questions"]
    qmap = {q["id"]: q for q in questions}
    out = []
    for row in table["rows"]:
        items = []
        for i, q in enumerate(questions):
            got = row["scores"][i]
            if got is None:
                continue
            full = to_float(q["max_score"])
            if not full:
                continue
            rate = to_float(got) * 100.0 / full
            if rate < threshold:
                items.append({
                    "no_label": q["no_label"],
                    "knowledge_point": q["knowledge_point"],
                    "max_score": full,
                    "score": round_score(got),
                    "lost": round_score(full - to_float(got)),
                    "rate": round_score(rate),
                })
        if items:
            items.sort(key=lambda x: -x["lost"])
            out.append({
                "student_no": row["student_no"],
                "name": row["name"],
                "total_score": row["total_score"],
                "rank": row["rank"],
                "items": items,
            })
    _ = qmap
    return out


def full_report(exam_id: int) -> dict:
    return {
        "table": score_table(exam_id),
        "stats": class_stats(exam_id),
        "questions": question_stats(exam_id),
        "knowledge": knowledge_stats(exam_id),
    }
