# -*- coding: utf-8 -*-
"""从 PDF 抠图。这是整个导入功能的地基，出错老师就导不进卷子。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import zlib
from pathlib import Path

from helper import build_pdf, fake_jpeg, flate_gray_page
from server import imgutil, pdfimg


class TestExtract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pjpg_pdf_"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_扫描件JPEG原样抠出来(self):
        jpg = fake_jpeg(300, 400)
        pdf = build_pdf([(b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                          jpg, 300, 400)])
        pages = pdfimg.extract_pages(pdf, self.tmp, "t")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["path"].suffix, ".jpg")
        # 必须是原封不动的字节，不能重新编码（重编会掉画质）
        self.assertEqual(pages[0]["path"].read_bytes(), jpg)
        self.assertEqual((pages[0]["width"], pages[0]["height"]), (300, 400))

    def test_多页按顺序抠(self):
        jpg = fake_jpeg(120, 160)
        page = (b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                jpg, 120, 160)
        pages = pdfimg.extract_pages(build_pdf([page, page, page]), self.tmp, "t")
        self.assertEqual([p["page_no"] for p in pages], [1, 2, 3])

    def test_Flate灰度图转成PNG(self):
        pages = pdfimg.extract_pages(build_pdf([flate_gray_page(240, 180)]), self.tmp, "t")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["path"].suffix, ".png")
        self.assertEqual(imgutil.image_size(pages[0]["path"]), (240, 180))

    def test_太小的图不当答卷(self):
        # 页眉 logo、水印之类的小图不能被当成整页答卷导进来
        with self.assertRaises(pdfimg.PdfExtractError):
            pdfimg.extract_pages(build_pdf([flate_gray_page(80, 60)]), self.tmp, "t")

    def test_Flate彩色图转成PNG(self):
        w, hgt = 200, 150
        rgb = bytearray()
        for y in range(hgt):
            for x in range(w):
                rgb += bytes((x * 4 % 256, y * 6 % 256, 100))
        page = (b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
                zlib.compress(bytes(rgb)), w, hgt)
        pages = pdfimg.extract_pages(build_pdf([page]), self.tmp, "t")
        self.assertEqual(imgutil.image_size(pages[0]["path"]), (w, hgt))

    def test_一页多图取最大的那张(self):
        # 扫描件常带个小 logo，得挑整页那张，不能挑到 logo
        big = fake_jpeg(800, 1000)
        pdf = build_pdf([(b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                          big, 800, 1000)])
        small = fake_jpeg(40, 40)
        extra = (b"99 0 obj\n<< /Type /XObject /Subtype /Image /Width 40 /Height 40 "
                 b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                 b"/Length %d >>\nstream\n" % len(small)) + small + b"\nendstream\nendobj\n"
        pdf = pdf.replace(b"/Im1 4 0 R", b"/Im1 4 0 R /Im2 99 0 R").replace(
            b"trailer", extra + b"trailer")
        pages = pdfimg.extract_pages(pdf, self.tmp, "t")
        self.assertEqual(len(pages), 1)
        self.assertEqual((pages[0]["width"], pages[0]["height"]), (800, 1000))


class TestFriendlyErrors(unittest.TestCase):
    """读不了的时候必须说人话，并告诉老师下一步该干嘛 —— 不许静默失败。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pjpg_pdferr_"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_不是PDF(self):
        with self.assertRaises(pdfimg.PdfExtractError) as cm:
            pdfimg.extract_pages(b"hello world", self.tmp, "t")
        self.assertIn("不是 PDF", str(cm.exception))

    def test_CCITT黑白传真给出重扫建议(self):
        pdf = build_pdf([(b"/ColorSpace /DeviceGray /BitsPerComponent 1 "
                          b"/Filter /CCITTFaxDecode", b"\x00" * 200, 200, 200)])
        with self.assertRaises(pdfimg.PdfExtractError) as cm:
            pdfimg.extract_pages(pdf, self.tmp, "t")
        msg = str(cm.exception)
        self.assertIn("CCITT", msg)
        self.assertIn("重新扫", msg)
        # 具体原因要直接报出来，不能被"这不是扫描件"那段通用话盖掉
        self.assertNotIn("不是扫描件", msg)

    def test_文字版PDF提示拍照(self):
        text_pdf = (b"%PDF-1.4\n"
                    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
                    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    b"/Resources << /Font << /F1 4 0 R >> >> >>\nendobj\n"
                    b"4 0 obj\n<< /Type /Font /Subtype /Type1 >>\nendobj\n"
                    b"trailer\n<< /Size 5 /Root 1 0 R >>\n%%EOF\n")
        with self.assertRaises(pdfimg.PdfExtractError) as cm:
            pdfimg.extract_pages(text_pdf, self.tmp, "t")
        msg = str(cm.exception)
        self.assertIn("不是扫描件", msg)
        self.assertIn("拍", msg)


class TestDictParsing(unittest.TestCase):
    """PDF 字典解析。get_raw 读不了名字型的值曾经导致 JPEG 被当成裸像素，很隐蔽。"""

    def test_名字型的值要带斜杠一起返回(self):
        d = b"<< /Filter /DCTDecode /Length 10 >>"
        self.assertEqual(pdfimg.get_raw(d, "Filter"), b"/DCTDecode")
        self.assertEqual(pdfimg.get_filters(d), ["DCTDecode"])

    def test_数组型的过滤器(self):
        d = b"<< /Filter [/FlateDecode /DCTDecode] >>"
        self.assertEqual(pdfimg.get_filters(d), ["FlateDecode", "DCTDecode"])

    def test_Length不会误匹配Length1(self):
        d = b"<< /Length1 999 /Length 42 >>"
        self.assertEqual(pdfimg.get_int(d, "Length"), 42)

    def test_间接引用(self):
        self.assertEqual(pdfimg.get_ref(b"<< /Pages 7 0 R >>", "Pages"), 7)

    def test_布尔值(self):
        self.assertTrue(pdfimg.get_bool(b"<< /ImageMask true >>", "ImageMask"))
        self.assertFalse(pdfimg.get_bool(b"<< /ImageMask false >>", "ImageMask"))


if __name__ == "__main__":
    unittest.main()
