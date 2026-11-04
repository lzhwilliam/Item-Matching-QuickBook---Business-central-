# Item Matching — QuickBooks → Business Central (Imperial label)

A local web app that turns the **QuickBooks Imperial item list** into rows for the **BC "Imperial Item Label Master" (table 50024)**. It checks every generated row against the **FS full database**.

## Run

**Windows:** double-click `run.bat`. The first run creates `.venv` and installs the packages; after that it starts the app and opens the browser.

**Any OS:**
```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8765
```
Open http://localhost:8765. Data lives in `./data/`, which is git-ignored (set `QBBC_DATA_DIR` to move it).

First time:
1. **Settings → Upload FS database .xlsx**. The `Mixed Database` sheet is read by header name and cached in `data/reference.json`.
2. **QuickBooks Source → Upload** the QuickBooks CSV/XLSX (Item, Description, Customer Item No, Case Weight, GTIN, Raw Material).
3. **Imperial Label (BC) → Generate from QuickBooks**.

## Tabs
| Tab | What it does |
|---|---|
| Imperial Label (BC) | An Excel-like grid. Double-click or type to edit; paste ranges from Excel; Ctrl+Z undoes. Rows are shaded by confidence (none = 100% → red ≤ 40%). Error cells have a red outline and warning cells an orange one; hover a cell to see why. Clicking a row opens a panel to pick a different FS item. Downloads: BC import layout, full XLSX with confidence/errors, or CSV. |
| QuickBooks Source | An editable source list with upload, download and history. |
| Learned Rules | What the matcher has learned from your edits, with usage counts. You can turn rules on or off, delete them, or export/import them as JSON. |
| Settings | The FS database and the defaults (Customer No. C0004, Customer Name, No. of copy, first Entry No., …). |

Each table has its own **version history**. Every save, upload, generate or restore creates a version, and you can see what changed in each cell, restore, or download any version.

## Git: one commit per change (app/gitsync.py)
Every saved version is written to `table_history/` (`imperial_label_bc.csv`, `quickbooks_source.csv`, `learned_rules.json`) and **committed to your local git repo**. Each commit message lists the cells that changed, and the commit id shows next to the save time in the toolbar.
- Only `table_history/` is ever staged, so code you're editing is never swept into these commits.
- To see the history, run `git log -p table_history/`, or open it in GitHub Desktop or VS Code.
- **Settings → Git: push each commit** (default **Off**) also pushes each commit. Only turn it on if the GitHub repo is private, because it uploads your item data.
- Git needs your name and email set once: `git config --global user.name "…"` and `git config --global user.email "…"`.

## Matching (app/matcher.py)
1. **Learned rules**: customer code → item, or QuickBooks description → item (100%).
2. **Customer code**: the QuickBooks `Customer Item No` vs the FS DB `Sysco imperial` codes. Confidence is lowered when the cut, size or pack disagree.
3. **Description**: parsed with the rules in the FS item-extraction guide (produce / cut method / cut size / other / pack). Scoring is weighted, and close ties are penalised.

BC fields:
- `Item Description` = the full FS description.
- `Cut Size and Method` = `3/8" DICED`.
- `a/b lb` → `aBAG` / `bLB` (the QuickBooks pack is used first).
- GTIN comes from QuickBooks.

## Error detection
- Same item with two GTINs; one GTIN on different items.
- Invalid GTIN (length or check digit).
- Duplicate customer code or Entry No.
- Item not in the FS DB, or marked "Not ready".
- Pack × weight disagrees with the QuickBooks Case Weight or the FS Total lb.
- Cut size or method differs from the QuickBooks description.
- QuickBooks Raw Material ≠ FS RM.
- Blank required fields.

Each error costs 30 points of confidence and each warning 10.

## Learning (app/learning.py)
- **Item No. edits** → customer-code and description rules. The row's other fields are refilled from the FS item.
- **Other field edits** → a field override for that customer code, applied while the Item No. stays the same.
- **The same word change in Cut Size and Method / Item Description seen N times** → a global token rule (e.g. `WEDGED` → `WEDGE`).

## Tests
```bash
python -m pytest -q
```
