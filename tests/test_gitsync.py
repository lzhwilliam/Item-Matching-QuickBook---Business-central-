import subprocess
from pathlib import Path

from app.gitsync import commit_message, commit_version


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout


def test_one_commit_per_version_and_only_history_staged(tmp_path):
    repo = Path(tmp_path)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "code.py").write_text("x = 1\n")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "init")
    (repo / "code.py").write_text("x = 2\n")  # uncommitted work must not be swept in

    tables = {"bc": {"columns": ["Item No.", "GTIN", "_rid"], "rows": [{"Item No.": "TPC1", "GTIN": "1", "_rid": "a"}]},
              "qb": {"columns": ["Description"], "rows": [{"Description": "Onion"}]}}
    v = {"version_no": 1, "source": "generate", "note": "", "changed_cells": 1}
    r1 = commit_version(repo, repo / "table_history", tables, [], commit_message("BC", v, [], []))
    assert r1["ok"] and r1["commit"]
    tables["bc"]["rows"][0]["GTIN"] = "2"
    ch = [{"rid": "a", "key": "123", "col": "GTIN", "old": "1", "new": "2"}]
    r2 = commit_version(repo, repo / "table_history", tables, [], commit_message("BC", v | {"version_no": 2, "source": "edit"}, ch, []))
    assert r2["ok"] and r2["commit"] != r1["commit"]
    log = git(repo, "log", "--format=%B", "-1")
    assert "BC v2: edit" in log and "GTIN: '1' → '2'" in log
    assert git(repo, "status", "--porcelain").strip() == "M code.py"
    assert "_rid" not in (repo / "table_history" / "imperial_label_bc.csv").read_text()
    # unchanged snapshot -> no empty commit
    r3 = commit_version(repo, repo / "table_history", tables, [], "same")
    assert r3["commit"] is None
