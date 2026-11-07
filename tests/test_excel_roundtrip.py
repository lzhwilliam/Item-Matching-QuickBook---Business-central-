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
    header = [c.value for c in ws[3]]  # BC configuration-package layout: header on row 3
    assert "Item No." in header and header[-1] == "Errors / warnings"  # matcher columns at the end
    j = header.index("Item No.") + 1
    assert ws.cell(row=4, column=j).font.b
    assert ws.cell(row=4, column=header.index("No. of copy") + 1).value == "=1+1"
    for fmt in ("bc", "xlsx", "csv"):  # the existing downloads are unchanged
        assert client.get("/api/table/bc/download", params={"fmt": fmt}).status_code == 200


def _without_calc_settings(xlsx: bytes) -> bytes:
    """Like files exported by Business Central: no <calcPr> in workbook.xml."""
    import re
    import zipfile
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/workbook.xml":
                data = re.sub(rb"<calcPr[^>]*/>", b"", data)
                assert b"calcPr" not in data
            dst.writestr(item, data)
    return out.getvalue()


def test_template_without_calc_settings_and_formula_values_are_kept(client):
    content = _without_calc_settings(qb_workbook())
    assert openpyxl.load_workbook(io.BytesIO(content)).calculation is None
    p = client.post("/api/table/qb/upload", files={"file": ("bc export.xlsx", content, XLSX)}).json()
    rows = p["rows"]
    rows[0]["Double"] = "40"  # what the browser calculated for =D4*2
    client.post("/api/table/qb/save", json={"rows": rows, "columns": p["columns"]})
    x = client.get("/api/table/qb/download", params={"fmt": "original"})
    assert x.status_code == 200, x.text
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb.calculation.fullCalcOnLoad and wb["Items"]["E4"].value == "=D4*2"
    cached = openpyxl.load_workbook(io.BytesIO(x.content), data_only=True)["Items"]
    assert cached["E4"].value == 40  # readers that don't recalculate (e.g. BC import) still see the number


def test_bc_never_uploaded_downloads_in_bc_layout(client):
    qb = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n"
          "Sales IM:0010967,Onion Yellow Diced 3/8in 4/5lb,0010967,20,10074865916063,Onion Yellow\n").encode()
    client.post("/api/table/qb/upload", files={"file": ("qb.csv", qb, "text/csv")})
    client.post("/api/generate", json={"mode": "replace"})
    x = client.get("/api/table/bc/download", params={"fmt": "original"})
    assert 'filename="Imperial_configuration_package_v1.xlsx"' in x.headers["content-disposition"]
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    assert [ws.cell(row=1, column=j).value for j in (1, 2, 3)] == ["IMPERIAL LABEL", "Imperial Item Label Master", "50024"]
    assert ws.cell(row=3, column=1).value == "Entry No." and ws.cell(row=4, column=7).value == "0010967"
    assert [c.value for c in ws[3]][11:] == ["Match Score", "Match Method", "Matched FS Description", "QB Description", "Errors / warnings"]


def test_resolved_rows_hide_known_problems_but_show_new_ones(client):
    qb = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n"
          "Sales IM:0010967,Onion Yellow Diced 3/8in 4/5lb,0010967,99,10074865916063,Onion Yellow\n").encode()
    client.post("/api/table/qb/upload", files={"file": ("qb.csv", qb, "text/csv")})
    g = client.post("/api/generate", json={"mode": "replace"}).json()
    a = g["analysis"][0]
    assert a["errors"] and a["confidence"] < 100  # case weight 99 disagrees with the pack
    rows = g["rows"]
    rows[0]["_resolved"] = {"errors": [f'{e["col"]}|{e["msg"]}' for e in a["errors"]], "at": "2026-11-05"}
    s = client.post("/api/table/bc/save", json={"rows": rows, "columns": g["columns"]}).json()
    a = s["analysis"][0]
    assert a["resolved"] and a["confidence"] == 100 and all(e["resolved"] for e in a["errors"])
    d = client.get("/api/table/bc/diff", params={"version": s["version_no"]}).json()
    assert any(c["col"] == "(row) (resolved)" for c in d["changed"])
    rows = s["rows"]
    rows[0]["GTIN"] = "123"  # a new problem after resolving
    a = client.post("/api/table/bc/save", json={"rows": rows, "columns": g["columns"]}).json()["analysis"][0]
    new = [e for e in a["errors"] if not e.get("resolved")]
    assert new and any(e["col"] == "GTIN" for e in new) and a["confidence"] < 100


def test_word_pattern_needs_two_different_rows(tmp_path):
    from app.db import Store
    st = Store(tmp_path / "t.db")
    pat = "Item Description::(2/5LB)=>(1/5lb)"
    st.log_edit("bc", 1, "9145764", "Item Description", "a", "b", pat)
    st.log_edit("bc", 2, "9145764", "Item Description", "a", "b", pat)  # same row fixed twice
    assert st.pattern_count(pat) == 1
    st.log_edit("bc", 3, "7156947", "Item Description", "a", "b", pat)
    assert st.pattern_count(pat) == 2


def bc_package_workbook() -> bytes:
    """A synthetic BC configuration package: title rows, header on row 3, an XML-mapped table."""
    from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo, XMLColumnProps
    from app.matcher import BC_COLUMNS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Imperial Item Label Master"
    ws["A1"], ws["B1"], ws["C1"] = "IMPERIAL LABEL", "Imperial Item Label Master", "50024"
    for j, h in enumerate(BC_COLUMNS, start=1):
        ws.cell(row=3, column=j, value=h)
    row = ["1", "C0004", "Baugh Supply", "TPC1", 'Onion Yellow Diced 3/8" 4/5lb', '3/8" DICED', "0010967",
           "4BAG", "5LB", "10074865916063", "0"]
    for j, v in enumerate(row, start=1):
        ws.cell(row=4, column=j, value=v)
    t = Table(displayName="Table5", ref=f"A3:{openpyxl.utils.get_column_letter(len(BC_COLUMNS))}4", tableType="xml")
    t.tableColumns = [TableColumn(id=k + 1, name=h, xmlColumnPr=XMLColumnProps(
        mapId=1, xpath=f"/DataList/Item/{k}", xmlDataType="string")) for k, h in enumerate(BC_COLUMNS)]
    t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(t)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_configuration_package_has_matcher_colours_columns_and_one_filterable_table(client):
    from app.matcher import BC_COLUMNS
    assert client.post("/api/table/bc/upload", files={"file": ("BC package.xlsx", bc_package_workbook(), XLSX)}).status_code == 200
    qb = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n"
          'Sales IM:0010967,"Onion Yellow Diced 3/8"" 4/5lb",0010967,20,10074865916063,Onion Yellow\n'
          "Sales IM:6864110,Carrot Diced 1/2in 1/5lb,6864110,5,BADGTIN,Carrot\n"      # invalid GTIN -> error
          "Sales IM:6523450,Celery Diced 1/4in 1/5lb,6523450,99,10074865935248,Celery\n").encode()  # weight off
    client.post("/api/table/qb/upload", files={"file": ("qb.csv", qb, "text/csv")})
    g = client.post("/api/generate", json={"mode": "replace"}).json()
    by_code = {r["Customer Item Code"]: i for i, r in enumerate(g["rows"])}
    cel = by_code["6523450"]
    rows = g["rows"]
    rows[cel]["_resolved"] = {"errors": [f'{e["col"]}|{e["msg"]}' for e in g["analysis"][cel]["errors"]], "at": "x"}
    client.post("/api/table/bc/save", json={"rows": rows, "columns": g["columns"]})

    x = client.get("/api/table/bc/download", params={"fmt": "original"})
    assert x.status_code == 200, x.text
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    assert ws["A1"].value == "IMPERIAL LABEL"
    header = [c.value for c in ws[3]]
    assert header[:len(BC_COLUMNS)] == BC_COLUMNS
    assert header[len(BC_COLUMNS):] == ["Match Score", "Match Method", "Matched FS Description",
                                        "QB Description", "Errors / warnings"]
    (name, ref), = ws.tables.items()
    t = ws.tables[name]
    assert ref == "A3:P6" and t.tableType is None
    assert [c.name for c in t.tableColumns] == header and all(c.xmlColumnPr is None for c in t.tableColumns)

    def row_of(code):
        return next(r for r in range(4, 7) if ws.cell(row=r, column=7).value == code)
    carrot, celery, onion = row_of("6864110"), row_of("6523450"), row_of("0010967")
    gtin_col = BC_COLUMNS.index("GTIN") + 1
    assert ws.cell(row=carrot, column=1).fill.fill_type == "solid"            # shaded by confidence
    assert ws.cell(row=carrot, column=gtin_col).border.left.color.rgb == "FFC62828"  # red error outline
    assert "⛔" in ws.cell(row=carrot, column=16).value
    assert isinstance(ws.cell(row=carrot, column=12).value, (int, float))      # Match Score is a number
    assert ws.cell(row=celery, column=1).fill.fill_type is None               # resolved: not shaded
    assert ws.cell(row=celery, column=16).value == "✓ resolved"
    assert ws.cell(row=onion, column=16).value in (None, "")


def v30_workbook(rows: list[dict], resolved_code: str) -> bytes:
    """Shaped like the user's 'Imperial label BC DB_v30': matcher columns, Errors / warnings and
    a User Comment column inside Table5, header notes, column B hidden, text format, and the
    matcher's shading baked into a resolved row by an earlier download."""
    from openpyxl.comments import Comment
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from app.matcher import BC_COLUMNS, BC_META
    cols = BC_COLUMNS + BC_META + ["Errors / warnings", "User Comment"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Imperial Item Label Master"
    ws["A1"], ws["B1"], ws["C1"] = "IMPERIAL LABEL", "Imperial Item Label Master", "50024"
    for j, h in enumerate(cols, start=1):
        ws.cell(row=3, column=j, value=h)
    ws["A3"].comment = Comment("Integer", "WILLIAM")
    for i, r in enumerate(rows):
        er = 4 + i
        done = r["Customer Item Code"] == resolved_code
        for j, h in enumerate(cols, start=1):
            v = {"Errors / warnings": "✓ resolved" if done else "",
                 "User Comment": "one GTIN from customer, one from us" if done else ""}.get(h, r.get(h, ""))
            c = ws.cell(row=er, column=j, value=v if v != "" else None)
            if j <= len(BC_COLUMNS):
                c.number_format = "@"
            if done:
                c.fill = PatternFill("solid", fgColor="FFFFE3E3")
    ws.column_dimensions["B"].hidden = True
    t = Table(displayName="Table5", ref=f"A3:Q{3 + len(rows)}")
    t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(t)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_v30_layout_upload_keeps_comments_resolved_and_order(client):
    qb = ("Item,Description,Customer Item No,Case Weight,GTIN,Raw Material\n"
          'Sales IM:0010967,"Onion Yellow Diced 3/8"" 4/5lb",0010967,20,10074865916063,Onion Yellow\n'
          "Sales IM:6523450,Celery Diced 1/4in 1/5lb,6523450,99,10074865935248,Celery\n").encode()  # weight off
    client.post("/api/table/qb/upload", files={"file": ("qb.csv", qb, "text/csv")})
    g = client.post("/api/generate", json={"mode": "replace"}).json()
    cel = next(i for i, r in enumerate(g["rows"]) if r["Customer Item Code"] == "6523450")
    assert g["analysis"][cel]["confidence"] < 100

    up = client.post("/api/table/bc/upload", files={"file": ("Imperial label BC DB_v30.xlsx",
                                                             v30_workbook(g["rows"], "6523450"), XLSX)}).json()
    assert "Errors / warnings" not in up["columns"] and up["columns"][-1] == "User Comment"
    row = up["rows"][cel]
    assert row["_rid"] == g["rows"][cel]["_rid"]                      # same row, not a new one
    assert row["User Comment"] == "one GTIN from customer, one from us"
    assert row["_resolved"] and up["analysis"][cel]["confidence"] == 100   # "✓ resolved" kept
    assert "_s" not in row                                            # matcher shading / @ not frozen in
    assert isinstance(row["Match Score"], int)
    pos = up["layout"]["col_pos"]
    assert pos["Errors / warnings"] == 16 and pos["User Comment"] == 17 and pos["Match Score"] == 12

    g2 = client.post("/api/generate", json={"mode": "replace"}).json()
    row = g2["rows"][cel]
    assert "User Comment" in g2["columns"] and row["User Comment"] == "one GTIN from customer, one from us"
    assert row["_resolved"] and g2["analysis"][cel]["confidence"] == 100

    x = client.get("/api/table/bc/download", params={"fmt": "original"})
    assert x.status_code == 200, x.text
    ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
    header = [c.value for c in ws[3]]
    assert header[15:] == ["Errors / warnings", "User Comment"] and len(header) == 17
    r = next(r for r in (4, 5) if ws.cell(row=r, column=7).value == "6523450")
    assert ws.cell(row=r, column=16).value == "✓ resolved"
    assert ws.cell(row=r, column=17).value == "one GTIN from customer, one from us"
    assert ws.cell(row=r, column=1).fill.fill_type is None            # resolved: no shading
    assert ws.cell(row=r, column=1).number_format == "@"              # the file's text format stays
    assert ws["A3"].comment and ws["A3"].comment.text == "Integer"
    assert ws.column_dimensions["B"].hidden
    assert {n: ref for n, ref in ws.tables.items()} == {"Table5": "A3:Q5"}
