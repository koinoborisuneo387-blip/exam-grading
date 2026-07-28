# -*- coding: utf-8 -*-
"""从扫描版 PDF 里把每一页的图片原样抠出来。零第三方依赖。

原理：扫描仪产出的 PDF，每一页本质上就是一张 JPEG（或 PNG 式的 Flate 位图）
被套进了 PDF 容器。所以不需要 PDF 渲染引擎，只要找到内嵌的图像流：

  /DCTDecode   → 流数据本身就是完整 JPEG 文件，直接改名存成 .jpg
  /FlateDecode → zlib 解压得到裸像素，用 imgutil 手工封成 PNG
  其它压缩     → 明确报错，告诉老师怎么办（绝不静默失败）

局限（写在 SPEC 里，不是 bug）：
  * 每个 PDF 页只取面积最大的那张图（扫描件就是一页一图）
  * 不支持 CCITTFax（黑白传真压缩）/ JBIG2 / JPEG2000
  * 不支持文字型（born-digital）PDF —— 那种页面里根本没有整页图片
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

from . import imgutil


class PdfExtractError(Exception):
    """抠图失败。message 是直接给老师看的大白话。"""


_OBJ_RE = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+obj\b")
_ROOT_RE = re.compile(rb"/Root\s+(\d{1,10})\s+(\d{1,5})\s+R")

_WS = b"\x00\t\n\x0c\r "


# --------------------------------------------------------------------------
# 极简 PDF 词法：够用就行，不追求完整实现
# --------------------------------------------------------------------------

def _skip_ws(data: bytes, i: int) -> int:
    n = len(data)
    while i < n:
        ch = data[i:i + 1]
        if ch == b"%":  # 注释，吃到行尾
            while i < n and data[i:i + 1] not in (b"\r", b"\n"):
                i += 1
        elif ch in (b"\x00", b"\t", b"\n", b"\x0c", b"\r", b" "):
            i += 1
        else:
            break
    return i


def _skip_literal_string(data: bytes, i: int) -> int:
    """跳过 (...) 字符串，处理转义和嵌套括号。i 指向左括号。"""
    n = len(data)
    depth = 0
    while i < n:
        ch = data[i]
        if ch == 0x5C:  # 反斜杠转义
            i += 2
            continue
        if ch == 0x28:  # (
            depth += 1
        elif ch == 0x29:  # )
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _find_dict_end(data: bytes, i: int) -> int:
    """i 指向 '<<'，返回配对 '>>' 之后的位置。"""
    n = len(data)
    depth = 0
    while i < n:
        two = data[i:i + 2]
        if two == b"<<":
            depth += 1
            i += 2
        elif two == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        elif data[i] == 0x28:  # (
            i = _skip_literal_string(data, i)
        elif data[i] == 0x3C:  # 单个 < ：十六进制字符串
            j = data.find(b">", i)
            i = n if j < 0 else j + 1
        else:
            i += 1
    return n


def _find_bracket_end(data: bytes, i: int) -> int:
    """i 指向 '['，返回配对 ']' 之后的位置。"""
    n = len(data)
    depth = 0
    while i < n:
        ch = data[i]
        if ch == 0x5B:  # [
            depth += 1
        elif ch == 0x5D:  # ]
            depth -= 1
            if depth == 0:
                return i + 1
        elif ch == 0x28:
            i = _skip_literal_string(data, i) - 1
        elif data[i:i + 2] == b"<<":
            i = _find_dict_end(data, i) - 1
        i += 1
    return n


def _key_re(key: str):
    # 名字后面必须跟非名字字符，免得 /Length 匹配到 /Length1
    return re.compile(rb"/" + key.encode("ascii") + rb"(?![A-Za-z0-9])")


def _find_value(d: bytes, key: str):
    m = _key_re(key).search(d)
    if not m:
        return None
    return _skip_ws(d, m.end())


def get_raw(d: bytes, key: str):
    """取某个键的原始值片段（不解析类型）。"""
    i = _find_value(d, key)
    if i is None:
        return None
    if d[i:i + 2] == b"<<":
        return d[i:_find_dict_end(d, i)]
    if d[i:i + 1] == b"[":
        return d[i:_find_bracket_end(d, i)]
    if d[i:i + 1] == b"(":
        return d[i:_skip_literal_string(d, i)]
    if d[i:i + 1] == b"/":
        # 名字型的值，比如 /Filter /DCTDecode —— 这里必须带上开头的斜杠一起返回，
        # 否则下面那段通用扫描会在第一个字符就停住，返回空串（踩过这个坑）
        m = re.match(rb"/[A-Za-z0-9._\-+#]*", d[i:])
        return m.group(0) if m else b""
    j = i
    n = len(d)
    while j < n and d[j:j + 1] not in (b"/", b"[", b"]", b"<", b">", b"(", b")") \
            and d[j] not in _WS:
        j += 1
    return d[i:j]


def get_name(d: bytes, key: str):
    i = _find_value(d, key)
    if i is None or d[i:i + 1] != b"/":
        return None
    m = re.match(rb"/([A-Za-z0-9._\-+#]+)", d[i:])
    return m.group(1).decode("latin-1") if m else None


def get_int(d: bytes, key: str):
    i = _find_value(d, key)
    if i is None:
        return None
    m = re.match(rb"[+-]?\d+", d[i:])
    return int(m.group(0)) if m else None


def get_bool(d: bytes, key: str):
    i = _find_value(d, key)
    if i is None:
        return None
    if d[i:i + 4] == b"true":
        return True
    if d[i:i + 5] == b"false":
        return False
    return None


def get_ref(d: bytes, key: str):
    i = _find_value(d, key)
    if i is None:
        return None
    m = re.match(rb"(\d{1,10})\s+(\d{1,5})\s+R\b", d[i:])
    return int(m.group(1)) if m else None


def get_filters(d: bytes) -> list:
    """/Filter 可能是单个名字，也可能是名字数组。"""
    raw = get_raw(d, "Filter")
    if not raw:
        return []
    return [x.decode("latin-1") for x in re.findall(rb"/([A-Za-z0-9]+)", raw)]


# --------------------------------------------------------------------------
# 对象表
# --------------------------------------------------------------------------

class PdfDoc(object):
    def __init__(self, data: bytes):
        self.data = data
        self.objects = {}   # objnum -> (dict_bytes, stream_bytes|None)
        self._parse_top_level()
        self._expand_object_streams()

    # -- 解析 --------------------------------------------------------------

    def _parse_top_level(self) -> None:
        data = self.data
        for m in _OBJ_RE.finditer(data):
            num = int(m.group(1))
            i = _skip_ws(data, m.end())
            if data[i:i + 2] == b"<<":
                end = _find_dict_end(data, i)
                dic = data[i:end]
            else:
                end = i
                dic = b""
            j = _skip_ws(data, end)
            stream = None
            if data[j:j + 6] == b"stream":
                j += 6
                if data[j:j + 2] == b"\r\n":
                    j += 2
                elif data[j:j + 1] in (b"\n", b"\r"):
                    j += 1
                length = get_int(dic, "Length")
                if length is None:
                    ref = get_ref(dic, "Length")
                    length = self._peek_int_object(ref) if ref else None
                if length is not None and length >= 0 and j + length <= len(data):
                    stream = data[j:j + length]
                    tail = data[j + length:j + length + 20]
                    if b"endstream" not in tail:  # /Length 不可信，退回搜关键字
                        stream = None
                if stream is None:
                    k = data.find(b"endstream", j)
                    stream = data[j:k] if k > 0 else b""
                    if stream.endswith(b"\r\n"):
                        stream = stream[:-2]
                    elif stream.endswith(b"\n") or stream.endswith(b"\r"):
                        stream = stream[:-1]
            # 增量更新的 PDF 里同一个对象号会出现多次，后面的是新版本
            self.objects[num] = (dic, stream)

    def _peek_int_object(self, num):
        """/Length 是间接引用时，直接在原文里找那个对象的数值。"""
        pat = re.compile(rb"(?<![0-9])" + str(num).encode() + rb"\s+\d+\s+obj\s*([+-]?\d+)")
        m = pat.search(self.data)
        return int(m.group(1)) if m else None

    def _expand_object_streams(self) -> None:
        """PDF 1.5+ 会把页面树、目录塞进 /ObjStm 压缩对象流里，得解出来。"""
        for num in list(self.objects.keys()):
            dic, stream = self.objects[num]
            if not stream or get_name(dic, "Type") != "ObjStm":
                continue
            try:
                payload = self._decode_stream(dic, stream)
            except Exception:
                continue
            count = get_int(dic, "N") or 0
            first = get_int(dic, "First") or 0
            header = payload[:first]
            nums = [int(x) for x in re.findall(rb"\d+", header)]
            for k in range(count):
                if 2 * k + 1 >= len(nums):
                    break
                obj_num = nums[2 * k]
                offset = first + nums[2 * k + 1]
                end = len(payload)
                if 2 * k + 3 < len(nums):
                    end = first + nums[2 * k + 3]
                body = payload[offset:end].strip()
                if obj_num not in self.objects:
                    self.objects[obj_num] = (body, None)

    def _decode_stream(self, dic: bytes, stream: bytes) -> bytes:
        """只解 FlateDecode（含预测器）。别的编码留给调用方判断。"""
        out = stream
        for f in get_filters(dic):
            if f in ("FlateDecode", "Fl"):
                try:
                    out = zlib.decompress(out)
                except zlib.error:
                    # 有些 PDF 的流尾部有垃圾字节，用增量解压尽力而为
                    d = zlib.decompressobj()
                    out = d.decompress(out)
            else:
                raise PdfExtractError("流用了不支持的编码：%s" % f)
        parms = get_raw(dic, "DecodeParms") or b""
        predictor = get_int(parms, "Predictor") or 1
        if predictor >= 10:
            colors = get_int(parms, "Colors") or 1
            bpc = get_int(parms, "BitsPerComponent") or 8
            columns = get_int(parms, "Columns") or 1
            out = imgutil.undo_png_predictor(out, colors, bpc, columns)
        return out

    # -- 页面顺序 ----------------------------------------------------------

    def page_objects(self) -> list:
        """按真实页序返回 (页对象字典, 继承下来的 Resources 字典)。"""
        roots = _ROOT_RE.findall(self.data)
        pages = []
        if roots:
            root_num = int(roots[-1][0])  # 增量更新时最后一个 /Root 才是当前的
            cat = self.objects.get(root_num)
            if cat:
                pages_ref = get_ref(cat[0], "Pages")
                if pages_ref is not None:
                    self._walk_pages(pages_ref, b"", pages, set(), 0)
        if pages:
            return pages
        # 页面树没走通（少见），退回按对象号顺序找 /Type /Page
        for num in sorted(self.objects.keys()):
            dic = self.objects[num][0]
            if get_name(dic, "Type") == "Page":
                pages.append((dic, self._resources_of(dic, b"")))
        return pages

    def _walk_pages(self, num, inherited_res, out, seen, depth) -> None:
        if num in seen or depth > 64 or len(out) > 2000:
            return
        seen.add(num)
        entry = self.objects.get(num)
        if not entry:
            return
        dic = entry[0]
        res = self._resources_of(dic, inherited_res)
        ntype = get_name(dic, "Type")
        kids_raw = get_raw(dic, "Kids")
        if ntype == "Page" or (kids_raw is None and ntype != "Pages"):
            out.append((dic, res))
            return
        if kids_raw:
            for m in re.finditer(rb"(\d{1,10})\s+(\d{1,5})\s+R", kids_raw):
                self._walk_pages(int(m.group(1)), res, out, seen, depth + 1)

    def _resources_of(self, dic: bytes, inherited: bytes) -> bytes:
        raw = get_raw(dic, "Resources")
        if raw and raw.startswith(b"<<"):
            return raw
        ref = get_ref(dic, "Resources")
        if ref is not None:
            entry = self.objects.get(ref)
            if entry:
                return entry[0]
        return inherited

    def image_refs_of_page(self, resources: bytes) -> list:
        """页面 /Resources /XObject 里所有图像对象号。"""
        xo = get_raw(resources, "XObject")
        if xo is None:
            ref = get_ref(resources, "XObject")
            if ref is None:
                return []
            entry = self.objects.get(ref)
            xo = entry[0] if entry else b""
        refs = []
        for m in re.finditer(rb"/[A-Za-z0-9._\-+#]+\s+(\d{1,10})\s+(\d{1,5})\s+R", xo or b""):
            num = int(m.group(1))
            entry = self.objects.get(num)
            if entry and get_name(entry[0], "Subtype") == "Image":
                refs.append(num)
        return refs

    def all_image_refs(self) -> list:
        return [
            num for num in sorted(self.objects.keys())
            if get_name(self.objects[num][0], "Subtype") == "Image"
        ]


# --------------------------------------------------------------------------
# 单张图片的解码
# --------------------------------------------------------------------------

_UNSUPPORTED_HINT = {
    "CCITTFaxDecode": "这个 PDF 是黑白传真压缩格式（CCITT）",
    "JBIG2Decode": "这个 PDF 用了 JBIG2 黑白压缩",
    "JPXDecode": "这个 PDF 用了 JPEG2000 压缩",
    "LZWDecode": "这个 PDF 用了 LZW 压缩",
    "RunLengthDecode": "这个 PDF 用了 RunLength 压缩",
}

_RESCAN_HINT = ("，本系统读不了。请在扫描仪设置里把颜色改成「彩色」或「灰度」"
                "重新扫一次，或者直接用手机拍照导入。")


def _colorspace_info(doc: PdfDoc, dic: bytes):
    """返回 (通道数, PNG colortype, 调色板bytes|None)。认不出来抛 PdfExtractError。"""
    raw = get_raw(dic, "ColorSpace")
    if raw is None:
        ref = get_ref(dic, "ColorSpace")
        if ref is not None:
            entry = doc.objects.get(ref)
            raw = entry[0] if entry else None
    if get_bool(dic, "ImageMask"):
        return 1, 0, None
    if raw is None:
        return 1, 0, None

    text = raw if isinstance(raw, bytes) else b""

    if text.startswith(b"["):
        if b"/Indexed" in text or b"/I " in text:
            palette = _read_palette(doc, text)
            return 1, 3, palette
        if b"/ICCBased" in text:
            m = re.search(rb"(\d{1,10})\s+(\d{1,5})\s+R", text)
            n_comp = 3
            if m:
                entry = doc.objects.get(int(m.group(1)))
                if entry:
                    n_comp = get_int(entry[0], "N") or 3
            if n_comp == 1:
                return 1, 0, None
            if n_comp == 3:
                return 3, 2, None
            raise PdfExtractError("这个 PDF 的图片是 CMYK 色彩" + _RESCAN_HINT)
        if b"/DeviceN" in text or b"/Separation" in text:
            raise PdfExtractError("这个 PDF 的图片用了专色通道" + _RESCAN_HINT)

    name = None
    m = re.match(rb"/([A-Za-z0-9]+)", text)
    if m:
        name = m.group(1).decode("latin-1")
    if name in ("DeviceGray", "CalGray", "G"):
        return 1, 0, None
    if name in ("DeviceRGB", "CalRGB", "RGB", "Lab"):
        return 3, 2, None
    if name in ("DeviceCMYK", "CMYK"):
        raise PdfExtractError("这个 PDF 的图片是 CMYK 色彩" + _RESCAN_HINT)
    return 1, 0, None


def _read_palette(doc: PdfDoc, text: bytes):
    """Indexed 色彩空间：[/Indexed base hival lookup]，lookup 可能是串也可能是流。"""
    base_gray = b"/DeviceGray" in text or b"/CalGray" in text
    table = None
    m = re.search(rb"<([0-9A-Fa-f\s]+)>", text)
    if m:
        hexs = re.sub(rb"\s", b"", m.group(1))
        if len(hexs) % 2:
            hexs += b"0"
        table = bytes.fromhex(hexs.decode("ascii"))
    if table is None:
        m = re.search(rb"\((.*)\)\s*\]", text, re.S)
        if m:
            table = _unescape_literal(m.group(1))
    if table is None:
        m = re.search(rb"(\d{1,10})\s+(\d{1,5})\s+R\s*\]", text)
        if m:
            entry = doc.objects.get(int(m.group(1)))
            if entry and entry[1] is not None:
                try:
                    table = doc._decode_stream(entry[0], entry[1])
                except Exception:
                    table = None
    if not table:
        raise PdfExtractError("这个 PDF 的调色板读不出来" + _RESCAN_HINT)
    if base_gray:  # 灰度调色板补成 RGB 三元组
        table = b"".join(bytes((v, v, v)) for v in table)
    if len(table) % 3:
        table = table + b"\x00" * (3 - len(table) % 3)
    return table


def _unescape_literal(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == 0x5C and i + 1 < n:
            nxt = raw[i + 1:i + 2]
            mapping = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            m = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 0xFF)
                i += 1 + len(m.group(0))
                continue
            out.append(raw[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return bytes(out)


def decode_image(doc: PdfDoc, num: int, out_path_noext: Path) -> dict:
    """把一个图像对象写成文件，返回 {path, width, height}。"""
    entry = doc.objects.get(num)
    if not entry or entry[1] is None:
        raise PdfExtractError("这一页的图片数据是空的")
    dic, stream = entry
    width = get_int(dic, "Width") or 0
    height = get_int(dic, "Height") or 0
    filters = get_filters(dic)

    for f in filters:
        if f in _UNSUPPORTED_HINT and f != "LZWDecode":
            raise PdfExtractError(_UNSUPPORTED_HINT[f] + _RESCAN_HINT)

    if "DCTDecode" in filters or "DCT" in filters:
        payload = stream
        # 极少数情况会先 Flate 再 DCT，先把 Flate 剥掉
        idx = filters.index("DCTDecode" if "DCTDecode" in filters else "DCT")
        for f in filters[:idx]:
            if f in ("FlateDecode", "Fl"):
                payload = zlib.decompress(payload)
            else:
                raise PdfExtractError(_UNSUPPORTED_HINT.get(f, "这个 PDF 用了不支持的压缩") + _RESCAN_HINT)
        path = out_path_noext.with_suffix(".jpg")
        path.write_bytes(payload)
        size = imgutil.jpeg_size(payload) or (width, height)
        return {"path": path, "width": size[0], "height": size[1]}

    if "LZWDecode" in filters:
        raise PdfExtractError(_UNSUPPORTED_HINT["LZWDecode"] + _RESCAN_HINT)

    # 剩下的就是 Flate 或者根本没压缩的裸位图
    samples = doc._decode_stream(dic, stream)
    bpc = get_int(dic, "BitsPerComponent") or (1 if get_bool(dic, "ImageMask") else 8)
    _, colortype, palette = _colorspace_info(doc, dic)
    if not width or not height:
        raise PdfExtractError("这一页的图片没有宽高信息")
    if bpc not in (1, 2, 4, 8, 16):
        raise PdfExtractError("这个 PDF 的图片位深不常见（%s 位）" % bpc + _RESCAN_HINT)
    if bpc == 16:
        # PNG 支持 16 位，但 PDF 的字节序和 PNG 一致，直接透传
        pass
    decode_arr = get_raw(dic, "Decode") or b""
    invert = bool(re.match(rb"\[\s*1\s+0", decode_arr))
    if get_bool(dic, "ImageMask") and not decode_arr:
        invert = True  # ImageMask 默认 0=涂墨，PNG 里 0=黑，反过来才对

    path = out_path_noext.with_suffix(".png")
    imgutil.write_png(path, width, height, bpc, colortype, samples,
                      palette=palette, invert_gray=invert)
    return {"path": path, "width": width, "height": height}


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------

def extract_pages(pdf_bytes: bytes, out_dir, prefix: str) -> list:
    """把 PDF 每页的主图抠出来存到 out_dir。

    返回 [{page_no, path(Path), width, height}, ...]，一页一项。
    完全抠不出来时抛 PdfExtractError，message 是给老师看的大白话。
    """
    if not pdf_bytes[:5].startswith(b"%PDF"):
        raise PdfExtractError("这个文件不是 PDF。")
    out_dir = Path(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = PdfDoc(pdf_bytes)
    except PdfExtractError:
        raise
    except Exception as exc:  # 结构坏掉的 PDF
        raise PdfExtractError("这个 PDF 解析不了（%s）。请换一个文件，或用手机拍照导入。"
                              % exc.__class__.__name__)

    pages = doc.page_objects()
    results = []
    errors = []       # 页面里压根没有整页图片
    decode_errors = []  # 有图但解不出来（这类错误自带解决办法，优先报给老师）

    def pick_largest(refs):
        best, best_area = None, -1
        for r in refs:
            dic = doc.objects[r][0]
            area = (get_int(dic, "Width") or 0) * (get_int(dic, "Height") or 0)
            if area > best_area:
                best, best_area = r, area
        return best, best_area

    if pages:
        for idx, (_page_dic, resources) in enumerate(pages):
            refs = doc.image_refs_of_page(resources)
            if not refs:
                errors.append("第 %d 页里没有整页图片" % (idx + 1))
                continue
            ref, area = pick_largest(refs)
            if area < 10000:  # 100x100 以下基本是水印或logo，不是答卷
                errors.append("第 %d 页里只有很小的图片" % (idx + 1))
                continue
            try:
                info = decode_image(doc, ref, out_dir / ("%s_p%03d" % (prefix, idx + 1)))
            except PdfExtractError as exc:
                decode_errors.append(str(exc))
                continue
            info["page_no"] = len(results) + 1
            results.append(info)
    else:
        for idx, ref in enumerate(doc.all_image_refs()):
            try:
                info = decode_image(doc, ref, out_dir / ("%s_p%03d" % (prefix, idx + 1)))
            except PdfExtractError as exc:
                decode_errors.append(str(exc))
                continue
            info["page_no"] = len(results) + 1
            results.append(info)

    if not results:
        if decode_errors:
            # 这类错误本身就带了「该怎么办」，直接原样告诉老师，别套一层无关的解释
            raise PdfExtractError(decode_errors[0])
        detail = errors[0] if errors else "里面找不到扫描图片"
        raise PdfExtractError(
            "这个 PDF 里没有能用的答卷图片（%s）。\n"
            "常见原因：它不是扫描件，而是电脑直接生成的文字版 PDF。\n"
            "解决办法：用手机把卷子拍成照片导入，或者在扫描仪里按「彩色/灰度」重扫一次。"
            % detail
        )
    return results
