# -*- coding: utf-8 -*-
"""名单解析与姓名匹配。

**这组用例守的是「分数记到谁头上」。** 宁可标黄让老师确认，也不许猜错人 ——
认错名字要等整班批完才会被发现，比批错分难收拾得多。
"""
from __future__ import annotations

import unittest

from helper import ROOT  # noqa: F401
from server import roster

ROSTER = [
    {"id": 1, "name": "张三", "student_no": "01"},
    {"id": 2, "name": "李四", "student_no": "02"},
    {"id": 3, "name": "欧阳修远", "student_no": "03"},
    {"id": 4, "name": "王小明", "student_no": "04"},
]


class TestNormalize(unittest.TestCase):
    def test_去空白和标点(self):
        self.assertEqual(roster.normalize_name("  张 三 "), "张三")
        self.assertEqual(roster.normalize_name("张·三"), "张三")

    def test_去掉姓名标签(self):
        self.assertEqual(roster.normalize_name("姓名：李四"), "李四")
        self.assertEqual(roster.normalize_name("姓 名: 李四"), "李四")

    def test_学号只留数字字母(self):
        self.assertEqual(roster.normalize_no("学号：01"), "01")
        self.assertEqual(roster.normalize_no("No. A-12"), "A12")

    def test_识别不出来的回答不算姓名(self):
        for bad in ("", "无", "看不清", "null", "None", "—", "?", "未知"):
            self.assertFalse(roster.is_meaningful_name(bad), bad)

    def test_纯数字不算姓名(self):
        # 多半是把学号读成姓名了，不能拿去建学生
        self.assertFalse(roster.is_meaningful_name("01"))

    def test_正常姓名算数(self):
        for good in ("张三", "欧阳修远", "买买提·艾力"):
            self.assertTrue(roster.is_meaningful_name(good), good)


class TestParseRoster(unittest.TestCase):
    def test_学号在前(self):
        self.assertEqual(roster.parse_roster("01 张三"),
                         [{"student_no": "01", "name": "张三"}])

    def test_逗号分隔(self):
        self.assertEqual(roster.parse_roster("02,李四"),
                         [{"student_no": "02", "name": "李四"}])

    def test_姓名在前学号在后(self):
        self.assertEqual(roster.parse_roster("王五 05"),
                         [{"student_no": "05", "name": "王五"}])

    def test_只有姓名(self):
        self.assertEqual(roster.parse_roster("赵六"),
                         [{"student_no": "", "name": "赵六"}])

    def test_空行跳过(self):
        self.assertEqual(len(roster.parse_roster("张三\n\n  \n李四")), 2)


class TestMatchHighConfidence(unittest.TestCase):
    def test_学号相同直接对上(self):
        m = roster.match_student("", "02", ROSTER)
        self.assertEqual(m["student_id"], 2)
        self.assertEqual(m["confidence"], "high")

    def test_学号优先于姓名(self):
        # 姓名读错了但学号对，以学号为准
        m = roster.match_student("李西", "02", ROSTER)
        self.assertEqual(m["student_id"], 2)
        self.assertEqual(m["confidence"], "high")

    def test_姓名完全相同(self):
        m = roster.match_student("张三", "", ROSTER)
        self.assertEqual(m["student_id"], 1)
        self.assertEqual(m["confidence"], "high")

    def test_姓名带空格和标签也能对上(self):
        for variant in ("  张 三 ", "姓名：张三", "张·三"):
            m = roster.match_student(variant, "", ROSTER)
            self.assertEqual(m["student_id"], 1, variant)
            self.assertEqual(m["confidence"], "high", variant)


class TestMatchDangerousCases(unittest.TestCase):
    """这些是会把分记错人的场景，必须挡住。"""

    def test_两个字的名字绝不做模糊匹配(self):
        # 「张山」和「张三」编辑距离是 1，但很可能是班上另一个真人
        m = roster.match_student("张山", "", ROSTER)
        self.assertIsNone(m["student_id"])
        self.assertNotEqual(m["confidence"], "high")

    def test_两字名相差一字一律当新人(self):
        for wrong in ("张山", "李西", "张二"):
            m = roster.match_student(wrong, "", ROSTER)
            self.assertIsNone(m["student_id"], wrong)

    def test_三字以上才模糊匹配且只给低置信度(self):
        m = roster.match_student("欧阳修元", "", ROSTER)
        self.assertEqual(m["student_id"], 3)
        self.assertEqual(m["confidence"], "low")
        self.assertIn("欧阳修远", m["reason"])

    def test_名单里有重名时不猜(self):
        dup = ROSTER + [{"id": 9, "name": "张三", "student_no": "09"}]
        m = roster.match_student("张三", "", dup)
        self.assertIsNone(m["student_id"])
        self.assertEqual(m["confidence"], "low")

    def test_有好几个都很像时不猜(self):
        many = [{"id": 1, "name": "王小明", "student_no": "01"},
                {"id": 2, "name": "王小朋", "student_no": "02"}]
        m = roster.match_student("王小名", "", many)
        self.assertIsNone(m["student_id"])
        self.assertEqual(m["confidence"], "low")

    def test_读不出姓名就明确说读不出(self):
        m = roster.match_student("看不清", "", ROSTER)
        self.assertIsNone(m["student_id"])
        self.assertEqual(m["confidence"], "none")
        self.assertIn("没读出", m["reason"])

    def test_名单里没有就提示会新建(self):
        m = roster.match_student("新同学", "", ROSTER)
        self.assertIsNone(m["student_id"])
        self.assertEqual(m["confidence"], "none")
        self.assertIn("新建", m["reason"])

    def test_名单为空时不报错(self):
        m = roster.match_student("张三", "01", [])
        self.assertIsNone(m["student_id"])


class TestGroupPages(unittest.TestCase):
    def test_按姓名栏分组(self):
        pages = [
            {"rel": "a1", "name": "张三", "student_no": "01", "has_header": True},
            {"rel": "a2", "name": "", "student_no": "", "has_header": False},
            {"rel": "b1", "name": "李四", "student_no": "02", "has_header": True},
            {"rel": "b2", "name": "", "student_no": "", "has_header": False},
        ]
        groups = roster.group_pages(pages)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["name"], "张三")
        self.assertEqual(groups[0]["rels"], ["a1", "a2"])
        self.assertEqual(groups[1]["rels"], ["b1", "b2"])

    def test_每人一页(self):
        pages = [
            {"rel": "a", "name": "张三", "student_no": "01", "has_header": True},
            {"rel": "b", "name": "李四", "student_no": "02", "has_header": True},
        ]
        self.assertEqual(len(roster.group_pages(pages)), 2)

    def test_第一页没有姓名栏也不能丢页(self):
        pages = [
            {"rel": "x", "name": "", "student_no": "", "has_header": False},
            {"rel": "y", "name": "张三", "student_no": "01", "has_header": True},
        ]
        groups = roster.group_pages(pages)
        self.assertEqual(sum(len(g["rels"]) for g in groups), 2)
        self.assertEqual(groups[0]["rels"], ["x"])

    def test_姓名写在第二页也能捡回来(self):
        pages = [
            {"rel": "p1", "name": "", "student_no": "", "has_header": True},
            {"rel": "p2", "name": "张三", "student_no": "01", "has_header": False},
        ]
        groups = roster.group_pages(pages)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "张三")
        self.assertEqual(groups[0]["student_no"], "01")

    def test_有姓名栏但姓名读不出不另起一组(self):
        # 不然一份卷子会被拆成好几个"无名氏"
        pages = [
            {"rel": "p1", "name": "张三", "student_no": "01", "has_header": True},
            {"rel": "p2", "name": "看不清", "student_no": "", "has_header": True},
        ]
        groups = roster.group_pages(pages)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["rels"], ["p1", "p2"])

    def test_空输入(self):
        self.assertEqual(roster.group_pages([]), [])
        self.assertEqual(roster.group_pages(None), [])


class TestEditDistance(unittest.TestCase):
    def test_基本(self):
        self.assertEqual(roster.edit_distance("张三", "张三"), 0)
        self.assertEqual(roster.edit_distance("张三", "张山"), 1)
        self.assertEqual(roster.edit_distance("欧阳修远", "欧阳修"), 1)
        self.assertEqual(roster.edit_distance("", "张三"), 2)


if __name__ == "__main__":
    unittest.main()
