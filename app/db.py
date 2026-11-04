"""SQLite storage: per-table version history, learned rules, edit log, settings."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_SETTINGS = {
    "customer_no": "C0004",
    "customer_name": "Baugh Supply",
    "no_of_copy": "0",
    "entry_no_start": "1",
    "company_hint": "imperial",
    "token_rule_threshold": "2",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    note TEXT,
    changed_cells INTEGER DEFAULT 0,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_versions ON versions(table_name, version_no);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    rkey TEXT NOT NULL,
    value TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    origin TEXT,
    UNIQUE(kind, rkey)
);
CREATE TABLE IF NOT EXISTS edit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    table_name TEXT NOT NULL,
    version_no INTEGER,
    row_key TEXT,
    column_name TEXT,
    old_value TEXT,
    new_value TEXT,
    pattern TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    skey TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------- versions ----------------
    def latest(self, table: str) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM versions WHERE table_name=? ORDER BY version_no DESC LIMIT 1", (table,)
            ).fetchone()
        return self._ver(r) if r else None

    def get_version(self, table: str, version_no: int) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM versions WHERE table_name=? AND version_no=?", (table, version_no)
            ).fetchone()
        return self._ver(r) if r else None

    def list_versions(self, table: str) -> list[dict]:
        with self.lock:
            rs = self.conn.execute(
                "SELECT id, table_name, version_no, created_at, source, note, changed_cells "
                "FROM versions WHERE table_name=? ORDER BY version_no DESC", (table,)
            ).fetchall()
        return [dict(r) for r in rs]

    def add_version(self, table: str, data: dict, source: str, note: str = "", changed_cells: int = 0) -> dict:
        with self.lock:
            cur = self.latest(table)
            vno = (cur["version_no"] + 1) if cur else 1
            self.conn.execute(
                "INSERT INTO versions(table_name, version_no, created_at, source, note, changed_cells, data) "
                "VALUES (?,?,?,?,?,?,?)",
                (table, vno, now(), source, note, changed_cells, json.dumps(data)),
            )
            self.conn.commit()
        return self.get_version(table, vno)

    @staticmethod
    def _ver(r) -> dict:
        d = dict(r)
        d["data"] = json.loads(d["data"])
        return d

    # ---------------- rules ----------------
    def rules(self, kind: str | None = None, enabled_only: bool = False) -> list[dict]:
        q, args = "SELECT * FROM rules WHERE 1=1", []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if enabled_only:
            q += " AND enabled=1"
        q += " ORDER BY updated_at DESC"
        with self.lock:
            rs = self.conn.execute(q, args).fetchall()
        out = []
        for r in rs:
            d = dict(r)
            d["value"] = json.loads(d["value"])
            out.append(d)
        return out

    def upsert_rule(self, kind: str, key: str, value, origin: str = "", enabled: int = 1):
        with self.lock:
            self.conn.execute(
                "INSERT INTO rules(kind, rkey, value, enabled, hits, created_at, updated_at, origin) "
                "VALUES (?,?,?,?,0,?,?,?) ON CONFLICT(kind, rkey) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at, origin=excluded.origin, enabled=excluded.enabled",
                (kind, key, json.dumps(value), enabled, now(), now(), origin),
            )
            self.conn.commit()

    def set_rule_enabled(self, rule_id: int, enabled: bool):
        with self.lock:
            self.conn.execute("UPDATE rules SET enabled=?, updated_at=? WHERE id=?", (1 if enabled else 0, now(), rule_id))
            self.conn.commit()

    def delete_rule(self, rule_id: int):
        with self.lock:
            self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
            self.conn.commit()

    def bump_hits(self, rule_ids: dict[int, int]):
        if not rule_ids:
            return
        with self.lock:
            for rid, n in rule_ids.items():
                self.conn.execute("UPDATE rules SET hits = hits + ? WHERE id=?", (n, rid))
            self.conn.commit()

    # ---------------- edit log ----------------
    def log_edit(self, table, version_no, row_key, column, old, new, pattern=""):
        with self.lock:
            self.conn.execute(
                "INSERT INTO edit_log(created_at, table_name, version_no, row_key, column_name, old_value, new_value, pattern) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (now(), table, version_no, row_key, column, old, new, pattern),
            )
            self.conn.commit()

    def pattern_count(self, pattern: str) -> int:
        with self.lock:
            r = self.conn.execute("SELECT COUNT(*) FROM edit_log WHERE pattern=?", (pattern,)).fetchone()
        return r[0]

    def edit_log(self, limit: int = 300) -> list[dict]:
        with self.lock:
            rs = self.conn.execute("SELECT * FROM edit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rs]

    # ---------------- settings ----------------
    def settings(self) -> dict:
        with self.lock:
            rs = self.conn.execute("SELECT skey, value FROM settings").fetchall()
        s = dict(DEFAULT_SETTINGS)
        s.update({r["skey"]: r["value"] for r in rs})
        return s

    def set_settings(self, values: dict):
        with self.lock:
            for k, v in values.items():
                if k in DEFAULT_SETTINGS:
                    self.conn.execute(
                        "INSERT INTO settings(skey, value) VALUES (?,?) ON CONFLICT(skey) DO UPDATE SET value=excluded.value",
                        (k, str(v)),
                    )
            self.conn.commit()
