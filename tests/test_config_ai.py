# -*- coding: utf-8 -*-
"""API Key 的读取方式，和 AI 返回值的解析。

老师的用法是「往 API_KEY.txt 里粘一行就完事」，这条路必须稳。
"""
from __future__ import annotations

import json
import unittest

from helper import TempDataCase
from server import ai, config


class TestKeyFile(TempDataCase):
    def _write_key(self, text):
        config.key_file().write_text(text, encoding="utf-8")

    def test_粘一行就能读到(self):
        self._write_key("abc123.SECRET\n")
        key, src = config.key_from_file()
        self.assertEqual(key, "abc123.SECRET")
        self.assertTrue(str(src).endswith("API_KEY.txt"))

    def test_跳过注释行和空行(self):
        self._write_key("# 说明\n#  还是说明\n\n   real-key-here  \n")
        self.assertEqual(config.key_from_file()[0], "real-key-here")

    def test_只有模板没有密钥时算没填(self):
        config.ensure_key_file()
        self.assertEqual(config.key_from_file()[0], "")
        self.assertFalse(config.public_config()["ai"]["has_key"])

    def test_有密钥就自动打开AI(self):
        self._write_key("real-key")
        cfg = config.load_config()
        self.assertEqual(cfg["ai"]["api_key"], "real-key")
        self.assertTrue(cfg["ai"]["enabled"])

    def test_老师手动关掉的话就别自作主张打开(self):
        self._write_key("real-key")
        config.save_config({"ai": {"enabled": False}})
        self.assertFalse(config.load_config()["ai"]["enabled"])

    def test_密钥不会被抄进config_json(self):
        # 一个密钥只存一个地方，免得改了文件还留着旧的
        self._write_key("real-key")
        config.save_config({"ai": {"model": "glm-5v-turbo"}})
        raw = json.loads(config.config_path().read_text(encoding="utf-8"))
        self.assertEqual(raw["ai"]["api_key"], "")
        self.assertEqual(config.load_config()["ai"]["api_key"], "real-key")

    def test_前端拿不到密钥明文(self):
        self._write_key("super-secret")
        pub = json.dumps(config.public_config(), ensure_ascii=False)
        self.assertNotIn("super-secret", pub)
        self.assertTrue(config.public_config()["ai"]["has_key"])

    def test_默认预置的是能看图的智谱视觉模型(self):
        cfg = config.load_config()
        self.assertIn("bigmodel.cn", cfg["ai"]["base_url"])
        self.assertEqual(cfg["ai"]["model"], "glm-5v-turbo")
        self.assertTrue(cfg["ai"]["vision"])
        # 思考型模型要留足时间，太短会在整卷批改时超时
        self.assertGreaterEqual(cfg["ai"]["timeout"], 120)

    def test_模板文件带中文说明(self):
        path = config.ensure_key_file()
        self.assertTrue(path.exists())
        self.assertIn("API Key", path.read_text(encoding="utf-8"))


class TestAIParsing(unittest.TestCase):
    def test_直接是JSON(self):
        obj = ai._extract_json('{"score": 9, "comment": "不错"}')
        self.assertEqual(obj["score"], 9)

    def test_包在代码块里(self):
        obj = ai._extract_json('好的：\n```json\n{"score": 8.5}\n```\n以上。')
        self.assertEqual(obj["score"], 8.5)

    def test_前后带废话(self):
        obj = ai._extract_json('我的判分如下 {"score": 7, "reasons": ["少了要点二"]} 仅供参考')
        self.assertEqual(obj["reasons"], ["少了要点二"])

    def test_完全看不懂就报错并带上原文(self):
        with self.assertRaises(ai.AIError) as cm:
            ai._extract_json("模型今天不想干活")
        self.assertIn("模型今天不想干活", str(cm.exception))


class TestAIImages(unittest.TestCase):
    def test_只收正经的图片dataURL(self):
        good = "data:image/jpeg;base64,AAAABBBB=="
        bad = ["javascript:alert(1)", "data:text/html;base64,AAA",
               "http://x/a.jpg", "", None]
        self.assertEqual(ai._clean_images([good] + bad), [good])

    def test_张数有上限(self):
        one = "data:image/png;base64,AAAA"
        self.assertEqual(len(ai._clean_images([one] * 100)), ai.MAX_IMAGES)

    def test_没有图就发纯文本(self):
        # 老模型只认字符串型的 content，硬塞数组会 400
        self.assertEqual(ai._user_content("题目", []), "题目")

    def test_有图才发数组(self):
        content = ai._user_content("题目", ["data:image/png;base64,AAAA"])
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")


class TestNoMaxTokens(TempDataCase):
    """glm-5v-turbo 是思考型模型，先烧一百多 token 推理再出话。

    只要给它设了 max_tokens，额度就会被推理吃光、content 变成空字符串。
    所以**任何一处请求都不许带 max_tokens** —— 这条用真接口踩出来过，必须钉死。
    """

    def setUp(self):
        TempDataCase.setUp(self)
        config.save_config({"ai": {"enabled": True, "api_key": "k", "vision": True,
                                   "base_url": "https://x/v1", "model": "glm-5v-turbo"}})
        self.sent = []
        self._real_post = ai._post

        def fake_post(url, payload, key, timeout):
            self.sent.append(payload)
            return {"choices": [{"message": {"content": json.dumps(
                {"score": 8, "comment": "还行", "reasons": ["要点一到位"],
                 "items": [{"question_id": 1, "score": 8, "comment": "还行",
                            "reasons": ["要点一到位"]}]}, ensure_ascii=False)}}]}
        ai._post = fake_post

    def tearDown(self):
        ai._post = self._real_post
        TempDataCase.tearDown(self)

    def _assert_clean(self):
        self.assertTrue(self.sent)
        for payload in self.sent:
            self.assertNotIn("max_tokens", payload)

    def test_单题批改不带max_tokens(self):
        ai.suggest({"id": 1, "max_score": 12, "no_label": "三、1"}, "学生的答案")
        self._assert_clean()

    def test_整卷批改不带max_tokens(self):
        ai.grade_paper([{"id": 1, "max_score": 12, "no_label": "三、1"}],
                       ["data:image/png;base64,AAAA"])
        self._assert_clean()

    def test_测试连接不带max_tokens(self):
        ai.test_connection()
        self._assert_clean()

    def test_思考型模型只有推理内容时也算连通(self):
        ai._post = lambda *a: {"choices": [{"message": {
            "content": "", "reasoning_content": "我在想怎么回答"}}]}
        r = ai.test_connection()
        self.assertTrue(r["ok"])
        self.assertTrue(r["reply"])


class TestAIGuards(TempDataCase):
    def test_没配置就明确说去哪儿配(self):
        with self.assertRaises(ai.AIError) as cm:
            ai.suggest({"max_score": 12}, "学生答案")
        self.assertIn("设置", str(cm.exception))

    def test_纯文本模型没作答时提示粘文字(self):
        config.save_config({"ai": {"enabled": True, "api_key": "k",
                                   "base_url": "https://x/v1", "model": "m",
                                   "vision": False}})
        with self.assertRaises(ai.AIError) as cm:
            ai.suggest({"max_score": 12}, "")
        msg = str(cm.exception)
        self.assertIn("看不了", msg)
        self.assertIn("粘贴", msg)

    def test_整卷预批要求能看图的模型(self):
        config.save_config({"ai": {"enabled": True, "api_key": "k",
                                   "base_url": "https://x/v1", "model": "m",
                                   "vision": False}})
        with self.assertRaises(ai.AIError) as cm:
            ai.grade_paper([{"id": 1, "max_score": 12}], ["data:image/png;base64,AA"])
        self.assertIn("看不了", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
