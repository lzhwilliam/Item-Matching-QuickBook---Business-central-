"""Learn from manual edits to the Imperial label (BC) table.

Every changed cell is logged. Then:
* Item No. changed      -> code_override (customer code -> item) + desc_alias (QB description -> item)
                           and the item's FS fields are re-filled unless edited in the same save.
* other learnable field -> field_override for that customer code (valid while the Item No. is the same)
* Cut Size and Method / Item Description token edits that repeat >= threshold times
                        -> global token_rule (e.g. WEDGED -> WEDGE) applied to future generations.
"""
from __future__ import annotations

import difflib

from .matcher import LEARNABLE, build_bc_row, code_key, desc_key
from .reference import Reference

TOKEN_COLUMNS = ("Cut Size and Method", "Item Description")


def diff_rows(old_rows: list[dict], new_rows: list[dict], columns: list[str]) -> dict:
    old = {r.get("_rid"): r for r in old_rows if r.get("_rid")}
    new = {r.get("_rid"): r for r in new_rows if r.get("_rid")}
    changed = []
    for rid, nr in new.items():
        orow = old.get(rid)
        if orow is None:
            continue
        for c in columns:
            a, b = _s(orow.get(c)), _s(nr.get(c))
            if a != b:
                changed.append({"rid": rid, "col": c, "old": a, "new": b})
    added = [rid for rid in new if rid not in old]
    removed = [rid for rid in old if rid not in new]
    return {"changed": changed, "added": added, "removed": removed}


STYLE_NAMES = (("b", "bold"), ("i", "italic"), ("u", "underline"))


def style_desc(st) -> str:
    if not st:
        return "(none)"
    parts = [n for k, n in STYLE_NAMES if st.get(k)]
    for k, label in (("color", "font #"), ("fill", "fill #"), ("numFmt", "format "), ("align", "align ")):
        if st.get(k):
            parts.append(f"{label}{st[k]}")
    if st.get("wrap"):
        parts.append("wrap")
    return ", ".join(parts) or "(none)"


def _same_formula(a: str, a_row, b: str, b_row) -> bool:
    if a == b:
        return True
    from .xlsx_export import move_formula  # local import: avoids a cycle at import time
    return bool(a and b and a_row and b_row) and move_formula(a, "A", a_row, b_row) == b


def diff_meta(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    """Formula (_f) and format (_s) changes per cell. Kept apart from diff_rows so
    learning keeps looking at values only."""
    old = {r.get("_rid"): r for r in old_rows if r.get("_rid")}
    out = []
    for r in new_rows:
        o = old.get(r.get("_rid"))
        if o is None:
            continue
        fa, fb = o.get("_f") or {}, r.get("_f") or {}
        for c in sorted(set(fa) | set(fb)):
            if not _same_formula(fa.get(c, ""), o.get("_fr"), fb.get(c, ""), r.get("_fr")):
                out.append({"rid": r["_rid"], "col": c, "kind": "formula",
                            "old": fa.get(c, "") or "(value)", "new": fb.get(c, "") or "(value)"})
        sa, sb = o.get("_s") or {}, r.get("_s") or {}
        for c in sorted(set(sa) | set(sb)):
            if (sa.get(c) or {}) != (sb.get(c) or {}):
                out.append({"rid": r["_rid"], "col": c, "kind": "format",
                            "old": style_desc(sa.get(c)), "new": style_desc(sb.get(c))})
    return out


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def token_patterns(old: str, new: str) -> list[tuple[str, str]]:
    a, b = old.split(), new.split()
    pats = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op == "replace" and (i2 - i1) == (j2 - j1):
            pats += list(zip(a[i1:i2], b[j1:j2]))
        elif op == "replace":
            pats.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
        elif op == "delete":
            pats.append((" ".join(a[i1:i2]), ""))
    return [(o, n) for o, n in pats if o.lower() != n.lower() or o != n]


def learn_from_edits(store, old_rows, new_rows, qb_rows, ref: Reference, settings: dict,
                     version_no: int) -> tuple[list[dict], list[str]]:
    """Apply learning; returns (possibly refilled rows, human-readable messages)."""
    d = diff_rows(old_rows, new_rows, LEARNABLE)
    if not d["changed"]:
        return new_rows, []
    qb_by_code = {code_key(q.get("Customer Item No")): q for q in qb_rows}
    by_rid = {r["_rid"]: r for r in new_rows if r.get("_rid")}
    edited_cols: dict[str, set] = {}
    for ch in d["changed"]:
        edited_cols.setdefault(ch["rid"], set()).add(ch["col"])

    threshold = int(settings.get("token_rule_threshold", "2") or 2)
    msgs = []
    # Item No. first so refills happen before field overrides are recorded
    changes = sorted(d["changed"], key=lambda c: 0 if c["col"] == "Item No." else 1)
    for ch in changes:
        row = by_rid[ch["rid"]]
        code = _s(row.get("Customer Item Code"))
        ck = code_key(code)
        rkey = code or ch["rid"]
        pattern = ""
        if ch["col"] == "Item No." and ch["new"]:
            it = ref.get(ch["new"])
            if ck:
                store.upsert_rule("code_override", ck, {"item_no": ch["new"].upper(), "customer_code": code,
                                                        "was": ch["old"]}, origin=f"v{version_no}: edit")
                msgs.append(f"Learned: customer code {code} → {ch['new']}")
            qb = qb_by_code.get(ck)
            if qb and qb.get("Description"):
                store.upsert_rule("desc_alias", desc_key(qb["Description"]),
                                  {"item_no": ch["new"].upper(), "description": qb["Description"]},
                                  origin=f"v{version_no}: edit")
            if it:  # refill dependent fields not edited in this same save
                fresh = build_bc_row(qb or {"Description": row.get("QB Description", ""),
                                            "Customer Item No": code, "GTIN": row.get("GTIN", "")},
                                     it, settings)
                for col in ("Item Description", "Cut Size and Method", "No. of Inner PKG", "LB per BAG"):
                    if col not in edited_cols.get(ch["rid"], set()):
                        row[col] = fresh[col]
                row["Matched FS Description"] = fresh["Matched FS Description"]
                row["Item No."] = it["item_no"]
            row["Match Score"] = 100
            row["Match Method"] = "Manual edit (Item No.)"
        elif ch["col"] != "Item No.":
            if ck:
                store.upsert_rule("field_override", f"{ck}|{ch['col']}",
                                  {"column": ch["col"], "value": ch["new"], "was": ch["old"],
                                   "item_no": _s(row.get("Item No.")), "customer_code": code},
                                  origin=f"v{version_no}: edit")
            if ch["col"] in TOKEN_COLUMNS and ch["old"] and ch["new"]:
                for o, n in token_patterns(ch["old"], ch["new"]):
                    pattern = f"{ch['col']}::{o.upper()}=>{n}"
                    store.log_edit("bc", version_no, rkey, ch["col"], ch["old"], ch["new"], pattern)
                    if store.pattern_count(pattern) >= threshold:
                        store.upsert_rule("token_rule", pattern, {"column": ch["col"], "old": o, "new": n},
                                          origin=f"v{version_no}: seen {store.pattern_count(pattern)}×")
                        msgs.append(f"Learned pattern in {ch['col']}: '{o}' → '{n or '(remove)'}'")
                pattern = "logged"
            try:
                score = float(row.get("Match Score") or 0)
            except ValueError:
                score = 0
            row["Match Score"] = max(score, 100)
            if "Manual" not in _s(row.get("Match Method")):
                row["Match Method"] = (_s(row.get("Match Method")) + " + Manual edit").strip(" +")
        if pattern != "logged":
            store.log_edit("bc", version_no, rkey, ch["col"], ch["old"], ch["new"], "")
    return new_rows, msgs
