# -*- coding: utf-8 -*-
"""成绩统计。算错分是这个系统最严重的错误，这里测得细一点。"""
from __future__ import annotations

import unittest

from helper import TempDataCase
from server import analysis, db


class TestRecalcPaper(TempDataCase):
    def test_总分等于主观题加客观题(self):
        eid = self.make_exam()
        q1 = self.make_question(eid, 10, "三、1", max_score=12)
        q2 = self.make_question(eid, 20, "四", "composition", 60)
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid, objective=40)
        self.set_score(pid, q1, 9)
        self.set_score(pid, q2, 44)
        r = analysis.recalc_paper(pid)
        self.assertEqual(r["total_score"], 93.0)
        self.assertEqual(r["subjective_score"], 53.0)
        self.assertEqual(r["status"], "done")

    def test_只批了一半是批改中(self):
        eid = self.make_exam()
        q1 = self.make_question(eid, 10)
        self.make_question(eid, 20, "三、2")
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.set_score(pid, q1, 8)
        r = analysis.recalc_paper(pid)
        self.assertEqual(r["status"], "doing")
        self.assertEqual(r["graded_count"], 1)
        self.assertEqual(r["question_count"], 2)

    def test_一题没批是未批(self):
        eid = self.make_exam()
        self.make_question(eid)
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.assertEqual(analysis.recalc_paper(pid)["status"], "todo")

    def test_零分也算批过了(self):
        eid = self.make_exam()
        q = self.make_question(eid)
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.set_score(pid, q, 0)
        self.assertEqual(analysis.recalc_paper(pid)["status"], "done")

    def test_批完再改回未批要清掉批改时间(self):
        eid = self.make_exam()
        q = self.make_question(eid)
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.set_score(pid, q, 10)
        analysis.recalc_paper(pid)
        self.assertTrue(db.query_one("SELECT graded_at FROM paper WHERE id=?", (pid,))["graded_at"])
        db.execute("UPDATE score SET score=NULL WHERE paper_id=?", (pid,))
        analysis.recalc_paper(pid)
        row = db.query_one("SELECT status, graded_at FROM paper WHERE id=?", (pid,))
        self.assertEqual(row["status"], "todo")
        self.assertEqual(row["graded_at"], "")


class TestClassStats(TempDataCase):
    def _setup_class(self):
        eid = self.make_exam(full_score=100, pass_score=60, excellent_score=85,
                             objective_full=0)
        q = self.make_question(eid, 10, "一", max_score=100)
        totals = [95, 88, 72, 60, 45]
        for i, total in enumerate(totals):
            sid = self.make_student(eid, "学生%d" % i, "%02d" % i, (i + 1) * 10)
            pid = self.make_paper(eid, sid)
            self.set_score(pid, q, total)
            analysis.recalc_paper(pid)
        # 再加一个没批完的，不该进统计
        sid = self.make_student(eid, "没批的", "99", 999)
        self.make_paper(eid, sid)
        return eid

    def test_只统计批完的(self):
        eid = self._setup_class()
        st = analysis.class_stats(eid)
        self.assertEqual(st["total_students"], 6)
        self.assertEqual(st["graded_count"], 5)
        self.assertEqual(st["ungraded_count"], 1)

    def test_平均最高最低中位(self):
        eid = self._setup_class()
        st = analysis.class_stats(eid)
        self.assertEqual(st["mean"], 72.0)      # (95+88+72+60+45)/5
        self.assertEqual(st["max"], 95.0)
        self.assertEqual(st["min"], 45.0)
        self.assertEqual(st["median"], 72.0)

    def test_及格率优秀率(self):
        eid = self._setup_class()
        st = analysis.class_stats(eid)
        self.assertEqual(st["pass_count"], 4)   # >=60 的有 95 88 72 60
        self.assertEqual(st["pass_rate"], 80.0)
        self.assertEqual(st["good_count"], 2)   # >=85 的有 95 88
        self.assertEqual(st["good_rate"], 40.0)

    def test_分数段十档且总人数对得上(self):
        eid = self._setup_class()
        st = analysis.class_stats(eid)
        self.assertEqual(len(st["buckets"]), 10)
        self.assertEqual(sum(b["count"] for b in st["buckets"]), 5)

    def test_满分正好落在最后一档(self):
        eid = self.make_exam(full_score=100, objective_full=0)
        q = self.make_question(eid, 10, "一", max_score=100)
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.set_score(pid, q, 100)
        analysis.recalc_paper(pid)
        st = analysis.class_stats(eid)
        self.assertEqual(st["buckets"][-1]["count"], 1)

    def test_没人批完也不炸(self):
        eid = self.make_exam()
        self.make_question(eid)
        self.make_student(eid)
        st = analysis.class_stats(eid)
        self.assertEqual(st["graded_count"], 0)
        self.assertEqual(st["mean"], 0.0)
        self.assertEqual(st["pass_rate"], 0.0)


class TestRanking(TempDataCase):
    def test_同分同名次且下一名跳号(self):
        eid = self.make_exam(objective_full=0)
        q = self.make_question(eid, 10, "一", max_score=100)
        for i, total in enumerate([90, 80, 80, 70]):
            sid = self.make_student(eid, "学生%d" % i, "%02d" % i, (i + 1) * 10)
            pid = self.make_paper(eid, sid)
            self.set_score(pid, q, total)
            analysis.recalc_paper(pid)
        ranks = [r["rank"] for r in analysis.score_table(eid)["rows"]]
        self.assertEqual(ranks, [1, 2, 2, 4])


class TestQuestionStats(TempDataCase):
    def test_按得分率从低到高排(self):
        eid = self.make_exam(objective_full=0)
        easy = self.make_question(eid, 10, "易", max_score=10, knowledge="甲")
        hard = self.make_question(eid, 20, "难", max_score=10, knowledge="乙")
        for i in range(2):
            sid = self.make_student(eid, "学生%d" % i, "%02d" % i, (i + 1) * 10)
            pid = self.make_paper(eid, sid)
            self.set_score(pid, easy, 9)
            self.set_score(pid, hard, 3)
            analysis.recalc_paper(pid)
        stats = analysis.question_stats(eid)
        self.assertEqual(stats[0]["no_label"], "难")
        self.assertEqual(stats[0]["rate"], 30.0)
        self.assertEqual(stats[1]["rate"], 90.0)

    def test_满分零分人数(self):
        eid = self.make_exam(objective_full=0)
        q = self.make_question(eid, 10, "一", max_score=10)
        for i, s in enumerate([10, 0, 5]):
            sid = self.make_student(eid, "学生%d" % i, "%02d" % i, (i + 1) * 10)
            pid = self.make_paper(eid, sid)
            self.set_score(pid, q, s)
            analysis.recalc_paper(pid)
        st = analysis.question_stats(eid)[0]
        self.assertEqual(st["full_count"], 1)
        self.assertEqual(st["zero_count"], 1)
        self.assertEqual(st["graded_count"], 3)

    def test_没填知识点归到未标注(self):
        eid = self.make_exam(objective_full=0)
        q = self.make_question(eid, 10, "一", max_score=10, knowledge="")
        sid = self.make_student(eid)
        pid = self.make_paper(eid, sid)
        self.set_score(pid, q, 6)
        analysis.recalc_paper(pid)
        ks = analysis.knowledge_stats(eid)
        self.assertEqual(ks[0]["knowledge_point"], "未标注")
        self.assertEqual(ks[0]["rate"], 60.0)


class TestWrongList(TempDataCase):
    def test_只列得分率低的题(self):
        eid = self.make_exam(objective_full=0)
        good = self.make_question(eid, 10, "答得好", max_score=10)
        bad = self.make_question(eid, 20, "答得差", max_score=10)
        sid = self.make_student(eid, "张三")
        pid = self.make_paper(eid, sid)
        self.set_score(pid, good, 9)
        self.set_score(pid, bad, 3)
        analysis.recalc_paper(pid)
        items = analysis.wrong_list(eid, 60)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "张三")
        self.assertEqual([x["no_label"] for x in items[0]["items"]], ["答得差"])
        self.assertEqual(items[0]["items"][0]["lost"], 7.0)


if __name__ == "__main__":
    unittest.main()
