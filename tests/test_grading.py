# -*- coding: utf-8 -*-
"""分数处理与题型。"""
from __future__ import annotations

import unittest

from helper import ROOT  # noqa: F401  （负责把项目根加进 sys.path）
from server import grading


class TestRoundScore(unittest.TestCase):
    def test_保留两位小数(self):
        self.assertEqual(grading.round_score(8.666666), 8.67)
        self.assertEqual(grading.round_score(12), 12.0)

    def test_负零抹平(self):
        self.assertEqual(grading.round_score(-0.0), 0.0)
        self.assertEqual(str(grading.round_score(-0.0)), "0.0")

    def test_None原样返回(self):
        self.assertIsNone(grading.round_score(None))


class TestClampScore(unittest.TestCase):
    def test_超过满分要夹回来(self):
        self.assertEqual(grading.clamp_score(99, 12), 12.0)

    def test_负分归零(self):
        self.assertEqual(grading.clamp_score(-5, 12), 0.0)

    def test_空字符串表示清空(self):
        self.assertIsNone(grading.clamp_score("", 12))
        self.assertIsNone(grading.clamp_score(None, 12))

    def test_乱输的东西不落库(self):
        self.assertIsNone(grading.clamp_score("abc", 12))

    def test_满分为零时不限制上限(self):
        # 满分还没填的题，先让老师随便记个数，别静默改成 0
        self.assertEqual(grading.clamp_score(7, 0), 7.0)

    def test_小数正常(self):
        self.assertEqual(grading.clamp_score(8.5, 12), 8.5)


class TestPaperTotal(unittest.TestCase):
    def test_主观题加客观题(self):
        self.assertEqual(grading.paper_total([9, 12, 44], 40), 105.0)

    def test_没批的题按零算(self):
        self.assertEqual(grading.paper_total([9, None, 44], 0), 53.0)

    def test_客观题缺省为零(self):
        self.assertEqual(grading.paper_total([10]), 10.0)


class TestQType(unittest.TestCase):
    def test_全是主观题(self):
        # 这个系统不批客观题，题型里不许出现单选多选判断填空
        for bad in ("single", "multi", "judge", "fill"):
            self.assertNotIn(bad, grading.QTYPES)

    def test_不认识的题型退回默认(self):
        self.assertEqual(grading.normalize_qtype("single"), grading.DEFAULT_QTYPE)
        self.assertEqual(grading.normalize_qtype(""), grading.DEFAULT_QTYPE)
        self.assertEqual(grading.normalize_qtype("composition"), "composition")

    def test_每个题型都有中文名(self):
        for q in grading.QTYPES:
            self.assertIn(q, grading.QTYPE_LABELS)
            self.assertTrue(grading.QTYPE_LABELS[q])


class TestFullyGraded(unittest.TestCase):
    def test_全批完才算完(self):
        self.assertTrue(grading.is_fully_graded([1, 0, 5]))
        self.assertFalse(grading.is_fully_graded([1, None]))
        self.assertFalse(grading.is_fully_graded([]))


if __name__ == "__main__":
    unittest.main()
