"""FastAPI app: QuickBooks → BC Imperial label matching platform."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import Store
from .io_excel import read_upload, to_csv, to_xlsx
from .learning import diff_rows, learn_from_edits
from .matcher import BC_COLUMNS, BC_META, QB_COLUMNS, analyze, generate, new_rid
from .reference import Reference, load_fs_database, read_reference, save_reference

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QBBC_DATA_DIR", ROOT / "data"))
REF_PATH = DATA / "reference.json"

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
    return out


def _count_changes(old_rows, new_rows, columns) -> int:
    d = diff_rows(old_rows, new_rows, columns)
    return len(d["changed"]) + len(d["added"]) + len(d["removed"])


def _save(name: str, columns: list[str], rows: list[dict], source: str, note: str = "", learn: bool = True):
    cur = _current(name)
    rows = _ensure_rids(rows)
    msgs = []
    if name == "bc" and learn and cur["data"]["rows"]:
        rows, msgs = learn_from_edits(store, cur["data"]["rows"], rows, _qb_rows(), ref(), store.settings(),
                                      cur["version_no"] + 1)
    n = _count_changes(cur["data"]["rows"], rows, columns)
    if n == 0 and cur["version_no"] > 0 and columns == cur["data"]["columns"]:
        return _payload(name, cur) | {"learned": msgs, "unchanged": True}
    v = store.add_version(name, {"columns": columns, "rows": rows}, source, note, n)
    return _payload(name, v) | {"learned": msgs}


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
    return {"loaded": True, "count": data["count"], "source_file": file.filename}


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
    return _save(name, columns, body.get("rows", []), body.get("source", "edit"), body.get("note", ""))


@app.post("/api/table/{name}/upload")
async def upload_table(name: str, file: UploadFile = File(...), learn: bool = True):
    t = _table(name)
    cols, rows = read_upload(file.filename, await file.read(), t["columns"])
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
    return _save(name, cols, rows, "upload", f"Uploaded {file.filename}", learn=learn)


@app.get("/api/table/{name}/download")
def download_table(name: str, fmt: str = "xlsx", version: int | None = None):
    t = _table(name)
    v = store.get_version(name, version) if version else _current(name)
    if not v:
        raise HTTPException(404, "Version not found")
    cols, rows = v["data"]["columns"], v["data"]["rows"]
    vno = v["version_no"]
    base = f"{'imperial_label_bc' if name == 'bc' else 'quickbooks_source'}_v{vno}"
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
        "changed": [c | {"key": new_by[c["rid"]].get(key, "")} for c in d["changed"]],
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
                 f"Restored v{v['version_no']}", learn=False)


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
