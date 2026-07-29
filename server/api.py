# -*- coding: utf-8 -*-
"""HTTP API 路由与处理。所有响应都是 JSON，除了文件下载。"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from base64 import b64decode
from datetime import datetime
from pathlib import Path

from . import ai, analysis, config, db, imgutil, pdfimg, roster, xlsx
from .grading import (DEFAULT_QTYPE, QTYPE_LABELS, QTYPES, clamp_score,
                      normalize_qtype, round_score, to_float)

ROUTES = []

MAX_UPLOAD = 300 * 1024 * 1024  # 300MB，整班扫描件也够了
IMAGE_EXTS = (".jpg", ".png", ".gif", ".webp", ".bmp")


class ApiError(Exception):
    def __init__(self, message, status=400):
        Exception.__init__(self, message)
        self.message = message
        self.status = status


class FileResponse(object):
    def __init__(self, data, content_type, filename=None, inline=False,
                 max_age=0, path=None, cleanup=False):
        self.data = data
        self.content_type = content_type
        self.filename = filename
        self.inline = inline
        self.max_age = max_age
        # path 不为 None 时改为从这个文件分块流式发送（备份包可能几百 MB，
        # 不许整块读进内存）；cleanup=True 表示发完把临时文件删掉
        self.path = path
        self.cleanup = cleanup


def route(method, pattern):
    compiled = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method.upper(), compiled, fn))
        return fn
    return deco


def dispatch(ctx):
    for method, pattern, fn in ROUTES:
        if method != ctx.method:
            continue
        m = pattern.match(ctx.path)
        if m:
            return fn(ctx, *m.groups())
    raise ApiError("接口不存在：%s %s" % (ctx.method, ctx.path), 404)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _rel_to_abs(rel: str) -> Path:
    """把库里存的相对路径还原成绝对路径，顺便挡掉路径穿越。"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ApiError("文件路径不合法", 400)
    base = config.data_dir().resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ApiError("文件路径不合法", 400)
    return target


def _abs_to_rel(path: Path) -> str:
    return path.resolve().relative_to(config.data_dir().resolve()).as_posix()


def _exam(exam_id):
    row = db.query_one("SELECT * FROM exam WHERE id=?", (exam_id,))
    if not row:
        raise ApiError("这场考试不存在，可能已经被删掉了。", 404)
    return row


def _student(student_id):
    row = db.query_one("SELECT * FROM student WHERE id=?", (student_id,))
    if not row:
        raise ApiError("这个学生不存在。", 404)
    return row


def _paper(paper_id):
    row = db.query_one("SELECT * FROM paper WHERE id=?", (paper_id,))
    if not row:
        raise ApiError("这份答卷不存在。", 404)
    return row


def _page(page_id):
    row = db.query_one("SELECT * FROM page WHERE id=?", (page_id,))
    if not row:
        raise ApiError("这一页不存在。", 404)
    return row


def _ensure_paper(exam_id, student_id):
    row = db.query_one(
        "SELECT * FROM paper WHERE exam_id=? AND student_id=?", (exam_id, student_id)
    )
    if row:
        return row
    pid = db.execute(
        "INSERT INTO paper (exam_id, student_id, status, updated_at) VALUES (?,?,'todo',?)",
        (exam_id, student_id, db.now()),
    )
    return _paper(pid)


def _next_sort(table, exam_id):
    row = db.query_one(
        "SELECT COALESCE(MAX(sort_order), 0) AS m FROM %s WHERE exam_id=?" % table,
        (exam_id,),
    )
    return (row["m"] or 0) + 10


# --------------------------------------------------------------------------
# 版本 / 设置 / AI
# --------------------------------------------------------------------------

@route("GET", r"/api/version")
def api_version(ctx):
    return {"version": config.version()}


def _client_config() -> dict:
    cfg = config.public_config()
    cfg["qtypes"] = [{"value": q, "label": QTYPE_LABELS[q]} for q in QTYPES]
    cfg["version"] = config.version()
    return cfg


@route("GET", r"/api/config")
def api_get_config(ctx):
    return _client_config()


@route("POST", r"/api/config")
def api_set_config(ctx):
    config.save_config(ctx.json())
    return _client_config()


@route("POST", r"/api/ai/test")
def api_ai_test(ctx):
    try:
        return ai.test_connection()
    except ai.AIError as exc:
        raise ApiError(str(exc), 400)


@route("POST", r"/api/ai/suggest")
def api_ai_suggest(ctx):
    body = ctx.json()
    paper_id = _int(body.get("paper_id"))
    question_id = _int(body.get("question_id"))
    if not paper_id or not question_id:
        raise ApiError("缺少答卷或题目编号。")
    paper = _paper(paper_id)
    question = db.query_one("SELECT * FROM question WHERE id=?", (question_id,))
    if not question or question["exam_id"] != paper["exam_id"]:
        raise ApiError("这道题不属于这场考试。")

    answer = str(body.get("student_answer") or "")
    _upsert_score(paper_id, question_id, student_answer=answer)

    try:
        result = ai.suggest(question, answer,
                            images=body.get("images"),
                            key_images=body.get("key_images"))
    except ai.AIError as exc:
        raise ApiError(str(exc), 400)

    db.execute(
        "UPDATE score SET ai_suggested_score=?, ai_comment=?, ai_accepted=0, updated_at=? "
        "WHERE paper_id=? AND question_id=?",
        (result["score"], result["comment"], db.now(), paper_id, question_id),
    )
    result["note"] = "这是 AI 的建议，点「采纳」才会记进成绩。"
    return result


@route("POST", r"/api/ai/grade_paper")
def api_ai_grade_paper(ctx):
    """整卷预批：把学生答卷图 + 标准答案图一起发给 AI，一次批完所有主观题。

    结果只写进 ai_suggested_score / ai_comment，**绝不动 score.score**。
    """
    body = ctx.json()
    paper_id = _int(body.get("paper_id"))
    if not paper_id:
        raise ApiError("缺少答卷编号。")
    paper = _paper(paper_id)
    questions = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id",
        (paper["exam_id"],),
    )
    try:
        result = ai.grade_paper(questions, body.get("images"), body.get("key_images"))
    except ai.AIError as exc:
        raise ApiError(str(exc), 400)

    for item in result["items"]:
        _upsert_score(paper_id, item["question_id"])
        detail = item["comment"]
        if item["reasons"]:
            detail = (detail + "\n" if detail else "") + "\n".join(
                "· " + r for r in item["reasons"])
        db.execute(
            "UPDATE score SET ai_suggested_score=?, ai_comment=?, ai_accepted=0, "
            "updated_at=? WHERE paper_id=? AND question_id=?",
            (item["score"], detail, db.now(), paper_id, item["question_id"]),
        )
    result["note"] = "以上全是 AI 的建议分，逐题点「采纳」才会记进成绩。"
    return result


# --------------------------------------------------------------------------
# 标准答案卷
# --------------------------------------------------------------------------

def _key_dir(exam_id) -> Path:
    p = config.uploads_dir() / str(exam_id) / "_key"
    p.mkdir(parents=True, exist_ok=True)
    return p


@route("GET", r"/api/exams/(\d+)/answerkey")
def api_answerkey_list(ctx, exam_id):
    _exam(exam_id)
    return {"items": db.query(
        "SELECT * FROM answer_page WHERE exam_id=? ORDER BY page_no, id", (exam_id,)
    )}


@route("POST", r"/api/exams/(\d+)/answerkey")
def api_answerkey_upload(ctx, exam_id):
    _exam(exam_id)
    parts = [p for p in ctx.parts() if p.is_file]
    if not parts:
        raise ApiError("没有收到文件。")
    folder = _key_dir(exam_id)
    images, problems = [], []
    for part in parts:
        got, errs = _explode_upload(part, folder)
        images.extend(got)
        problems.extend(errs)
    if not images:
        raise ApiError("标准答案一张都没导进来。\n" + "\n".join(problems or ["文件格式不支持。"]))
    row = db.query_one(
        "SELECT COALESCE(MAX(page_no), 0) AS m FROM answer_page WHERE exam_id=?", (exam_id,)
    )
    page_no = (row["m"] or 0) + 1
    for img in images:
        db.execute(
            "INSERT INTO answer_page (exam_id, page_no, image_path, width, height) "
            "VALUES (?,?,?,?,?)",
            (exam_id, page_no, img["rel"], _int(img.get("width"), 0),
             _int(img.get("height"), 0)),
        )
        page_no += 1
    return {"count": len(images), "problems": problems, "items": db.query(
        "SELECT * FROM answer_page WHERE exam_id=? ORDER BY page_no, id", (exam_id,)
    )}


@route("DELETE", r"/api/answerkey/(\d+)")
def api_answerkey_delete(ctx, page_id):
    row = db.query_one("SELECT * FROM answer_page WHERE id=?", (page_id,))
    if not row:
        raise ApiError("这一页不存在。", 404)
    try:
        _rel_to_abs(row["image_path"]).unlink()
    except (OSError, ApiError):
        pass
    db.execute("DELETE FROM answer_page WHERE id=?", (page_id,))
    return {"deleted": True}


@route("POST", r"/api/answerkey/(\d+)/rotate")
def api_answerkey_rotate(ctx, page_id):
    row = db.query_one("SELECT * FROM answer_page WHERE id=?", (page_id,))
    if not row:
        raise ApiError("这一页不存在。", 404)
    rotate = (int(row["rotate"]) + _int(ctx.json().get("delta"), 90)) % 360
    if rotate not in (0, 90, 180, 270):
        rotate = 0
    db.execute("UPDATE answer_page SET rotate=? WHERE id=?", (rotate, page_id))
    return {"page_id": int(page_id), "rotate": rotate}


# --------------------------------------------------------------------------
# 考试
# --------------------------------------------------------------------------

def _exam_fields(body, base=None):
    base = base or {}
    return (
        str(body.get("name", base.get("name", ""))).strip(),
        str(body.get("subject", base.get("subject", ""))).strip(),
        str(body.get("klass", base.get("klass", ""))).strip(),
        str(body.get("exam_date", base.get("exam_date", ""))).strip(),
        to_float(body.get("full_score", base.get("full_score", 100)), 100),
        to_float(body.get("pass_score", base.get("pass_score", 60)), 60),
        to_float(body.get("excellent_score", base.get("excellent_score", 85)), 85),
        to_float(body.get("objective_full", base.get("objective_full", 0)), 0),
    )


@route("GET", r"/api/exams")
def api_exams(ctx):
    rows = db.query(
        "SELECT e.*, "
        "(SELECT COUNT(*) FROM student s WHERE s.exam_id=e.id) AS student_count, "
        "(SELECT COUNT(*) FROM question q WHERE q.exam_id=e.id) AS question_count, "
        "(SELECT COUNT(*) FROM paper p WHERE p.exam_id=e.id AND p.status='done') AS done_count "
        "FROM exam e ORDER BY COALESCE(NULLIF(e.exam_date,''), e.created_at) DESC, e.id DESC"
    )
    return {"items": rows}


@route("POST", r"/api/exams")
def api_create_exam(ctx):
    body = ctx.json()
    fields = _exam_fields(body)
    if not fields[0]:
        raise ApiError("考试名称不能为空。")
    now = db.now()
    eid = db.execute(
        "INSERT INTO exam (name, subject, klass, exam_date, full_score, pass_score, "
        "excellent_score, objective_full, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        fields + (now, now),
    )
    return _exam(eid)


@route("GET", r"/api/exams/(\d+)")
def api_exam_detail(ctx, exam_id):
    exam = _exam(exam_id)
    exam["questions"] = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )
    exam["students"] = db.query(
        "SELECT * FROM student WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )
    return exam


@route("PUT", r"/api/exams/(\d+)")
def api_update_exam(ctx, exam_id):
    exam = _exam(exam_id)
    body = ctx.json()
    fields = _exam_fields(body, exam)
    if not fields[0]:
        raise ApiError("考试名称不能为空。")
    db.execute(
        "UPDATE exam SET name=?, subject=?, klass=?, exam_date=?, full_score=?, "
        "pass_score=?, excellent_score=?, objective_full=?, updated_at=? WHERE id=?",
        fields + (db.now(), exam_id),
    )
    for p in db.query("SELECT id FROM paper WHERE exam_id=?", (exam_id,)):
        analysis.recalc_paper(p["id"])
    return _exam(exam_id)


@route("DELETE", r"/api/exams/(\d+)")
def api_delete_exam(ctx, exam_id):
    exam = _exam(exam_id)
    # 后端再确认一次，不能只靠前端弹窗（删的是老师一整场考试的成绩）
    if str(ctx.query.get("confirm", [""])[0]) != exam["name"]:
        raise ApiError("删除未确认：请把考试名称原样填进 confirm 参数。", 400)
    db.execute("DELETE FROM exam WHERE id=?", (exam_id,))
    folder = config.uploads_dir() / str(exam_id)
    if folder.exists():
        shutil.rmtree(str(folder), ignore_errors=True)
    return {"deleted": True}


# --------------------------------------------------------------------------
# 题卡（只有主观题）
# --------------------------------------------------------------------------

def _question_payload(item, sort_order):
    return (
        sort_order,
        str(item.get("no_label", "")).strip(),
        normalize_qtype(item.get("qtype", DEFAULT_QTYPE)),
        max(0.0, to_float(item.get("max_score"), 0)),
        str(item.get("stem", "")).strip(),
        str(item.get("answer_key", "")).strip(),
        str(item.get("rubric", "")).strip(),
        str(item.get("knowledge_point", "")).strip(),
    )


@route("GET", r"/api/exams/(\d+)/questions")
def api_questions(ctx, exam_id):
    _exam(exam_id)
    return {"items": db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )}


@route("POST", r"/api/exams/(\d+)/questions")
def api_add_questions(ctx, exam_id):
    _exam(exam_id)
    body = ctx.json()
    items = body.get("items")
    if items is None:
        items = [body]
    if not isinstance(items, list) or not items:
        raise ApiError("没有要添加的题目。")
    sort_order = _next_sort("question", exam_id)
    created = []
    for item in items:
        payload = _question_payload(item, sort_order)
        qid = db.execute(
            "INSERT INTO question (exam_id, sort_order, no_label, qtype, max_score, "
            "stem, answer_key, rubric, knowledge_point) VALUES (?,?,?,?,?,?,?,?,?)",
            (exam_id,) + payload,
        )
        created.append(qid)
        sort_order += 10
    for p in db.query("SELECT id FROM paper WHERE exam_id=?", (exam_id,)):
        analysis.recalc_paper(p["id"])
    return {"created": created, "items": db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )}


@route("PUT", r"/api/questions/(\d+)")
def api_update_question(ctx, question_id):
    q = db.query_one("SELECT * FROM question WHERE id=?", (question_id,))
    if not q:
        raise ApiError("这道题不存在。", 404)
    body = ctx.json()
    merged = dict(q)
    merged.update({k: v for k, v in body.items() if v is not None})
    payload = _question_payload(merged, q["sort_order"])
    db.execute(
        "UPDATE question SET sort_order=?, no_label=?, qtype=?, max_score=?, "
        "stem=?, answer_key=?, rubric=?, knowledge_point=? WHERE id=?",
        payload + (question_id,),
    )
    # 满分改小了，已经打的分要跟着夹回去
    new_max = payload[3]
    for s in db.query(
        "SELECT id, score FROM score WHERE question_id=? AND score IS NOT NULL", (question_id,)
    ):
        if to_float(s["score"]) > new_max:
            db.execute("UPDATE score SET score=?, updated_at=? WHERE id=?",
                       (round_score(new_max), db.now(), s["id"]))
    for p in db.query("SELECT id FROM paper WHERE exam_id=?", (q["exam_id"],)):
        analysis.recalc_paper(p["id"])
    return db.query_one("SELECT * FROM question WHERE id=?", (question_id,))


@route("DELETE", r"/api/questions/(\d+)")
def api_delete_question(ctx, question_id):
    q = db.query_one("SELECT * FROM question WHERE id=?", (question_id,))
    if not q:
        raise ApiError("这道题不存在。", 404)
    db.execute("DELETE FROM question WHERE id=?", (question_id,))
    for p in db.query("SELECT id FROM paper WHERE exam_id=?", (q["exam_id"],)):
        analysis.recalc_paper(p["id"])
    return {"deleted": True}


@route("POST", r"/api/exams/(\d+)/questions/reorder")
def api_reorder_questions(ctx, exam_id):
    _exam(exam_id)
    ids = ctx.json().get("ids") or []
    for i, qid in enumerate(ids):
        db.execute("UPDATE question SET sort_order=? WHERE id=? AND exam_id=?",
                   ((i + 1) * 10, _int(qid, 0), exam_id))
    return {"items": db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )}


@route("POST", r"/api/exams/(\d+)/questions/copy")
def api_copy_questions(ctx, exam_id):
    _exam(exam_id)
    src = _int(ctx.json().get("from_exam_id"))
    if not src:
        raise ApiError("没有选择要复制的考试。")
    rows = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (src,)
    )
    if not rows:
        raise ApiError("那场考试还没有题目可以复制。")
    sort_order = _next_sort("question", exam_id)
    for r in rows:
        db.execute(
            "INSERT INTO question (exam_id, sort_order, no_label, qtype, max_score, "
            "stem, answer_key, rubric, knowledge_point) VALUES (?,?,?,?,?,?,?,?,?)",
            (exam_id, sort_order, r["no_label"], r["qtype"], r["max_score"],
             r["stem"], r["answer_key"], r["rubric"], r["knowledge_point"]),
        )
        sort_order += 10
    return {"copied": len(rows), "items": db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id", (exam_id,)
    )}


# --------------------------------------------------------------------------
# 学生
# --------------------------------------------------------------------------

# 名单解析和姓名匹配都在 server/roster.py 里，那边是纯函数、好测
parse_roster = roster.parse_roster


def _roster_of(exam_id) -> list:
    return db.query(
        "SELECT id, name, student_no FROM student WHERE exam_id=? ORDER BY sort_order, id",
        (exam_id,),
    )


@route("GET", r"/api/exams/(\d+)/students")
def api_students(ctx, exam_id):
    _exam(exam_id)
    rows = db.query(
        "SELECT s.*, p.id AS paper_id, p.status, p.total_score, "
        "(SELECT COUNT(*) FROM page pg WHERE pg.paper_id=p.id) AS page_count "
        "FROM student s LEFT JOIN paper p ON p.student_id=s.id "
        "WHERE s.exam_id=? ORDER BY s.sort_order, s.id",
        (exam_id,),
    )
    return {"items": rows}


@route("POST", r"/api/exams/(\d+)/students")
def api_add_students(ctx, exam_id):
    _exam(exam_id)
    body = ctx.json()
    items = body.get("items")
    if items is None:
        items = parse_roster(body.get("text", ""))
    if not items:
        raise ApiError("没有解析出学生名单。一行写一个学生，可以是「学号 姓名」或只写姓名。")
    sort_order = _next_sort("student", exam_id)
    created = 0
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        db.execute(
            "INSERT INTO student (exam_id, sort_order, name, student_no, note) "
            "VALUES (?,?,?,?,?)",
            (exam_id, sort_order, name, str(item.get("student_no", "")).strip(),
             str(item.get("note", "")).strip()),
        )
        sort_order += 10
        created += 1
    if not created:
        raise ApiError("名单里一个有效姓名都没有。")
    return {"created": created}


@route("POST", r"/api/exams/(\d+)/students/copy")
def api_copy_students(ctx, exam_id):
    """从上一场考试复制名单 —— 班级名单一学期基本不变，这比每次重录靠谱得多。"""
    _exam(exam_id)
    src = _int(ctx.json().get("from_exam_id"))
    if not src:
        raise ApiError("没有选择要复制的考试。")
    rows = db.query(
        "SELECT name, student_no, note FROM student WHERE exam_id=? ORDER BY sort_order, id",
        (src,),
    )
    if not rows:
        raise ApiError("那场考试还没有学生名单可以复制。")

    # 已经有的学生不重复加：学号相同或姓名相同就跳过
    existing = _roster_of(exam_id)
    have_no = {roster.normalize_no(s["student_no"]) for s in existing
               if roster.normalize_no(s["student_no"])}
    have_name = {roster.normalize_name(s["name"]) for s in existing}

    sort_order = _next_sort("student", exam_id)
    created, skipped = 0, 0
    for r in rows:
        no = roster.normalize_no(r["student_no"])
        nm = roster.normalize_name(r["name"])
        if (no and no in have_no) or (nm and nm in have_name):
            skipped += 1
            continue
        db.execute(
            "INSERT INTO student (exam_id, sort_order, name, student_no, note) "
            "VALUES (?,?,?,?,?)",
            (exam_id, sort_order, r["name"], r["student_no"], r["note"]))
        if no:
            have_no.add(no)
        have_name.add(nm)
        sort_order += 10
        created += 1
    return {"created": created, "skipped": skipped}


@route("PUT", r"/api/students/(\d+)")
def api_update_student(ctx, student_id):
    stu = _student(student_id)
    body = ctx.json()
    name = str(body.get("name", stu["name"])).strip()
    if not name:
        raise ApiError("姓名不能为空。")
    db.execute(
        "UPDATE student SET name=?, student_no=?, note=? WHERE id=?",
        (name, str(body.get("student_no", stu["student_no"])).strip(),
         str(body.get("note", stu["note"])).strip(), student_id),
    )
    return _student(student_id)


@route("DELETE", r"/api/students/(\d+)")
def api_delete_student(ctx, student_id):
    stu = _student(student_id)
    paper = db.query_one("SELECT * FROM paper WHERE student_id=?", (student_id,))
    if paper:
        folder = config.uploads_dir() / str(stu["exam_id"]) / ("p%d" % paper["id"])
        if folder.exists():
            shutil.rmtree(str(folder), ignore_errors=True)
    db.execute("DELETE FROM student WHERE id=?", (student_id,))
    return {"deleted": True}


# --------------------------------------------------------------------------
# 答卷导入
# --------------------------------------------------------------------------

def _stage_dir(exam_id) -> Path:
    p = config.uploads_dir() / str(exam_id) / "_stage"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _paper_dir(exam_id, paper_id) -> Path:
    p = config.uploads_dir() / str(exam_id) / ("p%d" % int(paper_id))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / (stem + suffix)
    i = 1
    while candidate.exists():
        candidate = folder / ("%s_%d%s" % (stem, i, suffix))
        i += 1
    return candidate


def _safe_stem(filename: str) -> str:
    stem = Path(str(filename or "page")).stem
    stem = re.sub(r"[^\w一-鿿\-]", "_", stem)[:40]
    return stem or "page"


def _explode_upload(part, folder: Path) -> tuple:
    """把一个上传的文件展开成若干张图片，返回 (图片信息列表, 出错说明列表)。"""
    data = part.data
    if not data:
        return [], ["「%s」是空文件。" % part.filename]
    kind = imgutil.sniff_ext(data)
    stem = _safe_stem(part.filename)

    if kind == ".pdf":
        try:
            pages = pdfimg.extract_pages(data, folder, stem)
        except pdfimg.PdfExtractError as exc:
            return [], ["「%s」：%s" % (part.filename, exc)]
        out = []
        for pg in pages:
            out.append({"rel": _abs_to_rel(pg["path"]), "width": pg["width"],
                        "height": pg["height"], "source": part.filename})
        return out, []

    if kind in IMAGE_EXTS:
        path = _unique_path(folder, stem, kind)
        path.write_bytes(data)
        w, h = imgutil.image_size(path)
        return [{"rel": _abs_to_rel(path), "width": w, "height": h,
                 "source": part.filename}], []

    return [], ["「%s」不是图片也不是 PDF，跳过了。" % (part.filename or "未命名文件")]


@route("POST", r"/api/exams/(\d+)/upload")
def api_upload(ctx, exam_id):
    _exam(exam_id)
    parts = [p for p in ctx.parts() if p.is_file]
    if not parts:
        raise ApiError("没有收到文件。")
    student_id = _int(ctx.query.get("student_id", [None])[0])

    if student_id:
        stu = _student(student_id)
        if str(stu["exam_id"]) != str(exam_id):
            raise ApiError("这个学生不属于这场考试。")
        paper = _ensure_paper(exam_id, student_id)
        folder = _paper_dir(exam_id, paper["id"])
    else:
        folder = _stage_dir(exam_id)

    images, problems = [], []
    for part in parts:
        got, errs = _explode_upload(part, folder)
        images.extend(got)
        problems.extend(errs)

    if student_id and images:
        _append_pages(_ensure_paper(exam_id, student_id)["id"], images)

    if not images and problems:
        raise ApiError("一张都没导进来。\n" + "\n".join(problems))
    return {"images": images, "problems": problems,
            "bound": bool(student_id), "count": len(images)}


def _append_pages(paper_id, images) -> None:
    row = db.query_one(
        "SELECT COALESCE(MAX(page_no), 0) AS m FROM page WHERE paper_id=?", (paper_id,)
    )
    page_no = (row["m"] or 0) + 1
    for img in images:
        db.execute(
            "INSERT INTO page (paper_id, page_no, image_path, width, height) "
            "VALUES (?,?,?,?,?)",
            (paper_id, page_no, img["rel"], _int(img.get("width"), 0),
             _int(img.get("height"), 0)),
        )
        page_no += 1


@route("GET", r"/api/exams/(\d+)/stage")
def api_stage_list(ctx, exam_id):
    _exam(exam_id)
    folder = _stage_dir(exam_id)
    items = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            w, h = imgutil.image_size(path)
            items.append({"rel": _abs_to_rel(path), "name": path.name,
                          "width": w, "height": h})
    return {"items": items}


IDENTIFY_BATCH = 8  # 一次最多送几页给模型，太多容易超时也容易串页


@route("POST", r"/api/exams/(\d+)/stage/identify")
def api_identify(ctx, exam_id):
    """让 AI 读暂存区每一页的姓名学号，并对到已有名单上。

    body: {"pages": [{"rel": "...", "image": "data:image/jpeg;base64,..."}]}
    返回按学生分好组的草稿，**前端必须让老师确认后再调 /bind**，这里不写任何数据。
    """
    _exam(exam_id)
    items = ctx.json().get("pages") or []
    if not items:
        raise ApiError("没有要识别的页面。")

    ordered = [it for it in items if it.get("rel")]
    read = []
    try:
        for start in range(0, len(ordered), IDENTIFY_BATCH):
            chunk = ordered[start:start + IDENTIFY_BATCH]
            result = ai.read_identity([it.get("image") for it in chunk])
            for i, page in enumerate(result["pages"]):
                if i >= len(chunk):
                    break
                read.append({
                    "rel": chunk[i]["rel"],
                    "name": page.get("name") or "",
                    "student_no": page.get("student_no") or "",
                    "has_header": page.get("has_header", True),
                })
    except ai.AIError as exc:
        raise ApiError(str(exc), 400)

    groups = roster.group_pages(read)
    current = _roster_of(exam_id)
    for g in groups:
        m = roster.match_student(g["name"], g["student_no"], current)
        g.update(m)
    return {"groups": groups, "pages": read, "roster": current}


@route("POST", r"/api/exams/(\d+)/bind")
def api_bind(ctx, exam_id):
    """把暂存区的页面绑到学生名下。老师在「确认姓名」界面点确认之后才会走到这里。

    body: {"assignments": [
        {"student_id": 1, "rels": [...]},                       # 绑到已有学生
        {"name": "张三", "student_no": "01", "rels": [...]}      # 新建学生再绑
    ]}
    """
    _exam(exam_id)
    assignments = ctx.json().get("assignments") or []
    if not assignments:
        raise ApiError("没有要绑定的页面。")
    stage = _stage_dir(exam_id).resolve()
    bound, created = 0, 0
    for item in assignments:
        student_id = _int(item.get("student_id"))
        rels = item.get("rels") or []
        if not rels:
            continue
        if not student_id:
            # 名单里没有这个人，老师确认后在这里新建
            name = str(item.get("name") or "").strip()
            if not name:
                raise ApiError("有答卷没有指定学生：请在「确认姓名」那一步把姓名填上，"
                               "或者选一个已有的学生。")
            student_id = db.execute(
                "INSERT INTO student (exam_id, sort_order, name, student_no) "
                "VALUES (?,?,?,?)",
                (exam_id, _next_sort("student", exam_id), name,
                 str(item.get("student_no") or "").strip()))
            created += 1
        stu = _student(student_id)
        if str(stu["exam_id"]) != str(exam_id):
            raise ApiError("学生「%s」不属于这场考试。" % stu["name"])
        paper = _ensure_paper(exam_id, student_id)
        folder = _paper_dir(exam_id, paper["id"])
        images = []
        for rel in rels:
            src = _rel_to_abs(rel)
            try:
                src.resolve().relative_to(stage)
            except ValueError:
                raise ApiError("只能绑定暂存区里的页面。")
            if not src.exists():
                continue
            dst = _unique_path(folder, src.stem, src.suffix)
            src.replace(dst)
            w, h = imgutil.image_size(dst)
            images.append({"rel": _abs_to_rel(dst), "width": w, "height": h})
        if images:
            _append_pages(paper["id"], images)
            bound += len(images)
    return {"bound": bound, "created_students": created}


@route("POST", r"/api/exams/(\d+)/stage/clear")
def api_stage_clear(ctx, exam_id):
    _exam(exam_id)
    folder = _stage_dir(exam_id)
    removed = 0
    for path in list(folder.iterdir()):
        if path.is_file():
            path.unlink()
            removed += 1
    return {"removed": removed}


# --------------------------------------------------------------------------
# 批改
# --------------------------------------------------------------------------

def _paper_detail(paper_id) -> dict:
    paper = _paper(paper_id)
    exam = _exam(paper["exam_id"])
    student = _student(paper["student_id"])
    questions = db.query(
        "SELECT * FROM question WHERE exam_id=? ORDER BY sort_order, id",
        (paper["exam_id"],),
    )
    scores = {
        s["question_id"]: s
        for s in db.query("SELECT * FROM score WHERE paper_id=?", (paper_id,))
    }
    for q in questions:
        s = scores.get(q["id"]) or {}
        q["qtype_label"] = QTYPE_LABELS.get(q["qtype"], q["qtype"])
        q["score"] = s.get("score")
        q["student_answer"] = s.get("student_answer", "")
        q["ai_suggested_score"] = s.get("ai_suggested_score")
        q["ai_comment"] = s.get("ai_comment", "")
        q["ai_accepted"] = s.get("ai_accepted", 0)
        q["comment"] = s.get("comment", "")
    pages = db.query(
        "SELECT p.*, a.data_json FROM page p "
        "LEFT JOIN annotation a ON a.page_id=p.id "
        "WHERE p.paper_id=? ORDER BY p.page_no, p.id",
        (paper_id,),
    )
    answer_pages = db.query(
        "SELECT * FROM answer_page WHERE exam_id=? ORDER BY page_no, id",
        (paper["exam_id"],),
    )
    return {"paper": paper, "exam": exam, "student": student,
            "questions": questions, "pages": pages, "answer_pages": answer_pages,
            "ai_ready": ai.is_ready(), "ai_vision": ai.is_vision()}


@route("GET", r"/api/papers/(\d+)")
def api_paper_detail(ctx, paper_id):
    return _paper_detail(paper_id)


@route("GET", r"/api/students/(\d+)/paper")
def api_student_paper(ctx, student_id):
    stu = _student(student_id)
    paper = _ensure_paper(stu["exam_id"], student_id)
    return _paper_detail(paper["id"])


def _upsert_score(paper_id, question_id, **fields):
    row = db.query_one(
        "SELECT * FROM score WHERE paper_id=? AND question_id=?", (paper_id, question_id)
    )
    if not row:
        db.execute(
            "INSERT INTO score (paper_id, question_id, updated_at) VALUES (?,?,?)",
            (paper_id, question_id, db.now()),
        )
        row = db.query_one(
            "SELECT * FROM score WHERE paper_id=? AND question_id=?",
            (paper_id, question_id),
        )
    if not fields:
        return row
    sets, args = [], []
    for key, value in fields.items():
        sets.append("%s=?" % key)
        args.append(value)
    sets.append("updated_at=?")
    args.append(db.now())
    args.extend([paper_id, question_id])
    db.execute("UPDATE score SET %s WHERE paper_id=? AND question_id=?"
               % ", ".join(sets), tuple(args))
    return db.query_one(
        "SELECT * FROM score WHERE paper_id=? AND question_id=?", (paper_id, question_id)
    )


@route("POST", r"/api/scores")
def api_set_score(ctx):
    body = ctx.json()
    paper_id = _int(body.get("paper_id"))
    question_id = _int(body.get("question_id"))
    if not paper_id or not question_id:
        raise ApiError("缺少答卷或题目编号。")
    paper = _paper(paper_id)
    question = db.query_one("SELECT * FROM question WHERE id=?", (question_id,))
    if not question or question["exam_id"] != paper["exam_id"]:
        raise ApiError("这道题不属于这场考试。")

    fields = {}
    if "score" in body:
        fields["score"] = clamp_score(body.get("score"), question["max_score"])
    if "student_answer" in body:
        fields["student_answer"] = str(body.get("student_answer") or "")
    if "comment" in body:
        fields["comment"] = str(body.get("comment") or "")
    if body.get("ai_accepted"):
        fields["ai_accepted"] = 1
    if not fields:
        raise ApiError("没有要保存的内容。")

    _upsert_score(paper_id, question_id, **fields)
    return {"score": db.query_one(
        "SELECT * FROM score WHERE paper_id=? AND question_id=?", (paper_id, question_id)
    ), "recalc": analysis.recalc_paper(paper_id)}


@route("POST", r"/api/papers/(\d+)/meta")
def api_paper_meta(ctx, paper_id):
    paper = _paper(paper_id)
    exam = _exam(paper["exam_id"])
    body = ctx.json()
    sets, args = [], []
    if "objective_score" in body:
        value = clamp_score(body.get("objective_score"), exam["objective_full"])
        sets.append("objective_score=?")
        args.append(value if value is not None else 0)
    if "comment" in body:
        sets.append("comment=?")
        args.append(str(body.get("comment") or ""))
    if not sets:
        raise ApiError("没有要保存的内容。")
    sets.append("updated_at=?")
    args.append(db.now())
    args.append(paper_id)
    db.execute("UPDATE paper SET %s WHERE id=?" % ", ".join(sets), tuple(args))
    return {"paper": _paper(paper_id), "recalc": analysis.recalc_paper(paper_id)}


@route("POST", r"/api/pages/(\d+)/rotate")
def api_rotate_page(ctx, page_id):
    page = _page(page_id)
    delta = _int(ctx.json().get("delta"), 90)
    rotate = (int(page["rotate"]) + delta) % 360
    if rotate not in (0, 90, 180, 270):
        rotate = 0
    db.execute("UPDATE page SET rotate=? WHERE id=?", (rotate, page_id))
    return {"page_id": int(page_id), "rotate": rotate}


@route("POST", r"/api/pages/(\d+)/annotation")
def api_save_annotation(ctx, page_id):
    _page(page_id)
    data = ctx.json().get("data")
    text = json.dumps(data, ensure_ascii=False) if data is not None else ""
    # 不用 UPSERT：老师机器上的 SQLite 可能老于 3.24，不支持 ON CONFLICT DO UPDATE
    existing = db.query_one("SELECT page_id FROM annotation WHERE page_id=?", (page_id,))
    if existing:
        db.execute("UPDATE annotation SET data_json=?, updated_at=? WHERE page_id=?",
                   (text, db.now(), page_id))
    else:
        db.execute("INSERT INTO annotation (page_id, data_json, updated_at) VALUES (?,?,?)",
                   (page_id, text, db.now()))
    return {"saved": True}


@route("POST", r"/api/pages/(\d+)/annotated")
def api_save_annotated_image(ctx, page_id):
    """前端把 Canvas 合成好的成品图（dataURL）发过来存盘，供导出用。"""
    page = _page(page_id)
    paper = _paper(page["paper_id"])
    data_url = str(ctx.json().get("image") or "")
    m = re.match(r"^data:image/(png|jpeg);base64,(.+)$", data_url, re.S)
    if not m:
        raise ApiError("图片数据不对。")
    try:
        raw = b64decode(m.group(2))
    except Exception:
        raise ApiError("图片数据解不开。")
    folder = _paper_dir(paper["exam_id"], paper["id"])
    suffix = ".png" if m.group(1) == "png" else ".jpg"
    path = folder / ("p%03d_marked%s" % (int(page["page_no"]), suffix))
    path.write_bytes(raw)
    db.execute("UPDATE page SET annotated_path=? WHERE id=?",
               (_abs_to_rel(path), page_id))
    return {"saved": True, "path": _abs_to_rel(path)}


@route("DELETE", r"/api/pages/(\d+)")
def api_delete_page(ctx, page_id):
    page = _page(page_id)
    for key in ("image_path", "annotated_path"):
        if page[key]:
            try:
                _rel_to_abs(page[key]).unlink()
            except (OSError, ApiError):
                pass
    db.execute("DELETE FROM page WHERE id=?", (page_id,))
    return {"deleted": True}


# --------------------------------------------------------------------------
# 报表与导出
# --------------------------------------------------------------------------

@route("GET", r"/api/exams/(\d+)/report")
def api_report(ctx, exam_id):
    _exam(exam_id)
    return analysis.full_report(exam_id)


@route("GET", r"/api/students/(\d+)/report")
def api_student_report(ctx, student_id):
    """个人报告：总分、名次、逐题得分与班平均对照、错题、评语。"""
    stu = _student(student_id)
    return analysis.student_report(stu["exam_id"], int(student_id))


@route("GET", r"/api/exams/(\d+)/wrong")
def api_wrong(ctx, exam_id):
    _exam(exam_id)
    threshold = to_float(ctx.query.get("threshold", ["60"])[0], 60)
    return {"items": analysis.wrong_list(exam_id, threshold)}


def _score_rows(exam_id):
    table = analysis.score_table(exam_id)
    exam = table["exam"]
    questions = table["questions"]
    header = ["学号", "姓名"]
    for q in questions:
        header.append("%s(%g分)" % (q["no_label"] or "题", to_float(q["max_score"])))
    if to_float(exam["objective_full"]) > 0:
        header.append("客观题(%g分)" % to_float(exam["objective_full"]))
    header += ["总分", "名次", "状态", "评语"]

    status_label = {"todo": "未批", "doing": "批改中", "done": "已批完"}
    rows = [header]
    for r in table["rows"]:
        row = [r["student_no"], r["name"]]
        for value in r["scores"]:
            row.append("" if value is None else round_score(value))
        if to_float(exam["objective_full"]) > 0:
            row.append(round_score(r["objective_score"]))
        row += [round_score(r["total_score"]), r["rank"] or "",
                status_label.get(r["status"], r["status"]), r["comment"]]
        rows.append(row)
    return exam, rows


def _download_name(exam, suffix) -> str:
    base = "%s_%s" % (exam["klass"] or "", exam["name"] or "考试")
    base = re.sub(r"[\\/:*?\"<>|]", "_", base).strip("_")
    return (base or "成绩") + suffix


@route("GET", r"/api/exams/(\d+)/export/scores\.xlsx")
def api_export_scores_xlsx(ctx, exam_id):
    _exam(exam_id)
    exam, rows = _score_rows(exam_id)
    stats = analysis.class_stats(exam_id)
    stat_rows = [
        ["项目", "数值"],
        ["考试", exam["name"]],
        ["班级", exam["klass"]],
        ["科目", exam["subject"]],
        ["考试日期", exam["exam_date"]],
        ["满分", to_float(exam["full_score"])],
        ["应考人数", stats.get("total_students", 0)],
        ["已批完人数", stats.get("graded_count", 0)],
        ["平均分", stats.get("mean", 0)],
        ["最高分", stats.get("max", 0)],
        ["最低分", stats.get("min", 0)],
        ["中位数", stats.get("median", 0)],
        ["标准差", stats.get("stdev", 0)],
        ["及格线", stats.get("pass_line", 0)],
        ["及格率(%)", stats.get("pass_rate", 0)],
        ["优秀线", stats.get("good_line", 0)],
        ["优秀率(%)", stats.get("good_rate", 0)],
        [],
        ["分数段", "人数", "占比(%)"],
    ]
    for b in stats.get("buckets", []):
        stat_rows.append([b["label"], b["count"], b["rate"]])

    qrows = [["题号", "题型", "知识点", "满分", "平均分", "得分率(%)", "满分人数", "零分人数"]]
    for q in analysis.question_stats(exam_id):
        qrows.append([q["no_label"], q["qtype_label"], q["knowledge_point"],
                      q["max_score"], q["mean"], q["rate"],
                      q["full_count"], q["zero_count"]])

    data = xlsx.build_xlsx([
        {"name": "成绩总表", "rows": rows, "header_rows": 1,
         "widths": [12, 10] + [8] * (len(rows[0]) - 2)},
        {"name": "班级分析", "rows": stat_rows, "header_rows": 1, "widths": [16, 12, 10]},
        {"name": "题目分析", "rows": qrows, "header_rows": 1,
         "widths": [10, 10, 16, 8, 8, 10, 10, 10]},
    ])
    return FileResponse(data,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        _download_name(exam, "_成绩表.xlsx"))


@route("GET", r"/api/exams/(\d+)/export/scores\.csv")
def api_export_scores_csv(ctx, exam_id):
    _exam(exam_id)
    exam, rows = _score_rows(exam_id)
    return FileResponse(xlsx.to_csv_bytes(rows), "text/csv; charset=utf-8",
                        _download_name(exam, "_成绩表.csv"))


@route("GET", r"/api/exams/(\d+)/export/wrong\.xlsx")
def api_export_wrong(ctx, exam_id):
    exam = _exam(exam_id)
    threshold = to_float(ctx.query.get("threshold", ["60"])[0], 60)
    rows = [["学号", "姓名", "总分", "名次", "题号", "知识点", "满分", "得分", "扣分"]]
    for stu in analysis.wrong_list(exam_id, threshold):
        for item in stu["items"]:
            rows.append([stu["student_no"], stu["name"], stu["total_score"],
                         stu["rank"] or "", item["no_label"], item["knowledge_point"],
                         item["max_score"], item["score"], item["lost"]])
    data = xlsx.build_xlsx([{"name": "错题清单", "rows": rows, "header_rows": 1,
                             "widths": [12, 10, 8, 8, 10, 16, 8, 8, 8]}])
    return FileResponse(data,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        _download_name(exam, "_错题清单.xlsx"))


@route("GET", r"/api/papers/(\d+)/export/marked\.zip")
def api_export_marked(ctx, paper_id):
    paper = _paper(paper_id)
    student = _student(paper["student_id"])
    exam = _exam(paper["exam_id"])
    pages = db.query("SELECT * FROM page WHERE paper_id=? ORDER BY page_no, id",
                     (paper_id,))
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for pg in pages:
            rel = pg["annotated_path"] or pg["image_path"]
            try:
                path = _rel_to_abs(rel)
            except ApiError:
                continue
            if not path.exists():
                continue
            name = "%s_第%d页%s" % (student["name"], pg["page_no"], path.suffix)
            z.writestr(name, path.read_bytes())
            count += 1
    if not count:
        raise ApiError("这个学生还没有导入答卷页面。")
    filename = re.sub(r"[\\/:*?\"<>|]", "_",
                      "%s_%s_批注卷.zip" % (exam["name"], student["name"]))
    return FileResponse(buf.getvalue(), "application/zip", filename)


def _temp_file(prefix, suffix) -> Path:
    """在系统临时目录建一个空文件并返回路径（大 zip 不走内存，落盘流式发）。"""
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(name)


def _safe_folder(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", str(name)).strip() or "未命名"


# 图片本身已经压缩过，deflate 只是白费时间
_STORE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".zip"}


@route("GET", r"/api/exams/(\d+)/export/marked\.zip")
def api_export_marked_all(ctx, exam_id):
    """全班批注卷一键打包：一人一个文件夹，有批注图用批注图，没有就用原图。"""
    exam = _exam(exam_id)
    students = db.query(
        "SELECT * FROM student WHERE exam_id=? ORDER BY sort_order, id", (exam_id,))
    papers = db.query("SELECT * FROM paper WHERE exam_id=?", (exam_id,))
    paper_by_student = {p["student_id"]: p for p in papers}

    tmp = _temp_file("pjpg_marked_", ".zip")
    count = 0
    used = set()
    try:
        with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_STORED) as z:
            for i, stu in enumerate(students):
                paper = paper_by_student.get(stu["id"])
                if not paper:
                    continue
                pages = db.query(
                    "SELECT * FROM page WHERE paper_id=? ORDER BY page_no, id",
                    (paper["id"],))
                if not pages:
                    continue
                prefix = str(stu["student_no"] or "").strip() or ("%02d" % (i + 1))
                folder = _safe_folder("%s_%s" % (prefix, stu["name"]))
                if folder in used:
                    folder = "%s_%d" % (folder, stu["id"])
                used.add(folder)
                for pg in pages:
                    rel = pg["annotated_path"] or pg["image_path"]
                    try:
                        path = _rel_to_abs(rel)
                    except ApiError:
                        continue
                    if not path.exists():
                        continue
                    z.write(str(path),
                            "%s/第%d页%s" % (folder, pg["page_no"], path.suffix))
                    count += 1
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if not count:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ApiError("还没有任何学生导入答卷，没有可以打包的图片。")
    return FileResponse(None, "application/zip",
                        _download_name(exam, "_全班批注卷.zip"),
                        path=tmp, cleanup=True)


# --------------------------------------------------------------------------
# 数据备份
# --------------------------------------------------------------------------

@route("GET", r"/api/backup/data\.zip")
def api_backup_data(ctx):
    """把整个 data/ 打包下载 —— 成绩库、答卷图、批注、密钥都在里面。

    - 数据库不直接拷文件（开着 WAL，可能拷到写了一半的），
      用 SQLite 在线备份接口抓一致快照放进包里，`-wal` / `-shm` 不进包
    - `update.sh` 存的旧代码备份（data/_backup_*/）不是老师的数据，不进包
    - 包内保留 data/ 目录结构：恢复 = 解压 → 替换 data/ → 重启
    """
    base = config.data_dir().resolve()
    db_name = config.db_path().name
    skip_names = {db_name, db_name + "-wal", db_name + "-shm"}

    tmp = _temp_file("pjpg_backup_", ".zip")
    snap = _temp_file("pjpg_snap_", ".db")
    try:
        db.snapshot_to(snap)
        with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_DEFLATED) as z:
            z.write(str(snap), "data/" + db_name)
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(base).as_posix()
                if rel.split("/")[0].startswith("_backup"):
                    continue
                if path.name in skip_names:
                    continue
                compress = (zipfile.ZIP_STORED
                            if path.suffix.lower() in _STORE_EXTS
                            else zipfile.ZIP_DEFLATED)
                z.write(str(path), "data/" + rel, compress_type=compress)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            snap.unlink()
        except OSError:
            pass
    filename = "试卷批改系统备份_%s.zip" % datetime.now().strftime("%Y%m%d_%H%M")
    return FileResponse(None, "application/zip", filename,
                        path=tmp, cleanup=True)


# --------------------------------------------------------------------------
# 答卷图片
# --------------------------------------------------------------------------

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


@route("GET", r"/files/(.+)")
def api_file(ctx, rel):
    path = _rel_to_abs(rel)
    if not path.exists() or not path.is_file():
        raise ApiError("图片不存在，可能已经被删掉了。", 404)
    ctype = _MIME.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path.read_bytes(), ctype, inline=True, max_age=3600)
