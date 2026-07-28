# -*- coding: utf-8 -*-
"""测试公共部分：把数据目录切到临时文件夹，别碰真实的 data/。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import config, db  # noqa: E402


class TempDataCase(unittest.TestCase):
    """每个用例一套干净的临时数据目录和数据库。"""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="pjpg_test_"))
        self._old_data = config.DATA_DIR
        config.DATA_DIR = self._tmp
        db.close()
        db.init()

    def tearDown(self):
        db.close()
        config.DATA_DIR = self._old_data
        shutil.rmtree(str(self._tmp), ignore_errors=True)

    # ---- 造数据的小工具 ----

    def make_exam(self, **kw):
        fields = {"name": "测试考试", "subject": "语文", "klass": "高二1班",
                  "full_score": 150, "pass_score": 90, "excellent_score": 120,
                  "objective_full": 45}
        fields.update(kw)
        return db.execute(
            "INSERT INTO exam (name, subject, klass, full_score, pass_score, "
            "excellent_score, objective_full, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (fields["name"], fields["subject"], fields["klass"], fields["full_score"],
             fields["pass_score"], fields["excellent_score"], fields["objective_full"],
             db.now(), db.now()))

    def make_question(self, exam_id, order=10, label="三、1", qtype="essay",
                      max_score=12, knowledge=""):
        return db.execute(
            "INSERT INTO question (exam_id, sort_order, no_label, qtype, max_score, "
            "stem, answer_key, rubric, knowledge_point) VALUES (?,?,?,?,?,?,?,?,?)",
            (exam_id, order, label, qtype, max_score, "题干", "参考答案", "要点", knowledge))

    def make_student(self, exam_id, name="张三", no="01", order=10):
        return db.execute(
            "INSERT INTO student (exam_id, sort_order, name, student_no) VALUES (?,?,?,?)",
            (exam_id, order, name, no))

    def make_paper(self, exam_id, student_id, objective=0):
        return db.execute(
            "INSERT INTO paper (exam_id, student_id, objective_score, updated_at) "
            "VALUES (?,?,?,?)", (exam_id, student_id, objective, db.now()))

    def set_score(self, paper_id, question_id, score):
        db.execute("INSERT INTO score (paper_id, question_id, score, updated_at) "
                   "VALUES (?,?,?,?)", (paper_id, question_id, score, db.now()))


def build_pdf(images):
    """拼一个最简 PDF。images: [(字典附加片段, 流数据, 宽, 高)]，一张一页。"""
    objs, streams = {}, {}
    page_ids = [3 + i * 3 for i in range(len(images))]
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % p for p in page_ids)
    objs[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(images)
    for i, (extra, data, w, hgt) in enumerate(images):
        pid, img_id, cont_id = page_ids[i], page_ids[i] + 1, page_ids[i] + 2
        objs[pid] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                     b"/Resources << /XObject << /Im1 %d 0 R >> >> /Contents %d 0 R >>"
                     % (img_id, cont_id))
        objs[img_id] = (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                        % (w, hgt)) + extra + (b" /Length %d >>" % len(data))
        streams[img_id] = data
        content = b"q 595 0 0 842 0 0 cm /Im1 Do Q"
        objs[cont_id] = b"<< /Length %d >>" % len(content)
        streams[cont_id] = content
    out = bytearray(b"%PDF-1.4\n")
    for num in sorted(objs):
        out += b"%d 0 obj\n" % num + objs[num]
        if num in streams:
            out += b"\nstream\n" + streams[num] + b"\nendstream"
        out += b"\nendobj\n"
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n%%%%EOF\n" % (max(objs) + 1)
    return bytes(out)


def flate_gray_page(width=120, height=80):
    """造一张 Flate 压缩的灰度图页面。"""
    samples = bytearray()
    for y in range(height):
        for x in range(width):
            samples.append((x * 255 // width) if y < height // 2 else 40)
    return (b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode",
            zlib.compress(bytes(samples)), width, height)


def fake_jpeg(width=200, height=150):
    """拼一个头部合法的 JPEG（够 jpeg_size 解析，不要求能真的解码成图）。"""
    import struct
    out = bytearray(b"\xff\xd8")                      # SOI
    out += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00" + b"\x00" * 7
    sof = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    out += b"\x00" * 64
    out += b"\xff\xd9"                                # EOI
    return bytes(out)
