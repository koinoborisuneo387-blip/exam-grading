# -*- coding: utf-8 -*-
"""个人报告：名次、班平均对照、错题清单的口径。"""
from __future__ import annotations

import unittest

from helper import TempDataCase
from server import analysis, api


class TestStudentReport(TempDataCase):
    def test_批完的卷子_名次班平均错题都对(self):
        exam_id = self.make_exam()  # 满分150 客观45
        q1 = self.make_question(exam_id, 10, "三、1", max_score=12, knowledge="修辞")
        q2 = self.make_question(exam_id, 20, "四", max_score=60, knowledge="作文")
        s1 = self.make_student(exam_id, "张三", "01", 10)
        s2 = self.make_student(exam_id, "李四", "02", 20)
        p1 = self.make_paper(exam_id, s1, objective=40)
        p2 = self.make_paper(exam_id, s2, objective=45)
        self.set_score(p1, q1, 6)    # 50% → 该进错题清单
        self.set_score(p1, q2, 55)
        self.set_score(p2, q1, 12)
        self.set_score(p2, q2, 50)
        analysis.recalc_paper(p1)    # 40+6+55 = 101
        analysis.recalc_paper(p2)    # 45+12+50 = 107 → 第 1 名

        rep = analysis.student_report(exam_id, s1)
        self.assertEqual(rep["paper"]["total_score"], 101)
        self.assertEqual(rep["rank"], 2)
        self.assertEqual(rep["graded_count"], 2)
        self.assertEqual(rep["class_mean"], 104)  # (101+107)/2

        items = rep["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["no_label"], "三、1")
        self.assertEqual(items[0]["score"], 6)
        self.assertEqual(items[0]["class_mean"], 9)  # (6+12)/2
        self.assertEqual(items[1]["score"], 55)

        wrong = rep["wrong"]
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0]["no_label"], "三、1")
        self.assertEqual(wrong[0]["lost"], 6)
        self.assertEqual(wrong[0]["rate"], 50)

    def test_没批完时不给名次(self):
        exam_id = self.make_exam(objective_full=0)
        q1 = self.make_question(exam_id, 10, "三、1", max_score=12)
        self.make_question(exam_id, 20, "三、2", max_score=12)
        s1 = self.make_student(exam_id)
        p1 = self.make_paper(exam_id, s1)
        self.set_score(p1, q1, 8)
        analysis.recalc_paper(p1)

        rep = analysis.student_report(exam_id, s1)
        self.assertEqual(rep["paper"]["status"], "doing")
        self.assertIsNone(rep["rank"])
        # 没批的题得分是 None，不是 0 —— 前端显示「—」
        self.assertIsNone(rep["items"][1]["score"])
        # 没批的题不进错题清单
        self.assertEqual([w["no_label"] for w in rep["wrong"]], [])

    def test_评语跟着题走_总评在paper上(self):
        exam_id = self.make_exam(objective_full=0)
        q1 = self.make_question(exam_id, max_score=10)
        s1 = self.make_student(exam_id)
        p1 = self.make_paper(exam_id, s1)
        self.set_score(p1, q1, 9)
        from server import db
        db.execute("UPDATE score SET comment=? WHERE paper_id=?", ("句式再紧凑些", p1))
        db.execute("UPDATE paper SET comment=? WHERE id=?", ("进步明显", p1))
        analysis.recalc_paper(p1)

        rep = analysis.student_report(exam_id, s1)
        self.assertEqual(rep["items"][0]["comment"], "句式再紧凑些")
        self.assertEqual(rep["paper"]["comment"], "进步明显")

    def test_还没建答卷的学生也能出报告(self):
        exam_id = self.make_exam()
        self.make_question(exam_id)
        s1 = self.make_student(exam_id)
        rep = api.api_student_report(None, str(s1))
        self.assertEqual(rep["student"]["name"], "张三")
        self.assertIsNone(rep["paper"])
        self.assertIsNone(rep["rank"])
        self.assertIsNone(rep["items"][0]["score"])


if __name__ == "__main__":
    unittest.main()
