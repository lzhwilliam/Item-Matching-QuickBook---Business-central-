"""Give a table back in the layout it was uploaded in ("Original format" download).

* xlsx upload -> the original workbook is the template: every other sheet, title rows,
  header styling, column widths, freeze panes, tables, named ranges and formulas stay.
  Data rows are rewritten in the platform's order; each row takes its styling (and any
  unmapped helper columns) from the Excel row it came from, new rows from the row above.
  Formulas are moved Excel-style (relative references shift) and Excel recalculates on open.
* csv upload  -> same title rows, header and column order.
* no upload (e.g. a generated table) -> a fresh workbook with the platform's formulas and formats.

Platform formats (row["_s"][col]) win over the template for the properties the
platform can edit (see io_excel.STYLE_KEYS); everything else comes from the template.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import namedtuple
from copy import copy
from datetime import datetime
from xml.sax.saxutils import escape

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.workbook.properties import CalcProperties

from .io_excel import formula_text, style_of

NUM_RE = re.compile(r"-?\d+(\.\d+)?")
Snap = namedtuple("Snap", "value style is_number is_date cached")


def effective_layout(sheet: dict | None, columns: list[str], skip=()) -> dict:
    """Excel column (1-based) for each exportable column, and the header row.
    Columns added in the platform go to the right of everything in the original sheet."""
    sheet = sheet or {}
    orig = sheet.get("col_pos") or {}
    col_pos = {c: int(orig[c]) for c in columns if c in orig and c not in skip}
    nxt = max([int(sheet.get("max_col") or 0)] + [int(v) for v in orig.values()] + [0]) + 1
    for c in columns:
        if c not in skip and c not in col_pos:
            col_pos[c] = nxt
            nxt += 1
    return {"col_pos": col_pos, "header_row": int(sheet.get("header_row") or 1)}


def move_formula(f: str, col_letter: str, from_row: int, to_row: int) -> str:
    if not from_row or from_row == to_row:
        return f
    try:
        return Translator(f, origin=f"{col_letter}{from_row}").translate_formula(f"{col_letter}{to_row}")
    except Exception:  # noqa: BLE001 - leave an untranslatable formula as written
        return f


def _snap(cell, cached=None) -> Snap:
    v = cell.value
    num = isinstance(v, (int, float)) and not isinstance(v, bool)
    return Snap(v, copy(cell._style), num, cell.is_date, cached)


NUMERIC = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")


def _cache_formula_values(xlsx: bytes, sheet_path: str, cached: dict) -> bytes:
    """openpyxl writes formula cells with an empty <v/>. Fill in each formula's last
    calculated value so programs that don't recalculate (e.g. Business Central's
    import) still read the number; Excel/OneDrive recalculate on open anyway."""
    if not cached or not sheet_path:
        return xlsx
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    try:
        xml = src.read(sheet_path).decode("utf-8")
    except KeyError:
        return xlsx

    def fill(m):
        ref, attrs, f = m.group(1), m.group(2), m.group(3)
        if ref not in cached:
            return m.group(0)
        v = str(cached[ref])
        attrs = re.sub(r'\s+t="[^"]*"', "", attrs)
        if NUMERIC.fullmatch(v.strip()):
            return f'<c r="{ref}"{attrs}><f>{f}</f><v>{v.strip()}</v>'
        return f'<c r="{ref}"{attrs} t="str"><f>{f}</f><v>{escape(v)}</v>'

    xml = re.sub(r'<c r="([A-Z]+\d+)"([^>]*)><f>(.*?)</f><v\s*/>', fill, xml)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = xml.encode("utf-8") if item.filename == sheet_path else src.read(item.filename)
            dst.writestr(item, data)
    return out.getvalue()


def _typed(v, snap: Snap | None, want: dict):
    v = "" if v is None else str(v)
    if v.strip() == "":
        return None
    if snap is not None and snap.is_date:
        try:
            return datetime.fromisoformat(v.strip())
        except ValueError:
            return v
    numeric_fmt = want.get("numFmt") not in (None, "", "@", "General")
    if ((snap is not None and snap.is_number) or numeric_fmt) and NUM_RE.fullmatch(v.strip()):
        s = v.strip()
        return float(s) if "." in s else int(s)
    return v


def apply_style(cell, want: dict):
    """Make the platform-editable style properties of ``cell`` equal ``want``."""
    have = style_of(cell)
    if any(have.get(k) != want.get(k) for k in ("b", "i", "u", "color")):
        ft = copy(cell.font)
        ft.b = bool(want.get("b"))
        ft.i = bool(want.get("i"))
        ft.u = "single" if want.get("u") else None
        if want.get("color"):
            ft.color = "FF" + want["color"]
        elif have.get("color"):
            ft.color = None
        cell.font = ft
    if have.get("fill") != want.get("fill"):
        cell.fill = PatternFill("solid", fgColor="FF" + want["fill"]) if want.get("fill") else PatternFill(fill_type=None)
    if have.get("numFmt") != want.get("numFmt"):
        cell.number_format = want.get("numFmt") or "General"
    if have.get("align") != want.get("align") or bool(have.get("wrap")) != bool(want.get("wrap")):
        al = copy(cell.alignment) if cell.alignment is not None else Alignment()
        al.horizontal = want.get("align") or None
        al.wrap_text = bool(want.get("wrap")) or None
        cell.alignment = al


def _fit_ref(ref: str, header_row: int, last_row: int) -> str | None:
    c1, r1, c2, r2 = range_boundaries(ref)
    if r1 != header_row:
        return None
    return f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{max(last_row, r1 + 1)}"


def write_xlsx(data: dict, template: bytes | None, skip=(), sheet_title: str = "Sheet1",
               macro: bool = False) -> bytes:
    sheet = data.get("sheet") or {}
    rows = data.get("rows") or []
    lay = effective_layout(sheet, data.get("columns") or [], skip)
    col_pos, hr = lay["col_pos"], lay["header_row"]
    orig_cols = sheet.get("col_pos") or {}

    wsv = None
    if template:
        wb = openpyxl.load_workbook(io.BytesIO(template), keep_vba=macro)
        name = sheet.get("sheet_name")
        ws = wb[name] if name in wb.sheetnames else wb.worksheets[0]
        old_last = max(int(sheet.get("last_row") or hr), hr)
        wbv = openpyxl.load_workbook(io.BytesIO(template), data_only=True)  # Excel's cached results
        wsv = wbv[ws.title] if ws.title in wbv.sheetnames else None
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_title[:31]
        old_last = hr
    max_col = max([ws.max_column] + list(col_pos.values()))
    mapped = set(col_pos.values())

    # headers for columns the template doesn't have
    for c, j in col_pos.items():
        if template and c in orig_cols:
            continue
        h = ws.cell(row=hr, column=j)
        if isinstance(h, MergedCell):
            continue
        h.value = c
        if template and j > 1:
            h._style = copy(ws.cell(row=hr, column=j - 1)._style)
        else:
            h.font = Font(bold=True)

    # snapshot the template's data rows before they're overwritten
    src = {r: {j: _snap(ws.cell(row=r, column=j), wsv.cell(row=r, column=j).value if wsv else None)
               for j in range(1, max_col + 1)}
           for r in range(hr + 1, old_last + 1)} if template else {}
    cached = {}  # "E4" -> last calculated value of the formula written there
    fallback = old_last if old_last in src else None

    prev = None
    for i, row in enumerate(rows):
        out_r = hr + 1 + i
        own = row.get("_row") if row.get("_row") in src else None
        style_r = own or prev or fallback
        if own:
            prev = own
        srow = src.get(style_r, {})
        # template columns the platform doesn't show (helper columns, etc.)
        for j in range(1, max_col + 1):
            if j in mapped or not srow:
                continue
            cell = ws.cell(row=out_r, column=j)
            if isinstance(cell, MergedCell):
                continue
            snap = src[own][j] if own else srow.get(j)
            f = formula_text(snap.value) if snap else None
            if f:
                cell.value = move_formula(f, get_column_letter(j), own or style_r, out_r)
                if own and snap.cached is not None:
                    cached[cell.coordinate] = snap.cached
            else:
                cell.value = snap.value if (own and snap) else None
            if snap:
                cell._style = copy(snap.style)
        fr = row.get("_fr") or out_r
        styles = row.get("_s") or {}
        formulas = row.get("_f") or {}
        for c, j in col_pos.items():
            cell = ws.cell(row=out_r, column=j)
            if isinstance(cell, MergedCell):
                continue
            snap = srow.get(j)
            if snap is not None:
                cell._style = copy(snap.style)
            want = styles.get(c)
            if formulas.get(c):
                cell.value = move_formula(formulas[c], get_column_letter(j), fr, out_r)
                if str(row.get(c, "") or "") != "":
                    cached[cell.coordinate] = row.get(c)
            else:
                cell.value = _typed(row.get(c), snap, want or {})
            if want is not None:  # key present = the platform owns this cell's format
                apply_style(cell, want)

    new_last = hr + len(rows)
    if template and old_last > new_last:
        ws.delete_rows(new_last + 1, old_last - new_last)
    if template:
        for t in ws.tables.values():
            ref = _fit_ref(t.ref, hr, new_last)
            if ref:
                t.ref = ref
                if t.autoFilter is not None:
                    t.autoFilter.ref = ref
        if ws.auto_filter.ref:
            ref = _fit_ref(ws.auto_filter.ref, hr, new_last)
            if ref:
                ws.auto_filter.ref = ref

    widths = sheet.get("col_widths") or {}
    for c, j in col_pos.items():
        letter = get_column_letter(j)
        if c in widths:
            ws.column_dimensions[letter].width = float(widths[c])
        elif not template or c not in orig_cols or sheet.get("autosize"):
            w = max([len(c)] + [len(str(r.get(c, ""))) for r in rows[:300]]) + 2
            ws.column_dimensions[letter].width = min(60, w)
    if not template:
        ws.freeze_panes = ws.cell(row=hr + 1, column=1)
    if wb.calculation is None:  # e.g. files exported by Business Central have no <calcPr>
        wb.calculation = CalcProperties()
    wb.calculation.fullCalcOnLoad = True  # Excel / OneDrive recalculate every formula on open
    buf = io.BytesIO()
    wb.save(buf)
    return _cache_formula_values(buf.getvalue(), ws.path.lstrip("/"), cached)


def write_csv(data: dict, skip=()) -> bytes:
    """CSV in the uploaded file's layout: title rows, header and column order."""
    sheet = data.get("sheet") or {}
    lay = effective_layout(sheet, data.get("columns") or [], skip)
    col_pos = lay["col_pos"]
    header = list(sheet.get("header") or [])
    width = max([len(header)] + list(col_pos.values()))
    header += [""] * (width - len(header))
    for c, j in col_pos.items():
        if c not in (sheet.get("col_pos") or {}):
            header[j - 1] = c
    buf = io.StringIO()
    w = csv.writer(buf)
    for t in sheet.get("title_rows") or []:
        w.writerow(t)
    w.writerow(header)
    for r in data.get("rows") or []:
        line = [""] * width
        for c, j in col_pos.items():
            v = r.get(c, "")
            line[j - 1] = "" if v is None else v  # CSV holds values only; formulas are already calculated
        w.writerow(line)
    return buf.getvalue().encode("utf-8-sig")
