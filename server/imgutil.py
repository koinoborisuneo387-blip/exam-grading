# -*- coding: utf-8 -*-
"""图片工具：读尺寸、写 PNG。全部标准库实现。

老师机器上装不了 Pillow（ARM 上没有预编译轮子），所以这些都得手写。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# colortype -> 每像素的样本数
_SAMPLES_PER_PIXEL = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# 这些 JPEG 标记段后面跟的是帧头，里面有宽高
_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def png_size(data: bytes):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def jpeg_size(data: bytes):
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        # 填充字节和无参数标记，直接跳过
        if marker in (0xFF, 0x01) or 0xD0 <= marker <= 0xD9:
            continue
        if i + 2 > n:
            break
        seg_len = struct.unpack(">H", data[i:i + 2])[0]
        if marker in _SOF_MARKERS:
            if i + 7 > n:
                break
            height, width = struct.unpack(">HH", data[i + 3:i + 7])
            return int(width), int(height)
        i += seg_len
    return None


def image_size(path) -> tuple:
    """返回 (宽, 高)，认不出来返回 (0, 0)。只读文件头，不整个读进内存。"""
    try:
        with open(str(path), "rb") as f:
            head = f.read(65536)
    except OSError:
        return (0, 0)
    size = png_size(head)
    if size:
        return size
    size = jpeg_size(head)
    if size:
        return size
    # 头 64KB 里没找到 SOF，把整个文件读进来再试一次（大图的 EXIF 可能很长）
    try:
        with open(str(path), "rb") as f:
            whole = f.read()
    except OSError:
        return (0, 0)
    return jpeg_size(whole) or (0, 0)


def sniff_ext(data: bytes) -> str:
    """按文件头判断图片类型，别信用户传上来的扩展名。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:2] == b"BM":
        return ".bmp"
    return ""


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def row_bytes(width: int, bitdepth: int, colortype: int) -> int:
    spp = _SAMPLES_PER_PIXEL.get(colortype, 1)
    bits = width * spp * bitdepth
    return (bits + 7) // 8


def write_png(path, width, height, bitdepth, colortype, samples,
              palette=None, invert_gray=False) -> None:
    """把裸像素数据封成 PNG 文件。

    samples: 逐行拼接的原始样本（不带 PNG 的行首过滤字节，本函数负责补 0）。
    palette: colortype=3 时的调色板，bytes，每 3 字节一个 RGB。
    invert_gray: PDF 里 1bit 灰度常常是「1=黑」，和 PNG 相反，需要按位取反。
    """
    if colortype not in _SAMPLES_PER_PIXEL:
        raise ValueError("不支持的 PNG 颜色类型: %s" % colortype)
    stride = row_bytes(width, bitdepth, colortype)
    need = stride * height
    if len(samples) < need:
        samples = samples + b"\x00" * (need - len(samples))
    elif len(samples) > need:
        samples = samples[:need]

    if invert_gray:
        samples = bytes(b ^ 0xFF for b in samples)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # 过滤方式 None
        raw += samples[y * stride:(y + 1) * stride]

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    ihdr = struct.pack(">IIBBBBB", width, height, bitdepth, colortype, 0, 0, 0)
    out += _chunk(b"IHDR", ihdr)
    if colortype == 3:
        if not palette:
            raise ValueError("调色板图缺少 PLTE 数据")
        out += _chunk(b"PLTE", bytes(palette))
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += _chunk(b"IEND", b"")
    Path(str(path)).write_bytes(bytes(out))


def undo_png_predictor(data: bytes, colors: int, bpc: int, columns: int) -> bytes:
    """还原 PNG 预测器（PDF 的 /Predictor >= 10 会用）。

    每行开头多一个字节表示过滤方式，需要按 PNG 规则反算回原始样本。
    """
    bpp = max(1, (colors * bpc + 7) // 8)
    stride = (columns * colors * bpc + 7) // 8
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    n = len(data)
    while pos + 1 <= n - 1:
        ft = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        if len(line) < stride:
            line += bytearray(stride - len(line))
        pos += stride
        if ft == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        out += line
        prev = line
    return bytes(out)
