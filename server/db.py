# -*- coding: utf-8 -*-
"""SQLite 建表与连接管理。

用线程本地连接（HTTP server 是多线程的），开 WAL 提升并发读写表现。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS exam (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    klass           TEXT NOT NULL DEFAULT '',
    exam_date       TEXT NOT NULL DEFAULT '',
    full_score      REAL NOT NULL DEFAULT 100,
    pass_score      REAL NOT NULL DEFAULT 60,
    excellent_score REAL NOT NULL DEFAULT 85,
    objective_full  REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS question (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id         INTEGER NOT NULL REFERENCES exam(id) ON DELETE CASCADE,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    no_label        TEXT NOT NULL DEFAULT '',
    qtype           TEXT NOT NULL DEFAULT 'essay',
    max_score       REAL NOT NULL DEFAULT 0,
    stem            TEXT NOT NULL DEFAULT '',
    -- 参考答案（主观题也有标准答案）。判分标准是「意思相近即可得分」，不要求字面一致
    answer_key      TEXT NOT NULL DEFAULT '',
    rubric          TEXT NOT NULL DEFAULT '',
    knowledge_point TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_question_exam ON question(exam_id, sort_order);

CREATE TABLE IF NOT EXISTS student (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id     INTEGER NOT NULL REFERENCES exam(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    name        TEXT NOT NULL,
    student_no  TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_student_exam ON student(exam_id, sort_order);

CREATE TABLE IF NOT EXISTS paper (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id     INTEGER NOT NULL REFERENCES exam(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'todo',
    total_score REAL NOT NULL DEFAULT 0,
    -- 客观题（选择/判断）不在本系统里逐题批，老师想算总分就在这里填一个合计分
    objective_score REAL NOT NULL DEFAULT 0,
    comment     TEXT NOT NULL DEFAULT '',
    graded_at   TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT '',
    UNIQUE(exam_id, student_id)
);

CREATE TABLE IF NOT EXISTS page (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id       INTEGER NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    page_no        INTEGER NOT NULL DEFAULT 1,
    image_path     TEXT NOT NULL,
    width          INTEGER NOT NULL DEFAULT 0,
    height         INTEGER NOT NULL DEFAULT 0,
    rotate         INTEGER NOT NULL DEFAULT 0,
    annotated_path TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_page_paper ON page(paper_id, page_no);

CREATE TABLE IF NOT EXISTS score (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id           INTEGER NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    question_id        INTEGER NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    score              REAL,
    -- 老师粘贴的学生作答文本，只给 AI 出建议分用；不填也能正常人工批
    student_answer     TEXT NOT NULL DEFAULT '',
    ai_suggested_score REAL,
    ai_comment         TEXT NOT NULL DEFAULT '',
    ai_accepted        INTEGER NOT NULL DEFAULT 0,
    comment            TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    UNIQUE(paper_id, question_id)
);

-- 标准答案卷：老师上传的参考答案扫描件/照片，整场考试共用一份
CREATE TABLE IF NOT EXISTS answer_page (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id    INTEGER NOT NULL REFERENCES exam(id) ON DELETE CASCADE,
    page_no    INTEGER NOT NULL DEFAULT 1,
    image_path TEXT NOT NULL,
    width      INTEGER NOT NULL DEFAULT 0,
    height     INTEGER NOT NULL DEFAULT 0,
    rotate     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_answer_page_exam ON answer_page(exam_id, page_no);

CREATE TABLE IF NOT EXISTS annotation (
    page_id    INTEGER PRIMARY KEY REFERENCES page(id) ON DELETE CASCADE,
    data_json  TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    """取当前线程的连接，没有就建一个。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


# 后续版本新增的列写在这里，老师那边升级时自动补上。
# 格式：表名 -> [(列名, 建列的 SQL 片段), ...]
# 只能往后加列、不能改列型不能删列 —— 老师机器上的库有真实数据，不许迁移丢数据。
ADDED_COLUMNS = {
    # 例：  "question": [("difficulty", "TEXT NOT NULL DEFAULT ''")],
}


def _migrate(conn: sqlite3.Connection) -> None:
    """老版本的库缺列就补上。这是老师那边能一键更新的前提。"""
    for table, columns in ADDED_COLUMNS.items():
        cur = conn.execute("PRAGMA table_info(%s)" % table)
        existing = {row[1] for row in cur.fetchall()}
        cur.close()
        if not existing:
            continue  # 表还不存在，SCHEMA 里已经建好了
        for name, decl in columns:
            if name not in existing:
                conn.execute(
                    "ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl)
                )


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def snapshot_to(path) -> None:
    """把当前数据库完整快照到 path。

    库开着 WAL，直接拷 .db 文件可能拷到写了一半的；
    SQLite 自带的在线备份接口（3.7 就有）能在使用中拿到一致副本。
    """
    dest = sqlite3.connect(str(path))
    try:
        connect().backup(dest)
        dest.commit()
    finally:
        dest.close()


def query(sql: str, args=()) -> list:
    cur = connect().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def query_one(sql: str, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    """执行写操作，返回 lastrowid。"""
    conn = connect()
    cur = conn.execute(sql, args)
    conn.commit()
    rowid = cur.lastrowid
    cur.close()
    return rowid


def execute_many(sql: str, seq) -> None:
    conn = connect()
    conn.executemany(sql, seq)
    conn.commit()
