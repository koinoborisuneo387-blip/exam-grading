# -*- coding: utf-8 -*-
"""手写的 xlsx 导出，和手写的 multipart 解析。两个都是自己造的轮子，得盯紧。"""
from __future__ import annotations

import io
import re
import unittest
import zipfile

from helper import ROOT  # noqa: F401
from server import multipart, xlsx


class TestXlsx(unittest.TestCase):
    def _build(self, sheets):
        data = xlsx.build_xlsx(sheets)
        return data, zipfile.ZipFile(io.BytesIO(data))

    def test_是个结构完整的xlsx包(self):
        data, z = self._build([{"name": "成绩总表", "rows": [["姓名", "总分"], ["张三", 105]],
                                "header_rows": 1}])
        names = z.namelist()
        for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                     "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                     "xl/worksheets/sheet1.xml"):
            self.assertIn(part, names)
        self.assertIsNone(z.testzip())

    def test_中文表名和内容都在(self):
        _, z = self._build([{"name": "成绩总表", "rows": [["姓名"], ["张三"]]}])
        self.assertIn("成绩总表", z.read("xl/workbook.xml").decode("utf-8"))
        self.assertIn("张三", z.read("xl/worksheets/sheet1.xml").decode("utf-8"))

    def test_数字存成数字文本存成文本(self):
        _, z = self._build([{"name": "s", "rows": [["张三", 105, 8.5, "", None]]}])
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<v>105</v>", sheet)
        self.assertIn("<v>8.5</v>", sheet)
        self.assertIn('t="inlineStr"', sheet)

    def test_特殊字符要转义(self):
        _, z = self._build([{"name": "s", "rows": [['<张三> & "李四"']]}])
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("&lt;", sheet)
        self.assertIn("&amp;", sheet)
        self.assertNotIn("<张三>", sheet)

    def test_控制字符会被删掉(self):
        # XML 里放控制字符会让 WPS 直接报"文件损坏"
        _, z = self._build([{"name": "s", "rows": [["坏\x01数\x0b据"]]}])
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("坏数据", sheet)

    def test_多个表且重名会自动改名(self):
        _, z = self._build([{"name": "表", "rows": [["a"]]}, {"name": "表", "rows": [["b"]]}])
        wb = z.read("xl/workbook.xml").decode("utf-8")
        names = re.findall(r'name="([^"]+)"', wb)
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)

    def test_表名里的非法字符被清掉(self):
        self.assertEqual(xlsx.safe_sheet_name("成绩/总表[1]", 0), "成绩总表1")
        self.assertEqual(xlsx.safe_sheet_name("", 2), "Sheet3")
        self.assertLessEqual(len(xlsx.safe_sheet_name("很长" * 40, 0)), 31)

    def test_列号换算(self):
        self.assertEqual(xlsx.col_letter(0), "A")
        self.assertEqual(xlsx.col_letter(25), "Z")
        self.assertEqual(xlsx.col_letter(26), "AA")
        self.assertEqual(xlsx.col_letter(51), "AZ")

    def test_csv带BOM(self):
        # 不带 BOM 的话 WPS 打开是乱码
        data = xlsx.to_csv_bytes([["姓名", "总分"], ["张三", 105]])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("张三", data.decode("utf-8-sig"))

    def test_csv里的逗号引号会被包起来(self):
        data = xlsx.to_csv_bytes([['张,三', '他说"好"']]).decode("utf-8-sig")
        self.assertIn('"张,三"', data)
        self.assertIn('"他说""好"""', data)


class TestMultipart(unittest.TestCase):
    def _body(self, boundary, parts):
        out = bytearray()
        for headers, data in parts:
            out += ("--%s\r\n%s\r\n\r\n" % (boundary, headers)).encode("utf-8")
            out += data + b"\r\n"
        out += ("--%s--\r\n" % boundary).encode()
        return bytes(out)

    def test_解析文件和普通字段(self):
        b = "----abc123"
        body = self._body(b, [
            ('Content-Disposition: form-data; name="file0"; filename="卷子.pdf"\r\n'
             'Content-Type: application/pdf', b"%PDF-1.4 fake"),
            ('Content-Disposition: form-data; name="note"', "备注".encode("utf-8")),
        ])
        parts = multipart.parse(body, "multipart/form-data; boundary=" + b)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].is_file)
        self.assertEqual(parts[0].filename, "卷子.pdf")
        self.assertEqual(parts[0].data, b"%PDF-1.4 fake")
        self.assertFalse(parts[1].is_file)
        self.assertEqual(parts[1].text, "备注")

    def test_二进制数据不能被改动(self):
        # 图片里出现 \r\n 是常事，不能被当成分隔符啃掉
        raw = bytes(range(256)) * 40
        b = "----bin"
        body = self._body(b, [
            ('Content-Disposition: form-data; name="f"; filename="a.jpg"', raw)])
        parts = multipart.parse(body, "multipart/form-data; boundary=" + b)
        self.assertEqual(parts[0].data, raw)

    def test_boundary带引号也认(self):
        b = "----q"
        body = self._body(b, [
            ('Content-Disposition: form-data; name="f"; filename="a.png"', b"x")])
        parts = multipart.parse(body, 'multipart/form-data; boundary="%s"' % b)
        self.assertEqual(len(parts), 1)

    def test_不是multipart返回空(self):
        self.assertEqual(multipart.parse(b"{}", "application/json"), [])
        self.assertEqual(multipart.parse(b"", ""), [])


if __name__ == "__main__":
    unittest.main()
