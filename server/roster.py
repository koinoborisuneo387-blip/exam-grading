# -*- coding: utf-8 -*-
"""名单解析，以及把 AI 从卷面读到的姓名/学号对到名单上。

纯函数，不碰数据库、不碰网络，方便单测。

**这里的判断关系到「分数记到谁头上」，宁可标黄让老师确认，也不许猜。**
姓名只有 2 个字时不做模糊匹配 ——「张三」和「张山」编辑距离也是 1，猜错代价太大。
"""
from __future__ import annotations

import re

# 一行里「学号 姓名」的写法，学号在前
_NO_NAME = re.compile(r"^\s*(\d{1,20})\s*[\s,，\t、:：-]\s*(.+?)\s*$")
# 「姓名 学号」的写法，学号在后
_NAME_NO = re.compile(r"^\s*([^\d\s,，\t]{1,20})\s*[\s,，\t、:：-]\s*(\d{1,20})\s*$")

_SPACE = " \t\r\n　 ​"
_PUNCT = "·．.,，、;；:：'\"“”‘’()（）[]【】-—_"

# 卷面上常见的前缀，AI 有时会连标签一起读回来
_LABEL_PREFIX = re.compile(r"^(姓\s*名|名\s*字|学\s*生|考\s*生|name)\s*[:：]?\s*", re.I)
_NO_PREFIX = re.compile(r"^(学\s*号|考\s*号|座\s*位\s*号|准考证号|no)\s*[:：]?\s*", re.I)

# 明显不是人名的回答
_NOT_A_NAME = {"", "无", "没有", "未知", "不详", "看不清", "无法识别", "null", "none",
               "n/a", "na", "空", "-", "—", "/", "？", "?"}


def normalize_name(text) -> str:
    """去空白、去标点、去「姓名：」这类前缀。不改大小写以外的字形。"""
    s = str(text or "").strip()
    s = _LABEL_PREFIX.sub("", s)
    out = []
    for ch in s:
        if ch in _SPACE or ch in _PUNCT:
            continue
        out.append(ch)
    return "".join(out)


def normalize_no(text) -> str:
    """学号只留数字和字母，去掉前导零之外的杂字符。"""
    s = str(text or "").strip()
    s = _NO_PREFIX.sub("", s)
    s = re.sub(r"[^0-9A-Za-z]", "", s)
    return s.upper()


def is_meaningful_name(text) -> bool:
    """AI 读不出来时会回「无」「看不清」之类，这些不能当成人名。"""
    s = normalize_name(text).lower()
    if s in _NOT_A_NAME:
        return False
    if not s:
        return False
    # 纯数字不是姓名（多半是把学号读成姓名了）
    if s.isdigit():
        return False
    return len(s) <= 20


def parse_roster(text: str) -> list:
    """一行一个学生。支持「学号 姓名」「学号,姓名」「姓名 学号」或只有姓名。"""
    out = []
    for line in str(text or "").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _NO_NAME.match(line)
        if m and is_meaningful_name(m.group(2)):
            out.append({"student_no": m.group(1), "name": m.group(2).strip()})
            continue
        m = _NAME_NO.match(line)
        if m and is_meaningful_name(m.group(1)):
            out.append({"student_no": m.group(2), "name": m.group(1).strip()})
            continue
        out.append({"student_no": "", "name": line})
    return out


def edit_distance(a: str, b: str) -> int:
    """标准编辑距离。名字都很短，直接算不优化。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# 姓名短于这个长度就不做模糊匹配：两个字的名字差一个字往往是另一个真人
MIN_FUZZY_LEN = 3


def match_student(name, student_no, roster):
    """把识别到的 (姓名, 学号) 对到名单上。

    roster: [{"id":.., "name":.., "student_no":..}, ...]
    返回 {"student_id", "confidence": high|low|none, "reason", "matched_name"}

    置信度含义：
      high —— 可以直接绑，界面打绿勾
      low  —— 像但不确定，界面标黄，**必须老师点头**
      none —— 名单里没有 / 读不出来，界面标黄或标红
    """
    result = {"student_id": None, "confidence": "none", "reason": "",
              "matched_name": ""}
    roster = roster or []

    no = normalize_no(student_no)
    if no:
        for s in roster:
            if normalize_no(s.get("student_no")) and normalize_no(s["student_no"]) == no:
                result.update({"student_id": s["id"], "confidence": "high",
                               "reason": "学号对上了", "matched_name": s["name"]})
                return result

    if not is_meaningful_name(name):
        result["reason"] = "没读出姓名"
        return result

    target = normalize_name(name)

    exact = [s for s in roster if normalize_name(s.get("name")) == target]
    if len(exact) == 1:
        result.update({"student_id": exact[0]["id"], "confidence": "high",
                       "reason": "姓名对上了", "matched_name": exact[0]["name"]})
        return result
    if len(exact) > 1:
        # 名单里有重名，不猜，交给老师
        result.update({"confidence": "low", "reason": "名单里有多个同名的，请手动选",
                       "matched_name": exact[0]["name"]})
        return result

    if len(target) >= MIN_FUZZY_LEN:
        near = []
        for s in roster:
            other = normalize_name(s.get("name"))
            if not other:
                continue
            if abs(len(other) - len(target)) > 1:
                continue
            if edit_distance(target, other) == 1:
                near.append(s)
        if len(near) == 1:
            result.update({"student_id": near[0]["id"], "confidence": "low",
                           "reason": "和「%s」很像，请确认是不是同一个人" % near[0]["name"],
                           "matched_name": near[0]["name"]})
            return result
        if len(near) > 1:
            result.update({"confidence": "low", "reason": "名单里有几个都很像，请手动选"})
            return result

    result["reason"] = "名单里没有，确认后会新建"
    return result


def group_pages(pages) -> list:
    """把识别结果按学生分组。

    pages: [{"rel":.., "name":.., "student_no":.., "has_header":bool}, ...]（按页序）
    有姓名栏的页开启一个新学生，没有姓名栏的页归到上一个学生名下。
    第一页就没有姓名栏时，也得单独成组 —— 不能丢页。
    """
    groups = []
    for page in pages or []:
        starts_new = bool(page.get("has_header")) and is_meaningful_name(page.get("name"))
        if not groups or starts_new:
            groups.append({
                "name": page.get("name") if is_meaningful_name(page.get("name")) else "",
                "student_no": normalize_no(page.get("student_no")),
                "rels": [],
            })
        g = groups[-1]
        g["rels"].append(page.get("rel"))
        # 后面的页上如果读到了信息，而首页没读到，就补上
        if not g["name"] and is_meaningful_name(page.get("name")):
            g["name"] = page.get("name")
        if not g["student_no"] and normalize_no(page.get("student_no")):
            g["student_no"] = normalize_no(page.get("student_no"))
    return groups
