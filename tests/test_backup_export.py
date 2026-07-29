# -*- coding: utf-8 -*-
"""数据备份打包、全班批注卷打包。

这两个接口都走「临时文件 + 流式发送」，测试里拿到 FileResponse 后
要自己把 resp.path 删掉（正常运行时由 httpd 发完删）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from helper import TempDataCase
from server import api, config, db


class TestBackupZip(TempDataCase):
    def _call(self):
        resp = api.api_backup_data(None)
        self.assertIsNone(resp.data)
        self.assertTrue(resp.cleanup)
        self.assertTrue(resp.path.exists())
        return resp

    def test_备份里有数据库快照和数据文件_没有旧代码备份(self):
        exam_id = self.make_exam()
        up = config.uploads_dir() / str(exam_id) / "p1"
        up.mkdir(parents=True)
        (up / "第1页.jpg").write_bytes(b"\xff\xd8fake")
        (config.data_dir() / "API_KEY.txt").write_text("k-123", encoding="utf-8")
        # update.sh 存的旧代码备份，不是老师的数据，不该进包
        old = config.data_dir() / "_backup_1.0.0"
        old.mkdir()
        (old / "app.py").write_text("old code", encoding="utf-8")

        db_name = config.db_path().name
        resp = self._call()
        try:
            with zipfile.ZipFile(str(resp.path)) as z:
                names = z.namelist()
                self.assertIn("data/" + db_name, names)
                self.assertIn("data/uploads/%s/p1/第1页.jpg" % exam_id, names)
                self.assertIn("data/API_KEY.txt", names)
                for name in names:
                    self.assertNotIn("_backup_", name)
                    self.assertFalse(name.endswith("-wal"))
                    self.assertFalse(name.endswith("-shm"))
                raw = z.read("data/" + db_name)
        finally:
            resp.path.unlink()

        # 快照必须是一个能打开的库，而且考试数据真的在里面
        snap = Path(tempfile.mkdtemp(prefix="pjpg_t_")) / "snap.db"
        snap.write_bytes(raw)
        conn = sqlite3.connect(str(snap))
        try:
            count = conn.execute("SELECT COUNT(*) FROM exam").fetchone()[0]
            name = conn.execute("SELECT name FROM exam").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)
        self.assertEqual(name, "测试考试")

    def test_文件名带日期(self):
        resp = self._call()
        try:
            self.assertTrue(resp.filename.startswith("试卷批改系统备份_"))
            self.assertTrue(resp.filename.endswith(".zip"))
        finally:
            resp.path.unlink()


class TestMarkedZipAll(TempDataCase):
    def _rel(self, path):
        return path.relative_to(config.data_dir()).as_posix()

    def test_一人一个文件夹_优先批注图_没答卷的不出现(self):
        exam_id = self.make_exam()
        s1 = self.make_student(exam_id, "张三", "01", 10)
        s2 = self.make_student(exam_id, "李四", "02", 20)
        self.make_student(exam_id, "王五", "03", 30)  # 没导答卷
        p1 = self.make_paper(exam_id, s1)
        p2 = self.make_paper(exam_id, s2)

        d1 = config.uploads_dir() / str(exam_id) / ("p%d" % p1)
        d2 = config.uploads_dir() / str(exam_id) / ("p%d" % p2)
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)
        (d1 / "a.jpg").write_bytes(b"raw-1")
        (d1 / "a_marked.png").write_bytes(b"marked-1")
        (d2 / "b.jpg").write_bytes(b"raw-2")

        db.execute(
            "INSERT INTO page (paper_id, page_no, image_path, annotated_path) "
            "VALUES (?,?,?,?)",
            (p1, 1, self._rel(d1 / "a.jpg"), self._rel(d1 / "a_marked.png")))
        db.execute(
            "INSERT INTO page (paper_id, page_no, image_path) VALUES (?,?,?)",
            (p2, 1, self._rel(d2 / "b.jpg")))

        resp = api.api_export_marked_all(None, str(exam_id))
        try:
            with zipfile.ZipFile(str(resp.path)) as z:
                names = sorted(z.namelist())
                self.assertEqual(names, ["01_张三/第1页.png", "02_李四/第1页.jpg"])
                # 有批注图用批注图，没有才用原图
                self.assertEqual(z.read("01_张三/第1页.png"), b"marked-1")
                self.assertEqual(z.read("02_李四/第1页.jpg"), b"raw-2")
        finally:
            resp.path.unlink()

    def test_一张图都没有时明确报错_不给空包(self):
        exam_id = self.make_exam()
        self.make_student(exam_id)
        with self.assertRaises(api.ApiError):
            api.api_export_marked_all(None, str(exam_id))


if __name__ == "__main__":
    unittest.main()
