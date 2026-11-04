import copy
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Store
from app.learning import learn_from_edits
from app.matcher import generate as gen
from tests.fixtures import QB, make_ref


def test_item_change_is_learned_and_reapplied(tmp_path):
    st, ref = Store(Path(tmp_path) / "t.db"), make_ref()
    rows, _ = gen(QB, ref, st, st.settings())
    new = copy.deepcopy(rows)
    new[1]["Item No."] = "TPC4"  # user says carrot is the 4/5lb item
    new, msgs = learn_from_edits(st, rows, new, QB, ref, st.settings(), 2)
    assert new[1]["Item Description"] == 'Carrot Diced 1/2" 4/5lb'  # refilled from FS
    assert any("6864110" in m for m in msgs)
    again, stats = gen(QB, ref, st, st.settings())
    assert again[1]["Item No."] == "TPC4" and again[1]["Match Score"] == 100
    assert again[1]["Match Method"].startswith("Learned")
    assert stats["learned"] == 1


def test_field_override_and_token_rule(tmp_path):
    st, ref = Store(Path(tmp_path) / "t.db"), make_ref()
    rows, _ = gen(QB, ref, st, st.settings())
    tomato = next(i for i, r in enumerate(rows) if r["Customer Item Code"] == "6715163")
    new = copy.deepcopy(rows)
    new[tomato]["Cut Size and Method"] = "8 CUT WEDGE"
    learn_from_edits(st, rows, new, QB, ref, st.settings(), 2)
    again, _ = gen(QB, ref, st, st.settings())
    assert again[tomato]["Cut Size and Method"] == "8 CUT WEDGE"  # field override
    assert not st.rules("token_rule")  # seen once only
    # second time the same word change happens -> global token rule
    rows2 = copy.deepcopy(again)
    rows2[tomato]["Cut Size and Method"] = "8 CUT WEDGED"
    new2 = copy.deepcopy(rows2)
    new2[tomato]["Cut Size and Method"] = "8 CUT WEDGE"
    learn_from_edits(st, rows2, new2, QB, ref, st.settings(), 3)
    assert st.rules("token_rule")


def test_api_versions_restore_and_download(tmp_path, monkeypatch):
    monkeypatch.setenv("QBBC_DATA_DIR", str(tmp_path))
    import app.main as main
    importlib.reload(main)
    main.state["ref"] = make_ref()
    c = TestClient(main.app)
    csv_bytes = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n" + "\n".join(
        ",".join('"' + str(q[k]).replace('"', '""') + '"' for k in
                 ["Item", "Description", "Customer Item No", "Case Weight", "GTIN", "Raw Material"]) for q in QB)).encode()
    r = c.post("/api/table/qb/upload", files={"file": ("qb.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200 and len(r.json()["rows"]) == 4
    g = c.post("/api/generate", json={"mode": "replace"}).json()
    assert g["version_no"] == 1 and len(g["analysis"]) == 4
    rows = g["rows"]
    rows[0]["GTIN"] = "10074865916070"
    s = c.post("/api/table/bc/save", json={"rows": rows, "columns": g["columns"]}).json()
    assert s["version_no"] == 2
    d = c.get("/api/table/bc/diff", params={"version": 2}).json()
    assert d["changed"][0]["col"] == "GTIN"
    assert c.post("/api/table/bc/restore", json={"version": 1}).json()["version_no"] == 3
    assert c.get("/api/table/bc").json()["rows"][0]["GTIN"] == "10074865916063"
    assert len(c.get("/api/table/bc/versions").json()) == 3
    x = c.get("/api/table/bc/download", params={"fmt": "bc"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.get("/api/rules").json()["rules"]  # GTIN edit was learned as a field override
