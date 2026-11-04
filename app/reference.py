"""FS full database loader.

Reads the ``Mixed Database`` sheet by *header name* (the extraction MD's column
letters are one column off from the live sheet, so letters are never trusted),
and caches the slim reference to ``data/reference.json``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

from .parser import parse_description, produce_tokens

SHEET = "Mixed Database"


def _norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _find(headers, *names):
    keys = [_norm_header(h) for h in headers]
    for n in names:
        n = _norm_header(n)
        if n in keys:
            return keys.index(n)
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    v = row[idx]
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("="):  # unevaluated formula: not usable as data
            return None
    return v


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def load_fs_database(xlsx_path: str | Path) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=False)
    if SHEET not in wb.sheetnames:
        raise ValueError(f"Sheet '{SHEET}' not found. Sheets: {wb.sheetnames}")
    ws = wb[SHEET]
    rows = ws.iter_rows(values_only=True)
    headers = list(next(rows))
    col = {
        "tpc_no": _find(headers, "TPC Number"),
        "item_no": _find(headers, "TPC Number(Business central)"),
        "product_name": _find(headers, "Product Name"),
        "item_type": _find(headers, "Item Type"),
        "status": _find(headers, "Item status"),
        "description": _find(headers, "Description"),
        "cut_method": _find(headers, "Cut Method"),
        "cut_size": _find(headers, "Cut Size/ Count"),
        "other": _find(headers, "Other Descriptions"),
        "pack": _find(headers, "Pack Size"),
        "total_lb": _find(headers, "Total lb"),
        "rm1": _find(headers, "Related RM (1)"),
        "rm1_code": _find(headers, "RM1 CODE"),
    }
    if col["item_no"] is None or col["description"] is None:
        raise ValueError("Could not find 'TPC Number(Business central)' / 'Description' headers")

    # Company / Item Code pairs ("Company 1 ", "Item Code 1 ", ..., incl. the 'Compnay 7' typo)
    pairs = []
    for i, h in enumerate(headers):
        nh = _norm_header(h)
        if re.fullmatch(r"(company|compnay)\d+", nh) and i + 1 < len(headers) \
                and _norm_header(headers[i + 1]).startswith("itemcode"):
            pairs.append((i, i + 1))

    items = []
    for excel_row, r in enumerate(rows, start=2):
        item_no = _str(_cell(r, col["item_no"]))
        if not item_no:
            continue
        desc = _str(_cell(r, col["description"])) or _str(_cell(r, col["product_name"]))
        codes = []
        for ci, vi in pairs:
            comp = _str(_cell(r, ci))
            val = _str(_cell(r, vi))
            if comp and val and val != "0":
                for code in re.split(r"[\s,/;]+", val):
                    if code and code != "0":
                        codes.append([comp, code])
        items.append({
            "row": excel_row,
            "tpc_no": _str(_cell(r, col["tpc_no"])),
            "item_no": item_no,
            "description": desc,
            "item_type": _str(_cell(r, col["item_type"])),
            "status": _str(_cell(r, col["status"])),
            "cut_method": _str(_cell(r, col["cut_method"])),
            "cut_size": _str(_cell(r, col["cut_size"])),
            "other": _str(_cell(r, col["other"])),
            "pack": _str(_cell(r, col["pack"])),
            "total_lb": _cell(r, col["total_lb"]) if isinstance(_cell(r, col["total_lb"]), (int, float)) else None,
            "rm1": _str(_cell(r, col["rm1"])),
            "rm1_code": _str(_cell(r, col["rm1_code"])),
            "codes": codes,
        })
    wb.close()
    return {"source_file": Path(xlsx_path).name, "count": len(items), "items": items}


class Reference:
    """In-memory index over the cached FS reference."""

    def __init__(self, data: dict | None):
        self.data = data or {"items": [], "count": 0, "source_file": None}
        self.items = self.data["items"]
        self.by_item_no: dict[str, list[dict]] = {}
        self.by_code: dict[str, list[tuple[str, dict]]] = {}
        for it in self.items:
            p = parse_description(it["description"])
            it["_parsed"] = p
            it["_tokens"] = set(produce_tokens(p.produce))
            self.by_item_no.setdefault(it["item_no"].upper(), []).append(it)
            for comp, code in it["codes"]:
                self.by_code.setdefault(code.lstrip("0"), []).append((comp, it))

    @property
    def loaded(self) -> bool:
        return bool(self.items)

    def get(self, item_no: str) -> dict | None:
        lst = self.by_item_no.get((item_no or "").strip().upper())
        return lst[0] if lst else None

    def by_customer_code(self, code: str, company_hint: str = "imperial") -> list[dict]:
        hits = self.by_code.get((code or "").strip().lstrip("0"), [])
        preferred = [it for comp, it in hits if company_hint in comp.lower()]
        seen, out = set(), []
        for it in preferred or [it for _, it in hits]:
            if it["item_no"] not in seen:
                seen.add(it["item_no"])
                out.append(it)
        return out


def save_reference(data: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def read_reference(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text())
    return None
