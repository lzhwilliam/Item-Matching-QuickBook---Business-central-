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
| Imperial Label (BC) | An Excel-like grid. Double-click or type to edit; paste ranges from Excel; Ctrl+Z undoes. Rows are shaded by confidence (none = 100% → red ≤ 40%). Error cells have a red outline and warning cells an orange one; hover a cell to see why. Clicking a row opens a panel to pick a different FS item. Download the Imperial configuration package in your uploaded file's layout. Select rows and click ✓ Resolve to clear problems you have fixed. |
| QuickBooks Source | An editable source list with upload, download and history. |
| Learned Rules | What the matcher has learned from your edits, with usage counts. You can turn rules on or off, delete them, or export/import them as JSON. |
| Settings | The FS database and the defaults (Customer No. C0004, Customer Name, No. of copy, first Entry No., …). |

## Excel features (Imperial Label and QuickBooks tables)
- **Formulas**: type `=` to enter one (e.g. `=D4*2`); the formula bar shows the selected cell's address and formula. Formulas uploaded from Excel/OneDrive are kept. Cells are calculated in the browser by HyperFormula (`static/vendor`, GPLv3). A green corner marks a formula cell. **Fill down** (Ctrl+D) copies the top cell down, shifting references like Excel. Addresses follow the uploaded sheet, so sorting or filtering the grid doesn't change them.
- **Formatting**: bold/italic/underline (Ctrl+B/I/U), font and fill colour, number format, alignment, wrap, Clear formats, column widths.
- **Undo/redo**: Ctrl+Z / Ctrl+Y (or the buttons) step back through edits, pastes, formulas, formats, row changes, Generate, uploads and restores.
- **Keys like Excel**:
  - Typing on a cell replaces its content. F2 or a double-click edits it.
  - Enter / Shift+Enter move down or up; Tab / Shift+Tab move right or left. While typing, the arrow keys finish the entry and move.
  - Esc cancels. Delete clears; Backspace clears and starts editing. Ctrl+Enter puts the value into every selected cell.
  - Ctrl+click or Ctrl+drag adds another selection; Shift+click extends.
- **Copy / cut / paste**: Ctrl+C or Ctrl+X marks the cells with a moving dashed border (Esc clears it). Pasting from Excel ignores Excel's trailing line break. One value pasted into a selection fills every cell, and a block that runs past the last row adds rows. Copying inside the platform also carries formulas, with references shifted, and formats.
- **Resolve** (Imperial Label): select rows, for example by Shift+clicking row numbers, and click **✓ Resolve**. Their current problems stop shading the row and leave the error list. New problems still show. **Unresolve** brings them back.
- **Download** (one button per table: Imperial configuration package, Imperial QB item list, FS database) gives the table back in the file you uploaded. It keeps the other sheets, title rows, column order and widths, styles, formulas and helper columns, and applies the formats you set in the platform. Excel recalculates when it opens the file. A CSV upload comes back as CSV in its own layout. A BC table never uploaded comes back in the standard BC layout (title rows, header on row 3). Formula cells also carry their last calculated value, so Business Central's import reads the numbers. The original upload is stored in `data/templates/`. Tables uploaded before this feature need to be uploaded once more to get their layout.
- The **Imperial configuration package** also carries the matcher's view, so problem rows are easy to filter in Excel:
  - columns Match Score → Errors / warnings
  - rows shaded by confidence, with red (error) and orange (warning) cell outlines; resolved problems are left out
  - a single table with a filter button on every column, so Filter by Color works

  It is a working file, not a Business Central-compliant package: the XML map is not kept.
- The FS Mix Database tab has **Download FS database** (the original file).

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
