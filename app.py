#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""试卷批改系统 —— 启动入口。

用法：
    python3 app.py                # 起服务并自动打开浏览器
    python3 app.py --port 9000    # 换端口
    python3 app.py --no-browser   # 不自动开浏览器

零第三方依赖，只用 Python 标准库。详见 CLAUDE.md 和 SPEC.md。
"""
from __future__ import annotations

import sys

MIN_PYTHON = (3, 6)

if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "这个程序需要 Python %d.%d 或更新的版本，当前是 %s。\n"
        % (MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0])
    )
    raise SystemExit(1)

from server import httpd  # noqa: E402  （版本检查必须在导入之前）


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = None
    open_browser = True
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--port", "-p") and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                sys.stderr.write("端口号要是数字。\n")
                return 2
            i += 2
            continue
        if arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                sys.stderr.write("端口号要是数字。\n")
                return 2
            i += 1
            continue
        if arg in ("--no-browser", "-n"):
            open_browser = False
            i += 1
            continue
        if arg in ("--help", "-h"):
            sys.stdout.write(__doc__)
            return 0
        sys.stderr.write("不认识的参数：%s（用 --help 看用法）\n" % arg)
        return 2
    httpd.run(port=port, open_browser=open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
