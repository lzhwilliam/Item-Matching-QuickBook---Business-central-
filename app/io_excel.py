"""Upload parsing and download building (CSV / XLSX / BC layout)."""
from __future__ import annotations

import csv
import io
import re

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

BC_TITLE = ["IMPERIAL LABEL", "Imperial Item Label Master", "50024"]


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def is_xlsx(filename: str) -> bool:
    return filename.lower().endswith((".xlsx", ".xlsm"))


def _read_grid(filename: str, content: bytes) -> list[list[str]]:
    if is_xlsx(filename):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [[_cell_str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        return grid
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text))]


def read_upload(filename: str, content: bytes, known_columns: list[str]) -> tuple[list[str], list[dict]]:
    """Return (columns, rows). Finds the header row (BC exports have 2 title rows),
    keeps codes as text, drops blank/unnamed columns."""
    ordered, rows, _ = _parse_grid(_read_grid(filename, content), known_columns)
    return ordered, rows


def _parse_grid(grid: list[list[str]], known_columns: list[str]):
    """-> (ordered columns, rows, positions) where positions holds the header index,
    the kept (grid column index, column) pairs and each row's grid row index."""
    known = {_norm(c): c for c in known_columns}
    header_idx = 0
    best = -1
    for i, r in enumerate(grid[:15]):
        hits = sum(1 for c in r if _norm(c) in known)
        if hits > best:
            best, header_idx = hits, i
    headers = grid[header_idx] if grid else []
    cols, keep = [], []
    for j, h in enumerate(headers):
        h = h.strip()
        if not h:
            continue
        canon = known.get(_norm(h), h)
        if canon in cols or canon in ("Confidence", "Errors"):
            continue
        cols.append(canon)
        keep.append((j, canon))
    rows, src = [], []
    for i, r in enumerate(grid[header_idx + 1:], start=header_idx + 1):
        if not any(x.strip() for x in r):
            continue
        row = {c: (r[j] if j < len(r) else "") for j, c in keep}
        rows.append(row)
        src.append(i)
    for row in rows:
        _fix_codes(row)
    # known columns first, in canonical order, then any extras
    ordered = [c for c in known_columns if c in cols] + [c for c in cols if c not in known_columns]
    return ordered, rows, {"header_idx": header_idx, "keep": keep, "src": src, "headers": headers}


# ------------------------------------------------------------------ formulas & formatting
# A cell's platform format: b/i/u (1), color/fill ("RRGGBB"), numFmt, align (left|center|right), wrap (1)
STYLE_KEYS = ("b", "i", "u", "color", "fill", "numFmt", "align", "wrap")


def _rgb(color) -> str | None:
    try:
        if color is not None and color.type == "rgb" and isinstance(color.rgb, str) and len(color.rgb) >= 6:
            return color.rgb[-6:].upper()
    except AttributeError:
        pass
    return None  # theme / indexed colours are left to the original file


def style_of(cell) -> dict:
    """The platform-editable part of an openpyxl cell's style (only non-defaults)."""
    s = {}
    f = cell.font
    if f is not None:
        if f.b:
            s["b"] = 1
        if f.i:
            s["i"] = 1
        if f.u:
            s["u"] = 1
        c = _rgb(f.color)
        if c and c != "000000":
            s["color"] = c
    fl = cell.fill
    if fl is not None and fl.fill_type == "solid":
        c = _rgb(fl.fgColor)
        if c:
            s["fill"] = c
    if cell.number_format and cell.number_format != "General":
        s["numFmt"] = cell.number_format
    a = cell.alignment
    if a is not None:
        if a.horizontal in ("left", "center", "right"):
            s["align"] = a.horizontal
        if a.wrap_text:
            s["wrap"] = 1
    return s


def formula_text(v) -> str | None:
    if isinstance(v, str) and v.startswith("=") and len(v) > 1:
        return v
    text = getattr(v, "text", None)  # ArrayFormula
    if isinstance(text, str) and text.startswith("="):
        return text
    return None


def read_upload_rich(filename: str, content: bytes, known_columns: list[str]) -> tuple[list[str], list[dict], dict]:
    """Like read_upload, plus what's needed to give the file back in its own layout:
    per-row formulas (_f, anchored at Excel row _fr) and formats (_s), and a
    ``sheet`` description (header row, column positions, widths, ...)."""
    grid = _read_grid(filename, content)
    ordered, rows, pos = _parse_grid(grid, known_columns)
    header_row = pos["header_idx"] + 1
    col_pos = {c: j + 1 for j, c in pos["keep"]}
    sheet = {"kind": "xlsx" if is_xlsx(filename) else "csv", "header_row": header_row, "col_pos": col_pos,
             "last_row": (pos["src"][-1] + 1) if pos["src"] else header_row, "file_name": filename,
             "max_col": max([len(r) for r in grid] + [0])}
    for r, i in zip(rows, pos["src"]):
        r["_row"] = i + 1  # original Excel/CSV row; dropped after the template layout is applied
    if not is_xlsx(filename):
        sheet["title_rows"] = grid[:pos["header_idx"]]
        sheet["header"] = list(pos["headers"])
        for r in rows:
            r.pop("_row", None)
        return ordered, rows, sheet

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    ws = wb.worksheets[0]
    sheet["sheet_name"] = ws.title
    widths = {}
    for c, j in col_pos.items():
        dim = ws.column_dimensions.get(get_column_letter(j))
        if dim is not None and dim.width:
            widths[c] = round(float(dim.width), 2)
    sheet["col_widths"] = widths
    for r in rows:
        er = r["_row"]
        fs, ss = {}, {}
        for c, j in col_pos.items():
            cell = ws.cell(row=er, column=j)
            f = formula_text(cell.value)
            if f:
                fs[c] = f
            st = style_of(cell)
            if st:
                ss[c] = st
        if fs:
            r["_f"], r["_fr"] = fs, er
        if ss:
            r["_s"] = ss
    wb.close()
    return ordered, rows, sheet


def _fix_codes(row: dict):
    """Excel strips leading zeros from numeric codes; restore them."""
    g = row.get("GTIN")
    if g and re.fullmatch(r"\d{1,13}", g):
        row["GTIN"] = g.zfill(14)
    for c in ("Customer Item No", "Customer Item Code"):
        v = row.get(c)
        if v and re.fullmatch(r"\d{1,6}", v):
            row[c] = v.zfill(7)  # Sysco SUPC codes are 7 digits


def to_csv(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([r.get(c, "") for c in columns])
    return buf.getvalue().encode("utf-8-sig")


def heat_fill(conf: float | None) -> PatternFill | None:
    if conf is None or conf >= 100:
        return None
    t = max(0.0, min(1.0, (100 - conf) / 60))  # 100 -> none, 40 or lower -> full red
    g = int(255 - t * (255 - 90))
    b = int(255 - t * (255 - 90))
    return PatternFill("solid", fgColor=f"FF{g:02X}{b:02X}")


def to_xlsx(columns: list[str], rows: list[dict], sheet: str, analysis: list[dict] | None = None,
            bc_layout: bool = False) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    start = 1
    if bc_layout:
        ws.append(BC_TITLE)
        ws.append([])
        start = 3
    out_cols = list(columns)
    if analysis is not None and not bc_layout:
        out_cols += ["Confidence", "Errors"]
    ws.append(out_cols)
    for c in ws[start]:
        c.font = Font(bold=True)
    red_font = Font(color="FFC00000", bold=True)
    for i, r in enumerate(rows):
        vals = [r.get(c, "") for c in columns]
        a = analysis[i] if analysis else None
        if a is not None and not bc_layout:
            vals += [a["confidence"], "; ".join(f"{e['col']}: {e['msg']}" for e in a["errors"])]
        ws.append(vals)
        rr = start + 1 + i
        for j in range(1, len(out_cols) + 1):
            ws.cell(row=rr, column=j).number_format = "@"
        if a is not None:
            fill = heat_fill(a["confidence"])
            if fill:
                for j in range(1, len(out_cols) + 1):
                    ws.cell(row=rr, column=j).fill = fill
            for e in a["errors"]:
                if e["col"] in columns:
                    cell = ws.cell(row=rr, column=columns.index(e["col"]) + 1)
                    if e["sev"] == "error":
                        cell.font = red_font
                    prev = cell.comment.text + "\n" if cell.comment else ""
                    cell.comment = Comment(prev + f"[{e['sev']}] {e['msg']}", "Matcher")
    for j, c in enumerate(out_cols, start=1):
        width = max([len(str(c))] + [len(str(r.get(c, ""))) for r in rows[:300]]) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(60, width)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
