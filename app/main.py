"""FastAPI app: QuickBooks → BC Imperial label matching platform."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import Store
from .gitsync import commit_message, commit_version
import hashlib

from .io_excel import is_xlsx, read_upload_rich, to_csv, to_xlsx
from .learning import diff_meta, diff_rows, learn_from_edits
from .xlsx_export import effective_layout, write_csv, write_xlsx
from .matcher import BC_COLUMNS, BC_META, QB_COLUMNS, analyze, generate, new_rid
from .reference import (Reference, load_fs_database, load_fs_sheet, read_reference, save_reference,
                        sheet_from_reference)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QBBC_DATA_DIR", ROOT / "data"))
REF_PATH = DATA / "reference.json"
SHEET_PATH = DATA / "fs_sheet.json"
TEMPLATES = DATA / "templates"  # original uploaded workbooks, used for "Original format" downloads
_KEEP = object()  # _save(): keep the current version's sheet layout
HISTORY_DIR = Path(os.environ.get("QBBC_HISTORY_DIR", ROOT / "table_history"))
GIT_ENABLED = os.environ.get("QBBC_GIT_AUTOCOMMIT", "1") != "0"

TABLES = {
    "bc": {"label": "Imperial Label (BC)", "columns": BC_COLUMNS + BC_META, "sheet": "Imperial Item Label Master"},
    "qb": {"label": "QuickBooks Source", "columns": QB_COLUMNS, "sheet": "QuickBooks Imperial Items"},
}

app = FastAPI(title="QuickBooks → BC Imperial Label Matcher")
store = Store(DATA / "platform.db")
state = {"ref": Reference(read_reference(REF_PATH))}


def ref() -> Reference:
    return state["ref"]


def _table(name: str) -> dict:
    if name not in TABLES:
        raise HTTPException(404, f"Unknown table {name}")
    return TABLES[name]


def _current(name: str) -> dict:
    v = store.latest(name)
    if v:
        return v
    return {"version_no": 0, "data": {"columns": _table(name)["columns"], "rows": []},
            "created_at": None, "source": "empty", "note": ""}


def _ensure_rids(rows: list[dict]) -> list[dict]:
    for r in rows:
        if not r.get("_rid"):
            r["_rid"] = new_rid()
    return rows


def _qb_rows() -> list[dict]:
    return _current("qb")["data"]["rows"]


def _payload(name: str, v: dict) -> dict:
    data = v["data"]
    out = {"table": name, "version_no": v["version_no"], "created_at": v.get("created_at"),
           "source": v.get("source"), "note": v.get("note"), "columns": data["columns"], "rows": data["rows"]}
    if name == "bc":
        out["analysis"] = analyze(data["rows"], _qb_rows(), ref())
    sheet = data.get("sheet") or {}
    out["layout"] = effective_layout(sheet, data["columns"], _skip(name))
    out["sheet"] = {"kind": sheet.get("kind"), "file_name": sheet.get("file_name"),
                    "col_widths": sheet.get("col_widths") or {}, "template": bool(_template_path(sheet))}
    return out


def _skip(name: str) -> tuple:
    """Platform-only columns that are never written into the user's file."""
    return tuple(BC_META) if name == "bc" else ()


def _template_path(sheet: dict | None) -> Path | None:
    fname = (sheet or {}).get("template")
    if not fname:
        return None
    p = TEMPLATES / Path(fname).name
    return p if p.exists() else None


def _count_changes(old_rows, new_rows, columns) -> int:
    d = diff_rows(old_rows, new_rows, columns)
    return len(d["changed"]) + len(d["added"]) + len(d["removed"]) + len(diff_meta(old_rows, new_rows))


def _save(name: str, columns: list[str], rows: list[dict], source: str, note: str = "", learn: bool = True,
          sheet=_KEEP):
    cur = _current(name)
    if sheet is _KEEP:
        sheet = cur["data"].get("sheet")
    rows = _ensure_rids(rows)
    msgs = []
    if name == "bc" and learn and cur["data"]["rows"]:
        rows, msgs = learn_from_edits(store, cur["data"]["rows"], rows, _qb_rows(), ref(), store.settings(),
                                      cur["version_no"] + 1)
    n = _count_changes(cur["data"]["rows"], rows, columns)
    if (n == 0 and cur["version_no"] > 0 and columns == cur["data"]["columns"]
            and (sheet or None) == (cur["data"].get("sheet") or None)):
        return _payload(name, cur) | {"learned": msgs, "unchanged": True}
    old_rows = cur["data"]["rows"]
    data = {"columns": columns, "rows": rows}
    if sheet:
        data["sheet"] = sheet
    v = store.add_version(name, data, source, note, n)
    return _payload(name, v) | {"learned": msgs, "git": _git_commit(name, v, old_rows, msgs)}


def _git_commit(name: str, v: dict, old_rows: list[dict], learned: list[str]) -> dict | None:
    """One git commit per saved version (see app/gitsync.py)."""
    settings = store.settings()
    if not GIT_ENABLED or settings.get("git_autocommit", "1") != "1":
        return None
    key = "Customer Item Code" if name == "bc" else "Customer Item No"
    by_rid = {r.get("_rid"): r for r in v["data"]["rows"]}
    changes = [c | {"key": by_rid.get(c["rid"], {}).get(key, "")}
               for c in diff_rows(old_rows, v["data"]["rows"], v["data"]["columns"])["changed"]]
    changes += [m | {"key": by_rid.get(m["rid"], {}).get(key, ""), "col": f"{m['col']} ({m['kind']})"}
                for m in diff_meta(old_rows, v["data"]["rows"])]
    tables = {k: _current(k)["data"] for k in TABLES}
    msg = commit_message(TABLES[name]["label"], v, changes, learned)
    repo = HISTORY_DIR.parent
    return commit_version(repo, HISTORY_DIR, tables, store.rules(), msg,
                          push=settings.get("git_autopush", "0") == "1")


# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# ------------------------------------------------------------------ state / settings / reference
@app.get("/api/state")
def get_state():
    r = ref()
    return {
        "settings": store.settings(),
        "reference": {"loaded": r.loaded, "count": len(r.items), "source_file": r.data.get("source_file")},
        "tables": {k: {"label": t["label"], "version_no": _current(k)["version_no"],
                       "rows": len(_current(k)["data"]["rows"])} for k, t in TABLES.items()},
        "rules": len(store.rules()),
    }


@app.post("/api/settings")
def set_settings(values: dict = Body(...)):
    store.set_settings(values)
    return store.settings()


@app.post("/api/reference")
async def upload_reference(file: UploadFile = File(...)):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "fs_database_upload.xlsx"
    tmp.write_bytes(await file.read())
    try:
        data = load_fs_database(tmp)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read FS database: {e}")
    data["source_file"] = file.filename
    save_reference(data, REF_PATH)
    state["ref"] = Reference(data)
    try:
        sheet = load_fs_sheet(tmp)
        sheet["source_file"] = file.filename
        SHEET_PATH.write_text(json.dumps(sheet))
    except Exception:  # noqa: BLE001 - the browse view is optional; matching data is already saved
        SHEET_PATH.unlink(missing_ok=True)
    return {"loaded": True, "count": data["count"], "source_file": file.filename}


@app.get("/api/reference/original")
def reference_original():
    """The FS database workbook exactly as it was uploaded."""
    p = DATA / "fs_database_upload.xlsx"
    if not p.exists():
        raise HTTPException(404, "The FS database file hasn't been uploaded yet (Settings tab).")
    name = Path(ref().data.get("source_file") or "fs_database.xlsx").name
    return FileResponse(p, media_type=XLSX_MIME, filename=name)


@app.get("/api/reference/sheet")
def reference_sheet():
    """The full FS 'Mixed Database' sheet for the browse/search tab."""
    if SHEET_PATH.exists():
        return json.loads(SHEET_PATH.read_text())
    r = ref()
    if not r.loaded:
        return {"columns": [], "rows": [], "source_file": None}
    out = sheet_from_reference(r.data)
    out["source_file"] = r.data.get("source_file")
    out["partial"] = True
    return out


@app.get("/api/reference/search")
def search_reference(q: str = "", limit: int = 30):
    from .matcher import best_description_match
    from .parser import parse_description
    r = ref()
    if not q.strip():
        return []
    exact = r.get(q.strip())
    out = [exact] if exact else []
    out += [it for _, _, it in best_description_match(parse_description(q), r, limit)]
    seen, res = set(), []
    for it in out:
        if it["item_no"] not in seen:
            seen.add(it["item_no"])
            res.append({k: it[k] for k in ("item_no", "description", "status", "cut_method", "cut_size", "pack", "rm1")})
    return res[:limit]


# ------------------------------------------------------------------ tables
@app.get("/api/table/{name}")
def get_table(name: str):
    _table(name)
    return _payload(name, _current(name))


@app.post("/api/table/{name}/save")
def save_table(name: str, body: dict = Body(...)):
    t = _table(name)
    columns = body.get("columns") or t["columns"]
    sheet = _KEEP
    widths = body.get("col_widths")
    if isinstance(widths, dict):  # column widths are the only layout the grid edits
        clean = {}
        for k, w in widths.items():
            try:
                clean[str(k)] = round(min(255.0, max(1.0, float(w))), 2)
            except (TypeError, ValueError):
                continue
        sheet = dict(_current(name)["data"].get("sheet") or {})
        if clean or sheet.get("col_widths"):
            sheet["col_widths"] = clean
    return _save(name, columns, body.get("rows", []), body.get("source", "edit"), body.get("note", ""),
                 sheet=sheet)


@app.post("/api/table/{name}/upload")
async def upload_table(name: str, file: UploadFile = File(...), learn: bool = True):
    t = _table(name)
    content = await file.read()
    cols, rows, sheet = read_upload_rich(file.filename, content, t["columns"])
    if is_xlsx(file.filename):
        ext = ".xlsm" if file.filename.lower().endswith(".xlsm") else ".xlsx"
        tname = f"{name}_{hashlib.sha1(content).hexdigest()[:12]}{ext}"
        TEMPLATES.mkdir(parents=True, exist_ok=True)
        (TEMPLATES / tname).write_bytes(content)
        sheet["template"] = tname
    if name == "bc":
        missing = [c for c in BC_COLUMNS if c not in cols]
        if len(missing) > 6:
            raise HTTPException(400, f"Doesn't look like a BC Imperial label file (missing {missing})")
        # carry over row ids by Customer Item Code so the upload is diffed/learned as edits
        cur = {r.get("Customer Item Code"): r for r in _current("bc")["data"]["rows"]}
        for r in rows:
            prev = cur.get(r.get("Customer Item Code"))
            if prev:
                r["_rid"] = prev["_rid"]
                for m in BC_META:
                    r.setdefault(m, prev.get(m, ""))
        cols = t["columns"] + [c for c in cols if c not in t["columns"]]
    else:
        cols = [c for c in t["columns"] if c in cols] + [c for c in cols if c not in t["columns"]]
        for c in t["columns"]:
            if c not in cols:
                cols.append(c)
    return _save(name, cols, rows, "upload", f"Uploaded {file.filename}", learn=learn, sheet=sheet)


@app.get("/api/table/{name}/download")
def download_table(name: str, fmt: str = "xlsx", version: int | None = None):
    t = _table(name)
    v = store.get_version(name, version) if version else _current(name)
    if not v:
        raise HTTPException(404, "Version not found")
    cols, rows = v["data"]["columns"], v["data"]["rows"]
    vno = v["version_no"]
    base = f"{'imperial_label_bc' if name == 'bc' else 'quickbooks_source'}_v{vno}"
    if fmt == "original":
        return _download_original(name, v, base)
    if fmt == "csv":
        out_cols = [c for c in cols if c not in BC_META] if name == "bc" else cols
        return Response(to_csv(out_cols, rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{base}.csv"'})
    if fmt == "bc":
        data = to_xlsx(BC_COLUMNS, rows, t["sheet"], bc_layout=True)
        fname = f"{base}_BC_import.xlsx"
    else:
        analysis = analyze(rows, _qb_rows(), ref()) if name == "bc" else None
        data = to_xlsx(cols, rows, t["sheet"], analysis)
        fname = f"{base}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download_original(name: str, v: dict, base: str) -> Response:
    """The table in the layout/format of the file it was uploaded from (see app/xlsx_export.py)."""
    data = v["data"]
    sheet = data.get("sheet") or {}
    stem = Path(sheet.get("file_name") or base).stem
    fname = f"{stem}_v{v['version_no']}"
    if sheet.get("kind") == "csv":
        return Response(write_csv(data, _skip(name)), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    tpl = _template_path(sheet)
    macro = bool(tpl and tpl.suffix == ".xlsm")
    out = write_xlsx(data, tpl.read_bytes() if tpl else None, _skip(name), TABLES[name]["sheet"], macro=macro)
    ext, mime = (".xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12") if macro else (".xlsx", XLSX_MIME)
    return Response(out, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{fname}{ext}"'})


@app.get("/api/table/{name}/versions")
def list_versions(name: str):
    _table(name)
    return store.list_versions(name)


@app.get("/api/table/{name}/diff")
def version_diff(name: str, version: int, against: int | None = None):
    _table(name)
    v = store.get_version(name, version)
    if not v:
        raise HTTPException(404, "Version not found")
    prev = store.get_version(name, against if against is not None else version - 1)
    old_rows = prev["data"]["rows"] if prev else []
    cols = v["data"]["columns"]
    d = diff_rows(old_rows, v["data"]["rows"], cols)
    new_by = {r["_rid"]: r for r in v["data"]["rows"]}
    old_by = {r["_rid"]: r for r in old_rows}
    key = "Customer Item Code" if name == "bc" else "Customer Item No"
    return {
        "version": version, "against": prev["version_no"] if prev else 0,
        "changed": [c | {"key": new_by[c["rid"]].get(key, "")} for c in d["changed"]]
                   + [m | {"key": new_by[m["rid"]].get(key, ""), "col": f"{m['col']} ({m['kind']})"}
                      for m in diff_meta(old_rows, v["data"]["rows"])],
        "added": [new_by[r].get(key, "") or r for r in d["added"]],
        "removed": [old_by[r].get(key, "") or r for r in d["removed"]],
    }


@app.post("/api/table/{name}/restore")
def restore_version(name: str, body: dict = Body(...)):
    _table(name)
    v = store.get_version(name, int(body["version"]))
    if not v:
        raise HTTPException(404, "Version not found")
    return _save(name, v["data"]["columns"], v["data"]["rows"], "restore",
                 f"Restored v{v['version_no']}", learn=False, sheet=v["data"].get("sheet"))


# ------------------------------------------------------------------ generate
@app.post("/api/generate")
def run_generate(body: dict = Body(default={})):
    r = ref()
    if not r.loaded:
        raise HTTPException(400, "Load the FS full database first (Settings tab).")
    qb = _qb_rows()
    if not qb:
        raise HTTPException(400, "QuickBooks source table is empty — upload it first.")
    mode = body.get("mode", "replace")
    existing = _current("bc")["data"]["rows"]
    rows, stats = generate(qb, r, store, store.settings(), existing, mode)
    # keep each regenerated row's formatting and its place in the uploaded file's layout
    prev = {p.get("_rid"): p for p in existing if p.get("_rid")}
    for row in rows:
        p = prev.get(row.get("_rid"))
        if p is not None and p is not row:
            for k in ("_s", "_row"):
                if k in p and k not in row:
                    row[k] = p[k]
    note = (f"Generated from QB v{_current('qb')['version_no']}: {stats['code']} by code, "
            f"{stats['description']} by description, {stats['learned']} by learned rule, {stats['none']} unmatched")
    out = _save("bc", TABLES["bc"]["columns"], rows, "generate", note, learn=False)
    return out | {"stats": stats}


# ------------------------------------------------------------------ rules
@app.get("/api/rules")
def get_rules():
    return {"rules": store.rules(), "edit_log": store.edit_log(200)}


@app.post("/api/rules/{rule_id}")
def update_rule(rule_id: int, body: dict = Body(...)):
    store.set_rule_enabled(rule_id, bool(body.get("enabled", True)))
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    store.delete_rule(rule_id)
    return {"ok": True}


@app.get("/api/rules/export")
def export_rules():
    rules = [{k: r[k] for k in ("kind", "rkey", "value", "enabled", "hits", "origin")} for r in store.rules()]
    return Response(json.dumps(rules, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="learned_rules.json"'})


@app.post("/api/rules/import")
async def import_rules(file: UploadFile = File(...)):
    rules = json.loads(await file.read())
    for r in rules:
        store.upsert_rule(r["kind"], r["rkey"], r["value"], r.get("origin", "imported"), int(r.get("enabled", 1)))
    return {"imported": len(rules)}


@app.exception_handler(ValueError)
def value_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)
