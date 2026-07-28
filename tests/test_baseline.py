# -*- coding: utf-8 -*-
"""语法基线守门：确保代码能在老师那台机器上跑起来。

老师用的是**统信 UOS Desktop 20 Pro**（代号 eagle，基于 Debian 10），
自带的是 **Python 3.7.3**。开发机是 3.12，**能跑不代表那边能跑**。

这组用例就是替那台机器把关：
- Python 用 ast 的 feature_version=(3,7) 真按 3.7 的语法规则解析一遍
- 3.8+/3.9+ 才有的标准库 API（语法合法但运行会炸）用文本扫描挡掉
- 前端 JS 同理，挡掉老浏览器不认的写法
"""
from __future__ import annotations

import ast
import io
import re
import unittest
from pathlib import Path

from helper import ROOT  # noqa: F401

PY_BASELINE = (3, 7)

# 只管会发到老师机器上的代码；tests/ 自己不会被发过去，可以随便用新语法
PROD_PY = sorted((ROOT / "server").glob("*.py")) + [ROOT / "app.py"]
PROD_JS = sorted((ROOT / "static" / "js").glob("*.js"))
PROD_HTML = sorted((ROOT / "static").glob("*.html"))


def read(path):
    with io.open(str(path), encoding="utf-8") as f:
        return f.read()


def strip_js_comments(src: str) -> list:
    """把 JS 注释换成空白，返回逐行列表（行号保持不变，方便报位置）。

    必须先剥注释再扫 —— 注释里往往就写着「不要用 ?. 」这种话，
    不剥的话检查器会抓自己的说明文字（第一版就是这么误报的）。
    `https://` 里的双斜杠要放过，所以只有前面不是冒号时才当行注释。
    """
    out = []
    in_block = False
    for line in src.splitlines():
        buf = []
        i = 0
        n = len(line)
        while i < n:
            two = line[i:i + 2]
            if in_block:
                if two == "*/":
                    in_block = False
                    i += 2
                else:
                    i += 1
                buf.append(" ")
                continue
            if two == "/*":
                in_block = True
                i += 2
                buf.append("  ")
                continue
            if two == "//" and (i == 0 or line[i - 1] != ":"):
                break  # 行注释，后面整行不要了
            buf.append(line[i])
            i += 1
        out.append("".join(buf))
    return out


class TestPythonSyntax37(unittest.TestCase):
    def test_全部能按Python37的规则解析(self):
        """海象 := 、位置形参 / 、f-string 的 = 号这些 3.8 才有的写法会在这里被抓住。"""
        for path in PROD_PY:
            src = read(path)
            try:
                ast.parse(src, filename=str(path), feature_version=PY_BASELINE)
            except SyntaxError as exc:
                self.fail("%s 用了 Python %d.%d 不支持的语法：第 %s 行 %s"
                          % (path.name, PY_BASELINE[0], PY_BASELINE[1],
                             exc.lineno, exc.msg))

    def test_没有用3_8以后才有的标准库API(self):
        """这些语法合法、在 3.12 上跑得好好的，到 3.7 上直接抛异常。"""
        banned = [
            (r"\bfunctools\.cache\b", "functools.cache 是 3.9 才有的，用 lru_cache"),
            (r"\.removeprefix\(", "str.removeprefix 是 3.9 才有的"),
            (r"\.removesuffix\(", "str.removesuffix 是 3.9 才有的"),
            (r"\bmath\.prod\b", "math.prod 是 3.8 才有的"),
            (r"\bzoneinfo\b", "zoneinfo 是 3.9 才有的"),
            (r"\bgraphlib\b", "graphlib 是 3.9 才有的"),
            (r"dirs_exist_ok\s*=", "shutil.copytree(dirs_exist_ok=) 是 3.8 才有的"),
            (r"\bimportlib\.resources\.files\b", "importlib.resources.files 是 3.9 才有的"),
            (r"\bcapture_output\s*=", "subprocess(capture_output=) 是 3.7 才有的，边界值，避开"),
            (r"\bast\.unparse\b", "ast.unparse 是 3.9 才有的"),
        ]
        problems = []
        for path in PROD_PY:
            for lineno, line in enumerate(read(path).splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for pat, why in banned:
                    if re.search(pat, line):
                        problems.append("%s:%d  %s" % (path.name, lineno, why))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_没有内建泛型下标(self):
        """list[str] / dict[str, int] 这种写法要 3.9。

        注解里可以靠 from __future__ import annotations 绕过（不求值），
        但在运行时代码里就是 TypeError。
        """
        problems = []
        for path in PROD_PY:
            src = read(path)
            has_future = "from __future__ import annotations" in src
            for lineno, line in enumerate(src.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if re.search(r"\b(list|dict|set|tuple|type|frozenset)\[", line):
                    # 有 future 声明时，纯注解行是安全的；其它位置一律报出来
                    is_annotation = bool(re.match(r"^[\w\.\s,\*]*:\s*\w+\[", stripped)) \
                        or "->" in line
                    if not (has_future and is_annotation):
                        problems.append("%s:%d  %s" % (path.name, lineno, stripped[:70]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_没有第三方依赖(self):
        """老师那台机器上 pip 装不了东西，只许用标准库。"""
        stdlib_ok = {
            "server", "app", "__future__", "base64", "datetime", "io", "json",
            "math", "mimetypes", "os", "pathlib", "re", "shutil", "socket",
            "sqlite3", "ssl", "struct", "subprocess", "sys", "tempfile",
            "threading", "time", "traceback", "typing", "unittest", "urllib",
            "uuid", "webbrowser", "zipfile", "zlib", "http", "collections",
            "functools", "itertools", "html", "codecs", "hashlib", "random",
            "string", "textwrap", "unicodedata", "binascii", "csv", "glob",
        }
        problems = []
        for path in PROD_PY:
            tree = ast.parse(read(path), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]] if node.level == 0 else []
                else:
                    continue
                for n in names:
                    if n and n not in stdlib_ok:
                        problems.append("%s: import %s" % (path.name, n))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_没有requirements文件(self):
        """存在这个文件本身就是「要装东西」的信号，会把人引到 pip 上去。"""
        for name in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"):
            self.assertFalse((ROOT / name).exists(),
                             "%s 不该存在：这个项目零依赖，不许有装包清单" % name)


class TestJavaScriptBaseline(unittest.TestCase):
    """UOS 20 自带浏览器较老，前端锁 ES2017。"""

    BANNED = [
        (r"\?\.", "可选链 ?. 是 ES2020"),
        (r"\?\?", "空值合并 ?? 是 ES2020"),
        (r"\.flat\s*\(", "Array.flat 是 ES2019"),
        (r"\.flatMap\s*\(", "Array.flatMap 是 ES2019"),
        (r"\.at\s*\(\s*-", "Array.at 是 ES2022"),
        (r"\bstructuredClone\s*\(", "structuredClone 很新"),
        (r"\breplaceAll\s*\(", "String.replaceAll 是 ES2021"),
        (r"\bObject\.hasOwn\s*\(", "Object.hasOwn 是 ES2022"),
        (r"\.\.\.\s*\w+\s*[,}]\s*;?\s*$", None),  # 占位，下面单独处理对象展开
    ]

    def test_没有太新的JS写法(self):
        problems = []
        for path in PROD_JS:
            for lineno, line in enumerate(strip_js_comments(read(path)), 1):
                for pat, why in self.BANNED:
                    if why is None:
                        continue
                    if re.search(pat, line):
                        problems.append("%s:%d  %s\n    %s"
                                        % (path.name, lineno, why, line.strip()[:70]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_没有对象展开语法(self):
        """{...obj} 是 ES2018，老浏览器直接语法错误、整个脚本不执行。"""
        problems = []
        for path in PROD_JS:
            for lineno, line in enumerate(strip_js_comments(read(path)), 1):
                if re.search(r"[{,]\s*\.\.\.[A-Za-z_$]", line):
                    problems.append("%s:%d  %s" % (path.name, lineno, line.strip()[:70]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_检查器自己不会被注释骗到(self):
        """给剥注释这段代码本身来个用例，免得以后改坏了变成假绿灯。"""
        src = "/* 说明：不要用 ?. 和 ?? */\nvar u = 'https://x/y';  // 也别用 .flat(\nvar a = b.c;"
        lines = strip_js_comments(src)
        self.assertEqual(len(lines), 3)
        self.assertNotIn("?.", lines[0])          # 块注释被剥掉
        self.assertIn("https://x/y", lines[1])    # URL 里的双斜杠要留着
        self.assertNotIn(".flat(", lines[1])      # 行尾注释被剥掉
        self.assertIn("b.c", lines[2])            # 正常代码原样保留

    def test_页面不引外部资源(self):
        """老师机器可能没网或被管控，引 CDN 会直接白屏。"""
        problems = []
        for path in PROD_HTML:
            for lineno, line in enumerate(read(path).splitlines(), 1):
                if re.search(r'(src|href)\s*=\s*["\'](https?:)?//', line):
                    problems.append("%s:%d  %s" % (path.name, lineno, line.strip()[:70]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
