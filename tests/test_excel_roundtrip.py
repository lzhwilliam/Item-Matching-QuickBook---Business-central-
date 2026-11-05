"""Upload -> edit -> "Original format" download keeps the file's layout, formats and formulas."""
import importlib
import io

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.styles import Font, PatternFill

from tests.fixtures import make_ref

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QBBC_DATA_DIR", str(tmp_path))
    import app.main as main
    importlib.reload(main)
    main.state["ref"] = make_ref()
    return TestClient(main.app)


def qb_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Items"
    ws["A1"] = "QUICKBOOKS ITEM EXPORT"
    ws["A1"].font = Font(bold=True, size=14)
    # header on row 3, columns NOT in the platform's canonical order, plus a formula column
    # ("Double") and a helper column with no header (H) that the platform doesn't show
    headers = ["Description", "Item", "Customer Item No", "Case Weight", "Double", "GTIN", "Raw Material"]
    for j, h in enumerate(headers, start=1):
        ws.cell(row=3, column=j, value=h).font = Font(bold=True)
    data = [
        ("Onion Yellow Diced 3/8\" 4/5lb", "Sales IM:0010967", "0010967", 20, "10074865916063", "Onion Yellow"),
        ("Carrot Diced 1/2\" 1/5lb", "Sales IM:6864110", "6864110", 5, "00074865931564", "Carrot"),
        ("Celery Diced 1/4\" 1/5lb", "Sales IM:6523450", "6523450", 5, "10074865935248", "Celery"),
    ]
    for i, (desc, item, code, wt, gtin, rm) in enumerate(data):
        r = 4 + i
        ws.cell(row=r, column=1, value=desc)
        ws.cell(row=r, column=2, value=item)
        ws.cell(row=r, column=3, value=code)
        ws.cell(row=r, column=4, value=wt).number_format = "0.00"
        ws.cell(row=r, column=5, value=f"=D{r}*2")
        ws.cell(row=r, column=6, value=gtin)
        ws.cell(row=r, column=7, value=rm)
        ws.cell(row=r, column=8, value=f"=D{r}+1")
    ws["A4"].fill = PatternFill("solid", fgColor="FFC6EFCE")
    ws.column_dimensions["A"].width = 41
    ws.freeze_panes = "A4"
    wb.create_sheet("Notes")["A1"] = "keep me"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_roundtrip_keeps_layout_formats_and_formulas(client):
    up = client.post("/api/table/qb/upload", files={"file": ("QB items.xlsx", qb_workbook(), XLSX)})
    assert up.status_code == 200, up.text
    p = up.json()
    rows = p["rows"]
    assert len(rows) == 3
    assert rows[0]["_f"] == {"Double": "=D4*2"} and rows[0]["_fr"] == 4 and rows[0]["_row"] == 4
    assert rows[0]["_s"]["Description"] == {"fill": "C6EFCE"}
    assert rows[0]["_s"]["Case Weight"] == {"numFmt": "0.00"}
    assert p["layout"]["header_row"] == 3 and p["layout"]["col_pos"]["Double"] == 5
    assert p["sheet"]["template"] is True

    # edit like the grid would: change a value, delete the middle row, format cells, add a row
    rows[0]["Case Weight"] = "7"
    rows[0]["_s"]["Item"] = {"b": 1, "fill": "FFFF00"}
    rows[0]["_s"]["Description"] = {}  # "Clear formats"
    del rows[1]
    rows.append({"Description": "Tomato Wedged 8 Cut 1/5lb", "Item": "Sales IM:6715163",
                 "Customer Item No": "6715163", "Case Weight": "5", "GTIN": "10074865951507",
                 "Raw Material": "Tomato", "Double": "10", "_f": {"Double": "=D4*2"}, "_fr": 4})
    s = client.post("/api/table/qb/save", json={"rows": rows, "columns": p["columns"],
                                                "col_widths": {"Item": 25}}).json()
    assert s["version_no"] == 2

    d = client.get("/api/table/qb/diff", params={"version": 2}).json()
    cols = {c["col"] for c in d["changed"]}
    assert "Item (format)" in cols and "Description (format)" in cols and "Case Weight" in cols

    x = client.get("/api/table/qb/download", params={"fmt": "original"})
    assert x.status_code == 200
    assert 'filename="QB items_v2.xlsx"' in x.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Items", "Notes"] and wb["Notes"]["A1"].value == "keep me"
    assert wb.calculation.fullCalcOnLoad
    ws = wb["Items"]
    assert ws["A1"].value == "QUICKBOOKS ITEM EXPORT" and ws["A1"].font.b
    assert [ws.cell(row=3, column=j).value for j in range(1, 8)] == [
        "Description", "Item", "Customer Item No", "Case Weight", "Double", "GTIN", "Raw Material"]
    assert ws.freeze_panes == "A4"
    assert ws.column_dimensions["A"].width == 41 and ws.column_dimensions["B"].width == 25
    # row 4: edited value written as a number, formulas kept, platform formats applied
    assert ws["D4"].value == 7 and ws["D4"].number_format == "0.00"
    assert ws["E4"].value == "=D4*2" and ws["H4"].value == "=D4+1"
    assert ws["B4"].font.b and ws["B4"].fill.fgColor.rgb.endswith("FFFF00")
    assert ws["A4"].fill.fill_type is None  # cleared in the platform
    # the old row 6 moved up to row 5: its formulas move with it
    assert ws["A5"].value.startswith("Celery") and ws["E5"].value == "=D5*2" and ws["H5"].value == "=D5+1"
    # new row 6: formula translated from its anchor, helper column filled down, style from the row above
    assert ws["A6"].value.startswith("Tomato")
    assert ws["E6"].value == "=D6*2" and ws["H6"].value == "=D6+1"
    assert ws["D6"].number_format == "0.00"
    assert ws.max_row == 6  # nothing left over from the deleted row


def test_csv_roundtrip_keeps_title_rows_and_column_order(client):
    text = ("Item list exported 2026-11-01,,,,,\n"
            "Description,Item,Customer Item No,Case Weight,GTIN,Raw Material\n"
            "\"Onion Yellow Diced 3/8\"\" 4/5lb\",Sales IM:0010967,0010967,20,10074865916063,Onion Yellow\n")
    up = client.post("/api/table/qb/upload", files={"file": ("qb.csv", text.encode(), "text/csv")})
    assert up.status_code == 200
    x = client.get("/api/table/qb/download", params={"fmt": "original"})
    lines = x.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Item list exported 2026-11-01,,,,,"
    assert lines[1] == "Description,Item,Customer Item No,Case Weight,GTIN,Raw Material"
    assert lines[2].startswith('"Onion Yellow Diced 3/8"" 4/5lb",Sales IM:0010967,0010967,20')


def test_original_download_without_upload_and_existing_formats_still_work(client):
    qb = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n"
          "Sales IM:0010967,Onion Yellow Diced 3/8in 4/5lb,0010967,20,10074865916063,Onion Yellow\n").encode()
    client.post("/api/table/qb/upload", files={"file": ("qb.csv", qb, "text/csv")})
    g = client.post("/api/generate", json={"mode": "replace"}).json()
    assert "sheet" not in g or not g["sheet"]["template"]
    g["rows"][0]["_s"] = {"Item No.": {"b": 1}}
    g["rows"][0]["_f"] = {"No. of copy": "=1+1"}
    client.post("/api/table/bc/save", json={"rows": g["rows"], "columns": g["columns"]})
    x = client.get("/api/table/bc/download", params={"fmt": "original"})
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    header = [c.value for c in ws[1]]
    assert "Item No." in header and "Match Score" not in header  # platform-only columns stay out
    j = header.index("Item No.") + 1
    assert ws.cell(row=2, column=j).font.b
    assert ws.cell(row=2, column=header.index("No. of copy") + 1).value == "=1+1"
    for fmt in ("bc", "xlsx", "csv"):  # the existing downloads are unchanged
        assert client.get("/api/table/bc/download", params={"fmt": fmt}).status_code == 200
