# -*- coding: utf-8 -*-
"""multipart/form-data 解析。

标准库的 cgi.FieldStorage 在 3.11 起废弃、3.13 已删除，自己写一个更省心，
反正只需要支持文件上传这一种场景。
"""
from __future__ import annotations

import re

_DISPOSITION_NAME = re.compile(rb'name="([^"]*)"')
_DISPOSITION_FILE = re.compile(rb'filename\*?="?([^";]*)"?')


class Part(object):
    __slots__ = ("name", "filename", "content_type", "data")

    def __init__(self, name, filename, content_type, data):
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")

    @property
    def is_file(self) -> bool:
        return bool(self.filename)


def parse_boundary(content_type: str):
    if not content_type:
        return None
    if "multipart/form-data" not in content_type.lower():
        return None
    m = re.search(r'boundary="?([^";]+)"?', content_type, re.I)
    if not m:
        return None
    return m.group(1).strip().encode("latin-1")


def parse(body: bytes, content_type: str) -> list:
    """返回 Part 列表。不是 multipart 或格式坏掉都返回空列表。"""
    boundary = parse_boundary(content_type)
    if not boundary:
        return []
    delim = b"--" + boundary
    chunks = body.split(delim)
    parts = []
    for chunk in chunks[1:]:
        if chunk[:2] == b"--":  # 结束标记
            break
        if chunk[:2] == b"\r\n":
            chunk = chunk[2:]
        elif chunk[:1] == b"\n":
            chunk = chunk[1:]
        idx = chunk.find(b"\r\n\r\n")
        sep = 4
        if idx < 0:
            idx = chunk.find(b"\n\n")
            sep = 2
            if idx < 0:
                continue
        head = chunk[:idx]
        data = chunk[idx + sep:]
        # 去掉块尾那个分隔用的换行
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]

        name, filename, ctype = "", "", ""
        for line in head.split(b"\r\n" if sep == 4 else b"\n"):
            low = line.lower()
            if low.startswith(b"content-disposition:"):
                m = _DISPOSITION_NAME.search(line)
                if m:
                    name = m.group(1).decode("utf-8", "replace")
                m = _DISPOSITION_FILE.search(line)
                if m:
                    filename = m.group(1).decode("utf-8", "replace")
            elif low.startswith(b"content-type:"):
                ctype = line.split(b":", 1)[1].strip().decode("latin-1")
        parts.append(Part(name, filename, ctype, data))
    return parts
