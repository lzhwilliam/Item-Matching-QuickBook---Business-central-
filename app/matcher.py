"""QuickBooks -> BC Imperial label matching, confidence scoring and error detection."""
from __future__ import annotations

import difflib
import re
import uuid

from .parser import (
    SIZE_RE, canon_method, format_cut_size_and_method, norm_size, normalize_quotes,
    pack_to_bc, parse_bc_pack, parse_description, produce_tokens,
)
from .reference import Reference

QB_COLUMNS = ["Item", "Description", "Customer Item No", "Case Weight", "GTIN", "Raw Material"]
BC_COLUMNS = [
    "Entry No.", "Customer No.", "Customer Name", "Item No.", "Item Description",
    "Cut Size and Method", "Customer Item Code", "No. of Inner PKG", "LB per BAG", "GTIN", "No. of copy",
]
# Extra working columns kept beside the BC columns (not part of the BC export).
BC_META = ["Match Score", "Match Method", "Matched FS Description", "QB Description"]
# Columns whose manual edits are learned.
LEARNABLE = ["Item No.", "Item Description", "Cut Size and Method", "No. of Inner PKG", "LB per BAG",
             "GTIN", "Customer No.", "Customer Name", "No. of copy"]

ERROR_PENALTY = {"error": 30, "warning": 10}


def new_rid() -> str:
    return uuid.uuid4().hex[:10]


def code_key(code) -> str:
    return re.sub(r"\s+", "", str(code or "")).lstrip("0")


def desc_key(desc) -> str:
    return " ".join(normalize_quotes(desc).lower().split())


# --------------------------------------------------------------------------- scoring
def _cmp(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.5
    return 1.0 if a == b else 0.0


def ref_parts(it: dict) -> dict:
    p = it["_parsed"]
    return {
        "method": canon_method(it.get("cut_method") or p.cut_method),
        "size": norm_size(it.get("cut_size") or p.cut_size),
        "inner": p.pack_inner, "per": p.pack_per, "total": p.total_lb,
        "tokens": it["_tokens"],
    }


def similarity(q, it: dict) -> dict:
    r = ref_parts(it)
    qt = set(q.tokens)
    inter = len(qt & r["tokens"])
    produce = inter / max(len(qt), len(r["tokens"]), 1)
    method = _cmp(canon_method(q.cut_method), r["method"])
    size = _cmp(norm_size(q.cut_size), r["size"])
    if q.pack_inner is not None and q.pack_inner == r["inner"] and q.pack_per == r["per"]:
        pack = 1.0
    elif q.total_lb is not None and q.total_lb == r["total"]:
        pack = 0.6
    else:
        pack = 0.0
    text = difflib.SequenceMatcher(None, desc_key(q.raw), desc_key(it["description"])).ratio()
    score = 0.45 * produce + 0.2 * method + 0.2 * size + 0.1 * pack + 0.05 * text
    return {"score": score, "produce": produce, "method": method, "size": size, "pack": pack}


def best_description_match(q, ref: Reference, top: int = 3):
    scored = []
    for it in ref.items:
        s = similarity(q, it)
        if s["produce"] < 0.5:
            continue
        bonus = 0.01 if it.get("status", "").lower() == "ready" else 0
        scored.append((s["score"] + bonus, s, it))
    scored.sort(key=lambda x: -x[0])
    return scored[:top]


def verify_code_match(q, it: dict) -> tuple[int, list[str]]:
    r = ref_parts(it)
    score, notes = 95, []
    ok = True
    if q.cut_method and r["method"] and canon_method(q.cut_method) != r["method"]:
        score -= 20; ok = False; notes.append("cut method differs")
    if q.cut_size and r["size"] and norm_size(q.cut_size) != r["size"]:
        score -= 25; ok = False; notes.append("cut size differs")
    if q.total_lb is not None and r["total"] is not None and q.total_lb != r["total"]:
        score -= 15; ok = False; notes.append("pack differs")
    if ok:
        score = 100
    return score, notes


# --------------------------------------------------------------------------- rules
def apply_token_rules(value: str, column: str, token_rules: list[dict], hits: dict) -> str:
    for rule in token_rules:
        v = rule["value"]
        if v.get("column") != column or not value:
            continue
        rx = re.compile(r"(?<!\S)" + re.escape(v["old"]) + r"(?!\S)", re.I)
        if rx.search(value):
            value = rx.sub(v["new"], value)
            value = re.sub(r"\s+", " ", value).strip()
            hits[rule["id"]] = hits.get(rule["id"], 0) + 1
    return value


# --------------------------------------------------------------------------- generate
def build_bc_row(qb: dict, it: dict | None, settings: dict) -> dict:
    q = parse_description(qb.get("Description", ""))
    row = {c: "" for c in BC_COLUMNS + BC_META}
    row["Customer No."] = settings.get("customer_no", "")
    row["Customer Name"] = settings.get("customer_name", "")
    row["Customer Item Code"] = str(qb.get("Customer Item No", "")).strip()
    row["GTIN"] = str(qb.get("GTIN", "")).strip()
    row["No. of copy"] = settings.get("no_of_copy", "0")
    row["QB Description"] = qb.get("Description", "")
    if it:
        p = it["_parsed"]
        row["Item No."] = it["item_no"]
        row["Item Description"] = it["description"]
        row["Matched FS Description"] = f'{it["description"]} (FS row {it["row"]})'
        row["Cut Size and Method"] = format_cut_size_and_method(
            it.get("cut_size") or p.cut_size, it.get("cut_method") or p.cut_method)
        # The label prints what is in the case, so the QuickBooks pack wins; FS pack is the fallback.
        inner, per = (q.pack_inner, q.pack_per) if q.pack_inner is not None else (p.pack_inner, p.pack_per)
    else:
        row["Cut Size and Method"] = format_cut_size_and_method(q.cut_size, q.cut_method)
        inner, per = q.pack_inner, q.pack_per
    row["No. of Inner PKG"], row["LB per BAG"] = pack_to_bc(inner, per)
    return row


def match_one(qb: dict, ref: Reference, rules: dict, settings: dict, hits: dict):
    """Return (ref_item or None, score, method_text)."""
    code = code_key(qb.get("Customer Item No"))
    q = parse_description(qb.get("Description", ""))

    r = rules["code_override"].get(code)
    if r and ref.get(r["value"]["item_no"]):
        hits[r["id"]] = hits.get(r["id"], 0) + 1
        return ref.get(r["value"]["item_no"]), 100, "Learned rule (customer code → item)"
    r = rules["desc_alias"].get(desc_key(qb.get("Description")))
    if r and ref.get(r["value"]["item_no"]):
        hits[r["id"]] = hits.get(r["id"], 0) + 1
        return ref.get(r["value"]["item_no"]), 100, "Learned rule (description → item)"

    hits_code = ref.by_customer_code(code, settings.get("company_hint", "imperial")) if code else []
    if hits_code:
        if len(hits_code) > 1:
            hits_code.sort(key=lambda it: -similarity(q, it)["score"])
        it = hits_code[0]
        score, notes = verify_code_match(q, it)
        method = "Customer code"
        if len(hits_code) > 1:
            score -= 10
            notes.append(f"code on {len(hits_code)} FS items: " + ", ".join(h["item_no"] for h in hits_code))
        if notes:
            method += " (" + "; ".join(notes) + ")"
        return it, score, method

    cands = best_description_match(q, ref)
    if cands and cands[0][0] >= 0.6:
        s, detail, it = cands[0]
        score = min(90, round(s * 90))
        if len(cands) > 1 and cands[0][0] - cands[1][0] < 0.02:
            score -= 10
        alt = ", ".join(f'{c[2]["item_no"]}' for c in cands[1:3])
        return it, score, f"Description match (alt: {alt})" if alt else "Description match"
    return None, 0, "No match"


def load_rules(store) -> dict:
    out = {"code_override": {}, "desc_alias": {}, "field_override": {}, "token_rule": []}
    for r in store.rules(enabled_only=True):
        if r["kind"] == "token_rule":
            out["token_rule"].append(r)
        elif r["kind"] in out:
            out[r["kind"]][r["rkey"]] = r
    return out


def generate(qb_rows: list[dict], ref: Reference, store, settings: dict, existing: list[dict] | None = None,
             mode: str = "replace") -> tuple[list[dict], dict]:
    rules = load_rules(store)
    hits: dict[int, int] = {}
    existing = existing or []
    by_code_existing = {code_key(r.get("Customer Item Code")): r for r in existing if r.get("Customer Item Code")}
    try:
        next_entry = int(settings.get("entry_no_start", "1"))
    except ValueError:
        next_entry = 1
    used = []
    for r in existing:
        try:
            used.append(int(str(r.get("Entry No.", "")).strip()))
        except ValueError:
            pass
    if used:
        next_entry = max(next_entry, max(used) + 1)

    out = list(existing) if mode == "append_missing" else []
    stats = {"total": 0, "code": 0, "description": 0, "learned": 0, "none": 0, "skipped": 0}
    for qb in qb_rows:
        if not str(qb.get("Description", "")).strip() and not str(qb.get("Customer Item No", "")).strip():
            continue
        ck = code_key(qb.get("Customer Item No"))
        prev = by_code_existing.get(ck)
        if mode == "append_missing" and prev:
            stats["skipped"] += 1
            continue
        it, score, method = match_one(qb, ref, rules, settings, hits)
        row = build_bc_row(qb, it, settings)
        for col in ("Cut Size and Method", "Item Description"):
            row[col] = apply_token_rules(row[col], col, rules["token_rule"], hits)
        # per-item learned field overrides
        applied = []
        for col in LEARNABLE:
            if col == "Item No.":
                continue
            fo = rules["field_override"].get(f"{ck}|{col}")
            if fo and fo["value"].get("item_no", "") == row["Item No."]:
                row[col] = fo["value"]["value"]
                applied.append(col)
                hits[fo["id"]] = hits.get(fo["id"], 0) + 1
        if applied:
            method += " + learned fields: " + ", ".join(applied)
        row["Match Score"] = score
        row["Match Method"] = method
        if prev and prev.get("Entry No."):
            row["Entry No."] = prev["Entry No."]
            row["_rid"] = prev.get("_rid") or new_rid()
        else:
            row["Entry No."] = str(next_entry)
            next_entry += 1
            row["_rid"] = new_rid()
        out.append(row)
        stats["total"] += 1
        if method.startswith("Learned"):
            stats["learned"] += 1
        elif method.startswith("Customer code"):
            stats["code"] += 1
        elif method.startswith("Description"):
            stats["description"] += 1
        else:
            stats["none"] += 1
    store.bump_hits(hits)
    return out, stats


# --------------------------------------------------------------------------- analysis
def gtin_valid(g: str) -> bool:
    if not re.fullmatch(r"\d{14}", g or ""):
        return False
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(g[:-1]))
    return (10 - total % 10) % 10 == int(g[-1])


def _split_cut_cell(cell: str) -> tuple[str, str]:
    s = normalize_quotes(cell or "")
    m = SIZE_RE.search(s)
    size = m.group(1) if m else ""
    method = (s[: m.start()] + " " + s[m.end():]).strip() if m else s.strip()
    return norm_size(size), canon_method(method)


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def analyze(bc_rows: list[dict], qb_rows: list[dict], ref: Reference) -> list[dict]:
    """Return one {confidence, errors:[{col, sev, msg}]} per BC row (same order)."""
    qb_by_code = {}
    for q in qb_rows:
        qb_by_code.setdefault(code_key(q.get("Customer Item No")), q)

    def group(col):
        g = {}
        for i, r in enumerate(bc_rows):
            v = str(r.get(col, "") or "").strip()
            if v:
                g.setdefault(v.upper(), []).append(i)
        return g

    by_item, by_gtin, by_code, by_entry = group("Item No."), group("GTIN"), {}, group("Entry No.")
    for i, r in enumerate(bc_rows):
        k = code_key(r.get("Customer Item Code"))
        if k:
            by_code.setdefault(k, []).append(i)

    def entry(i):
        return bc_rows[i].get("Entry No.") or f"#{i + 1}"

    results = []
    for i, r in enumerate(bc_rows):
        errs = []

        def add(col, sev, msg):
            errs.append({"col": col, "sev": sev, "msg": msg})

        item_no = str(r.get("Item No.", "") or "").strip()
        gtin = str(r.get("GTIN", "") or "").strip()
        ck = code_key(r.get("Customer Item Code"))
        it = ref.get(item_no) if item_no else None
        qb = qb_by_code.get(ck)

        # --- identity
        if not item_no:
            add("Item No.", "error", "No Item No. (no match found) — enter the TPC item")
        elif ref.loaded and not it:
            add("Item No.", "error", f"{item_no} not found in FS database")
        if it and it.get("status", "").lower().replace(" ", "") == "notready":
            add("Item No.", "warning", f"{item_no} is 'Not ready' in FS database")
        if not ck:
            add("Customer Item Code", "error", "Customer Item Code is blank")
        elif len(by_code.get(ck, [])) > 1:
            others = [entry(j) for j in by_code[ck] if j != i]
            add("Customer Item Code", "error", f"Duplicate Customer Item Code (also Entry {', '.join(others)})")
        elif qb_rows and not qb:
            add("Customer Item Code", "warning", "Customer Item Code not in QuickBooks source")
        e = str(r.get("Entry No.", "") or "").strip()
        if not e:
            add("Entry No.", "warning", "Entry No. is blank")
        elif len(by_entry.get(e.upper(), [])) > 1:
            add("Entry No.", "error", "Duplicate Entry No.")
        for col in ("Customer No.", "Customer Name"):
            if not str(r.get(col, "") or "").strip():
                add(col, "warning", f"{col} is blank")

        # --- GTIN
        if not gtin:
            add("GTIN", "error", "GTIN is blank")
        elif not gtin_valid(gtin):
            add("GTIN", "error", "GTIN is not a valid 14-digit GTIN (length or check digit)")
        if item_no:
            gtins = sorted({str(bc_rows[j].get("GTIN", "")).strip() for j in by_item.get(item_no.upper(), [])} - {""})
            if len(gtins) > 1:
                others = [f"Entry {entry(j)} ({bc_rows[j].get('Customer Item Code')})"
                          for j in by_item[item_no.upper()] if j != i]
                add("GTIN", "error", f"Same item {item_no} has {len(gtins)} GTINs: {', '.join(gtins)} — see {', '.join(others)}")
        if gtin:
            items = sorted({str(bc_rows[j].get("Item No.", "")).strip().upper() for j in by_gtin.get(gtin.upper(), [])} - {""})
            if len(items) > 1:
                add("GTIN", "error", f"GTIN {gtin} is used by different items: {', '.join(items)}")
            elif len(by_gtin.get(gtin.upper(), [])) > 1:
                others = [entry(j) for j in by_gtin[gtin.upper()] if j != i]
                add("GTIN", "warning", f"GTIN repeated on Entry {', '.join(others)}")

        # --- pack / weight
        inner, per = parse_bc_pack(r.get("No. of Inner PKG"), r.get("LB per BAG"))
        if inner is None or per is None:
            add("No. of Inner PKG", "warning", "Pack not parseable — enter No. of Inner PKG / LB per BAG by hand")
        else:
            total = inner * per
            cw = _num(qb.get("Case Weight")) if qb else None
            if cw is not None and abs(cw - total) > 0.01:
                add("LB per BAG", "warning", f"Inner × LB/bag = {total:g} lb but QB Case Weight = {cw:g}")
            if it and isinstance(it.get("total_lb"), (int, float)) and abs(it["total_lb"] - total) > 0.01:
                add("No. of Inner PKG", "warning", f"Inner × LB/bag = {total:g} lb but FS Total lb = {it['total_lb']:g}")

        # --- cut / description vs QuickBooks
        if qb:
            qp = parse_description(qb.get("Description", ""))
            size, meth = _split_cut_cell(r.get("Cut Size and Method"))
            if qp.cut_size and size and norm_size(qp.cut_size) != size:
                add("Cut Size and Method", "warning", f"Cut size differs from QB description ({qp.cut_size})")
            if qp.cut_method and meth and canon_method(qp.cut_method) != meth:
                add("Cut Size and Method", "warning", f"Cut method differs from QB description ({qp.cut_method})")
            if not (r.get("Cut Size and Method") or "").strip() and (qp.cut_size or qp.cut_method):
                add("Cut Size and Method", "warning", "Cut Size and Method is blank")
            rm = produce_tokens(qb.get("Raw Material", ""))
            if it and rm:
                known = set(produce_tokens(it.get("rm1", ""))) | it["_tokens"]
                if not set(rm) & known:
                    add("Item No.", "warning",
                        f"QB Raw Material '{qb.get('Raw Material')}' ≠ FS RM '{it.get('rm1')}'")
            bd = parse_description(r.get("Item Description", ""))
            if bd.tokens and qp.tokens and not set(bd.tokens) & set(qp.tokens):
                add("Item Description", "warning", "Item Description produce differs from QB description")
        if it and r.get("Item Description"):
            bd = set(parse_description(r["Item Description"]).tokens)
            if bd and not bd & it["_tokens"]:
                add("Item Description", "warning", f"Item Description doesn't match FS description of {item_no}")

        base = _num(r.get("Match Score"))
        base = 100 if base is None else base
        penalty = sum(ERROR_PENALTY[e["sev"]] for e in errs)
        results.append({"confidence": max(0, min(100, round(base - penalty))), "errors": errs})
    return results
