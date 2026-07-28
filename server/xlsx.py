# -*- coding: utf-8 -*-
"""手写 .xlsx 导出。零依赖（openpyxl 在老师那台 ARM 机器上装不了）。

xlsx 就是一个 zip 包 + 几个 XML。用 inlineStr 存文本可以省掉 sharedStrings.xml。
WPS、LibreOffice、Excel 都能正常打开。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

_ILLEGAL_XML = re.compile(
    u"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f﷐-﷟￾￿]"
)
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheet_overrides}
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

# 两种单元格格式：0=普通，1=加粗（表头用）
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="宋体"/></font>
<font><b/><sz val="11"/><name val="宋体"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
</styleSheet>"""


def esc(text) -> str:
    s = "" if text is None else str(text)
    s = _ILLEGAL_XML.sub("", s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def col_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA"""
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _is_number(value) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))


def _cell_xml(ref: str, value, style: int) -> str:
    attr = ' s="%d"' % style if style else ""
    if value is None or value == "":
        return '<c r="%s"%s/>' % (ref, attr)
    if _is_number(value):
        num = value
        if isinstance(num, float):
            num = round(num, 4)
            if num == int(num):
                num = int(num)
        return '<c r="%s"%s><v>%s</v></c>' % (ref, attr, num)
    return ('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (ref, attr, esc(value)))


def _sheet_xml(rows, widths, header_rows: int) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if widths:
        cols = ['<cols>']
        for i, w in enumerate(widths):
            if w:
                cols.append('<col min="%d" max="%d" width="%s" customWidth="1"/>'
                            % (i + 1, i + 1, w))
        cols.append('</cols>')
        if len(cols) > 2:
            parts.append("".join(cols))
    parts.append("<sheetData>")
    for r, row in enumerate(rows):
        style = 1 if r < header_rows else 0
        cells = "".join(
            _cell_xml("%s%d" % (col_letter(c), r + 1), val, style)
            for c, val in enumerate(row)
        )
        parts.append('<row r="%d">%s</row>' % (r + 1, cells))
    parts.append("</sheetData>")
    parts.append("</worksheet>")
    return "".join(parts)


def safe_sheet_name(name: str, index: int) -> str:
    s = _BAD_SHEET_CHARS.sub("", str(name or "")).strip()
    s = s[:31]
    return s or ("Sheet%d" % (index + 1))


def write_xlsx(path, sheets) -> Path:
    """sheets: [{"name": 表名, "rows": [[...], ...], "widths": [宽,...], "header_rows": 1}]"""
    path = Path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_xlsx(sheets))
    return path


def build_xlsx(sheets) -> bytes:
    """同 write_xlsx，但直接返回字节流（给 HTTP 下载用，不落盘）。"""
    if not sheets:
        sheets = [{"name": "Sheet1", "rows": []}]

    names = []
    for i, sheet in enumerate(sheets):
        base = safe_sheet_name(sheet.get("name"), i)
        name, k = base, 1
        while name in names:  # 表名不能重复
            suffix = "_%d" % k
            name = base[:31 - len(suffix)] + suffix
            k += 1
        names.append(name)

    overrides = "\n".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        % (i + 1) for i in range(len(sheets))
    )
    wb_sheets = "".join(
        '<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (esc(names[i]), i + 1, i + 1)
        for i in range(len(sheets))
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>%s</sheets></workbook>' % wb_sheets
    )
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(len(sheets)):
        rels.append(
            '<Relationship Id="rId%d" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
        )
    rels.append(
        '<Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    rels.append("</Relationships>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheet_overrides=overrides))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets):
            z.writestr(
                "xl/worksheets/sheet%d.xml" % (i + 1),
                _sheet_xml(sheet.get("rows") or [],
                           sheet.get("widths") or [],
                           int(sheet.get("header_rows") or 0)),
            )
    return buf.getvalue()


def to_csv_bytes(rows) -> bytes:
    """UTF-8 带 BOM 的 CSV —— 不带 BOM 的话 WPS 打开是乱码。"""
    out = []
    for row in rows:
        cells = []
        for value in row:
            s = "" if value is None else str(value)
            if any(c in s for c in (",", '"', "\n", "\r")):
                s = '"' + s.replace('"', '""') + '"'
            cells.append(s)
        out.append(",".join(cells))
    return b"\xef\xbb\xbf" + "\r\n".join(out).encode("utf-8")
