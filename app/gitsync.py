"""Mirror every table version into git: one commit per change.

After each saved version the current tables and learned rules are written to
``table_history/`` as plain CSV/JSON (readable diffs on GitHub / in any git
tool) and committed. Only ``table_history/`` is ever staged, so code changes
you are working on are never swept into these commits. Pushing is optional
(setting ``git_autopush``).
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path

from .io_excel import to_csv

log = logging.getLogger("gitsync")
_lock = threading.Lock()

FILES = {"bc": "imperial_label_bc.csv", "qb": "quickbooks_source.csv"}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)


def is_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def write_snapshot(folder: Path, tables: dict, rules: list[dict]):
    folder.mkdir(parents=True, exist_ok=True)
    for name, fname in FILES.items():
        t = tables.get(name)
        if t is None:
            continue
        cols = [c for c in t["columns"] if not c.startswith("_")]
        (folder / fname).write_bytes(to_csv(cols, t["rows"]).replace(b"\r\n", b"\n"))
    slim = [{k: r[k] for k in ("kind", "rkey", "value", "enabled", "origin")} for r in rules]
    slim.sort(key=lambda r: (r["kind"], r["rkey"]))
    (folder / "learned_rules.json").write_text(json.dumps(slim, indent=2, ensure_ascii=False) + "\n")


def commit_version(repo: Path, folder: Path, tables: dict, rules: list[dict], message: str,
                   push: bool = False) -> dict:
    """Write snapshot + commit. Never raises: returns {'ok', 'commit'|'error'}."""
    with _lock:
        try:
            if not is_repo(repo):
                return {"ok": False, "error": "not a git repository"}
            write_snapshot(folder, tables, rules)
            rel = str(folder.relative_to(repo))
            _git(repo, "add", "--", rel)
            if _git(repo, "diff", "--cached", "--quiet", "--", rel).returncode == 0:
                return {"ok": True, "commit": None, "note": "nothing changed"}
            r = _git(repo, "commit", "-m", message, "--", rel)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout).strip()[:500]}
            sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
            out = {"ok": True, "commit": sha}
            if push:
                p = _git(repo, "push")
                out["pushed"] = p.returncode == 0
                if p.returncode != 0:
                    out["push_error"] = (p.stderr or p.stdout).strip()[:500]
            return out
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.warning("git autocommit failed: %s", e)
            return {"ok": False, "error": str(e)}


def commit_message(table_label: str, version: dict, changes: list[dict], learned: list[str]) -> str:
    head = f"{table_label} v{version['version_no']}: {version['source']}"
    if version.get("note"):
        head += f" — {version['note']}"
    head += f" ({version.get('changed_cells', 0)} change(s))"
    lines = [head[:200], ""]
    for c in changes[:40]:
        lines.append(f"- {c.get('key') or c['rid']} · {c['col']}: '{c['old']}' → '{c['new']}'")
    if len(changes) > 40:
        lines.append(f"- … and {len(changes) - 40} more")
    for m in learned:
        lines.append(f"- {m}")
    return "\n".join(lines).strip() + "\n"
