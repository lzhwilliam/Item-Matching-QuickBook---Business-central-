from pathlib import Path

from app.db import Store
from app.matcher import analyze, gtin_valid
from app.matcher import generate as gen
from tests.fixtures import QB, make_ref


def store(tmp_path):
    return Store(Path(tmp_path) / "t.db")


def test_code_and_description_matching(tmp_path):
    st, ref = store(tmp_path), make_ref()
    rows, stats = gen(QB, ref, st, st.settings())
    by = {r["Customer Item Code"]: r for r in rows}
    assert by["0010967"]["Item No."] == "TPC1" and by["0010967"]["Match Score"] == 100
    assert by["6864110"]["Item No."] == "TPC3"  # description match picks the 1/5lb pack
    assert by["6864110"]["Match Method"].startswith("Description")
    assert by["0010967"]["Cut Size and Method"] == '3/8" DICED'
    assert (by["0010967"]["No. of Inner PKG"], by["0010967"]["LB per BAG"]) == ("4BAG", "5LB")
    assert by["0010967"]["Customer No."] == "C0004"
    assert stats["code"] == 2 and stats["description"] == 2
    assert [r["Entry No."] for r in rows] == ["1", "2", "3", "4"]


def test_gtin_errors(tmp_path):
    st, ref = store(tmp_path), make_ref()
    rows, _ = gen(QB, ref, st, st.settings())
    rows[1]["Item No."] = "TPC1"            # same item, different GTIN than row 0
    rows[2]["GTIN"] = "12345"               # invalid
    a = analyze(rows, QB, ref)
    assert any("2 GTINs" in e["msg"] for e in a[0]["errors"])
    assert any("not a valid" in e["msg"] for e in a[2]["errors"])
    assert a[0]["confidence"] < 100
    assert gtin_valid("10074865916063") and not gtin_valid("10074865916064")


def test_weight_and_cut_warnings(tmp_path):
    st, ref = store(tmp_path), make_ref()
    rows, _ = gen(QB, ref, st, st.settings())
    rows[0]["LB per BAG"] = "6LB"
    rows[0]["Cut Size and Method"] = '1/4" DICED'
    msgs = [e["msg"] for e in analyze(rows, QB, ref)[0]["errors"]]
    assert any("Case Weight" in m for m in msgs)
    assert any("Cut size differs" in m for m in msgs)
