/* QuickBooks → BC Imperial Label Matcher — front end */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TABLES = {
  bc: { grid: null, el: "#gridBc", save: "#saveBc", search: "#searchBc", version: 0, timer: null, saving: false, again: false, suppress: 0,
        undo: [], redo: [], order: [], layout: null, sheet: null, widths: {}, lastState: null, pending: false, nextLabel: null, barCell: null },
  qb: { grid: null, el: "#gridQb", save: "#saveQb", search: "#searchQb", version: 0, timer: null, saving: false, again: false, suppress: 0,
        undo: [], redo: [], order: [], layout: null, sheet: null, widths: {}, lastState: null, pending: false, nextLabel: null, barCell: null },
};
const BC_META = ["Match Score", "Match Method", "Matched FS Description", "QB Description"];
const WIDTHS = {
  "Entry No.": 70, "Customer No.": 90, "Customer Name": 120, "Item No.": 100, "Item Description": 300,
  "Cut Size and Method": 150, "Customer Item Code": 110, "No. of Inner PKG": 90, "LB per BAG": 80, GTIN: 135,
  "No. of copy": 70, "Match Score": 70, "Match Method": 230, "Matched FS Description": 280, "QB Description": 280,
  Item: 160, Description: 320, "Customer Item No": 120, "Case Weight": 90, "Raw Material": 160,
};

// ---------------------------------------------------------------- utils
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

function heat(conf) {
  if (conf == null || conf >= 100) return "";
  const t = Math.max(0, Math.min(1, (100 - conf) / 60)); // 100 → none, ≤40 → full red
  return `rgba(220, 38, 38, ${(0.08 + t * 0.5).toFixed(3)})`;
}

function setSaveState(name, text, cls = "") {
  const el = $(TABLES[name].save);
  el.textContent = text;
  el.className = "save-state " + cls;
}

// ---------------------------------------------------------------- grid building
function decorate(rows, analysis) {
  return rows.map((r, i) => {
    const a = analysis ? analysis[i] : null;
    const out = { ...r };
    if (a) {
      out._conf = a.confidence;
      out._errs = a.errors;
      out._errtext = a.errors.map((e) => `${e.sev === "error" ? "⛔" : "⚠"} ${e.msg}`).join("  ·  ");
    }
    return out;
  });
}

function cellFormatter(cell) {
  const field = cell.getField();
  const errs = (cell.getRow().getData()._errs || []).filter((e) => e.col === field);
  const el = cell.getElement();
  el.classList.toggle("cell-err", errs.some((e) => e.sev === "error"));
  el.classList.toggle("cell-warn", !errs.some((e) => e.sev === "error") && errs.length > 0);
  const d = cell.getRow().getData();
  const st = d._s?.[field];
  paintCell(el, st, d._f?.[field]);
  return esc(formatNumber(cell.getValue(), st?.numFmt));
}

function cellTooltip(e, cell) {
  const field = cell.getField();
  const errs = (cell.getRow().getData()._errs || []).filter((x) => x.col === field);
  if (errs.length) return errs.map((x) => `[${x.sev}] ${x.msg}`).join("\n");
  const f = cell.getRow().getData()._f?.[field];
  if (f) return f;
  const v = cell.getValue();
  return v && String(v).length > 30 ? String(v) : "";
}

function buildColumns(name, columns) {
  const cols = [];
  if (name === "bc") {
    cols.push({
      title: "Conf.", field: "_conf", width: 64, editor: false, hozAlign: "center", sorter: "number",
      formatter: (c) => (c.getValue() == null ? "" : `<span class="conf-pill">${c.getValue()}%</span>`),
    });
  }
  for (const c of columns) {
    const meta = BC_META.includes(c);
    cols.push({
      title: c, field: c, width: TABLES[name].widths[c] ? Math.round(TABLES[name].widths[c] * 7 + 5) : (WIDTHS[c] || 120),
      editor: meta && c !== "Match Score" ? false : cellEditor,
      formatter: cellFormatter, tooltip: cellTooltip, cssClass: meta ? "cell-meta" : "",
    });
  }
  if (name === "bc") {
    cols.push({
      title: "Errors / warnings", field: "_errtext", width: 420, editor: false,
      formatter: (c) => {
        const errs = c.getRow().getData()._errs || [];
        return errs.map((e) => `<span class="${e.sev === "error" ? "err-text" : "warn-text"}">${e.sev === "error" ? "⛔" : "⚠"} ${esc(e.msg)}</span>`).join(" · ");
      },
      tooltip: (e, c) => c.getValue() || "",
    });
  }
  return cols;
}

function makeGrid(name, payload) {
  const T = TABLES[name];
  const rows = decorate(payload.rows, payload.analysis);
  if (T.grid) T.grid.destroy();
  T.columns = payload.columns;
  T.order = payload.rows.map((r) => r._rid);
  T.layout = payload.layout || null;
  T.sheet = payload.sheet || null;
  T.widths = { ...(payload.sheet?.col_widths || {}) };
  T.lastState = null;
  T.barCell = null;
  T.grid = new Tabulator(T.el, {
    data: rows,
    index: "_rid",
    nestedFieldSeparator: false, // BC headers contain "." (Item No., Entry No.)
    headerSortClickElement: "icon",
    columns: buildColumns(name, payload.columns),
    layout: "fitData",
    height: "100%",
    reactiveData: false,
    history: false, // undo/redo is the platform's own stack (covers formats, formulas and server actions)
    selectableRange: 1,
    selectableRangeColumns: true,
    selectableRangeRows: true,
    selectableRangeClearCells: true,
    editTriggerEvent: "dblclick",
    clipboard: true,
    clipboardCopyStyled: false,
    clipboardCopyConfig: { rowHeaders: false, columnHeaders: false },
    clipboardCopyRowRange: "range",
    clipboardPasteParser: "range",
    clipboardPasteAction: "range",
    rowHeader: { resizable: false, frozen: true, width: 44, hozAlign: "center", formatter: "rownum", headerSort: false, editor: false },
    columnDefaults: { headerSort: true, resizable: "header", headerHozAlign: "left" },
    rowFormatter: (row) => {
      const d = row.getData();
      row.getElement().style.backgroundColor = heat(d._conf);
    },
    placeholder: name === "bc" ? "No rows yet. Upload the QuickBooks list, then click “Generate from QuickBooks”." : "Upload the QuickBooks item list (.csv / .xlsx).",
  });
  T.grid.on("dataChanged", () => onUserChange(name));
  T.grid.on("rowDeleted", () => onUserChange(name, "Delete row"));
  T.grid.on("rowAdded", () => onUserChange(name, "Add row"));
  T.grid.on("clipboardPasted", () => { T.nextLabel = "Paste"; });
  T.grid.on("columnResized", (col) => {
    if (T.suppress) return;
    T.widths[col.getField()] = Math.round(((col.getWidth() - 5) / 7) * 100) / 100;
    onUserChange(name, "Column width");
  });
  T.grid.on("cellClick", (e, cell) => refreshBar(name, cell));
  T.grid.on("rangeChanged", (range) => { const c = [range.getCells() || []].flat(2)[0]; if (c) refreshBar(name, c); });
  if (name === "bc") T.grid.on("cellClick", (e, cell) => showDetail(cell.getRow()));
  T.grid.on("tableBuilt", () => {
    applyFilters(name);
    recalc(name).forEach((d) => T.grid.getRow(d._rid)?.reformat());
    T.lastState = snapshot(name);
    refreshBar(name);
  });
  setVersion(name, payload);
}

function setVersion(name, p) {
  TABLES[name].version = p.version_no;
  const when = p.created_at ? new Date(p.created_at).toLocaleString() : "";
  let git = "";
  if (p.git && p.git.commit) git = ` · git ${p.git.commit}` + (p.git.pushed === false ? " (push failed)" : p.git.pushed ? " (pushed)" : "");
  setSaveState(name, p.version_no ? `v${p.version_no} · ${p.source || ""} · ${when}${git}` : "no versions yet", p.git && !p.git.ok ? "err" : "");
  if (p.git && !p.git.ok) setSaveState(name, `v${p.version_no} saved · git commit failed: ${p.git.error}`, "err");
  if (name === "bc") updateBadge(p.analysis || []);
}

function updateBadge(analysis) {
  const n = analysis.filter((a) => a.errors.some((e) => e.sev === "error")).length;
  $("#badgeBc").textContent = n ? String(n) : "";
  $("#badgeBc").title = `${n} rows with errors`;
}

// apply a server response to an existing grid without losing scroll/selection
function applyServer(name, payload) {
  const T = TABLES[name];
  const rows = decorate(payload.rows, payload.analysis);
  T.suppress++;
  Promise.resolve(T.grid.updateData(rows)).finally(() => {
    T.grid.getRows().forEach((r) => r.reformat());
    T.suppress--;
    if (!T.pending && !T.timer) T.lastState = snapshot(name);
  });
  setVersion(name, payload);
  if (payload.learned && payload.learned.length) {
    toast("🧠 " + payload.learned.join("\n"));
    loadRules();
  }
}

function currentRows(name) {
  return orderedData(name).map((r) => {
    const o = {};
    for (const [k, v] of Object.entries(r)) {
      if (!k.startsWith("_")) o[k] = v ?? "";
      else if (ROW_META.includes(k) && v !== undefined && v !== null) o[k] = clone(v);
    }
    return o;
  });
}

function scheduleSave(name) {
  const T = TABLES[name];
  setSaveState(name, "unsaved changes…", "dirty");
  clearTimeout(T.timer);
  T.timer = setTimeout(() => saveNow(name), 900);
}

async function saveNow(name, note = "") {
  const T = TABLES[name];
  if (T.saving) { T.again = true; return; }
  T.saving = true;
  setSaveState(name, "saving…", "dirty");
  try {
    const p = await postJSON(`/api/table/${name}/save`, { columns: T.columns, rows: currentRows(name), note, col_widths: T.widths });
    applyServer(name, p);
    if (name === "bc" && currentDetailRid) {
      const row = T.grid.getRow(currentDetailRid);
      if (row) showDetail(row);
    }
  } catch (e) {
    setSaveState(name, "save failed: " + e.message, "err");
  } finally {
    T.saving = false;
    if (T.again) { T.again = false; saveNow(name); }
  }
}

async function loadTable(name) {
  const p = await api(`/api/table/${name}`);
  makeGrid(name, p);
}

// ---------------------------------------------------------------- filters
function applyFilters(name) {
  const T = TABLES[name];
  const q = ($(T.search).value || "").toLowerCase().trim();
  const only = name === "bc" && $("#onlyIssues").checked;
  if (!q && !only) { T.grid.clearFilter(); return; }
  T.grid.setFilter((d) => {
    if (only && !(d._errs && d._errs.length) && !(d._conf != null && d._conf < 100)) return false;
    if (!q) return true;
    return Object.entries(d).some(([k, v]) => k !== "_errs" && String(v ?? "").toLowerCase().includes(q));
  });
}

// ---------------------------------------------------------------- detail panel (BC)
let currentDetailRid = null;
async function showDetail(row) {
  const d = row.getData();
  currentDetailRid = d._rid;
  const box = $("#detail");
  box.classList.remove("hidden");
  const errs = (d._errs || []).map((e) => `<li class="${e.sev === "error" ? "err-text" : "warn-text"}">${esc(e.col)}: ${esc(e.msg)}</li>`).join("") || "<li>None</li>";
  box.innerHTML = `
    <div class="cols">
      <div>
        <h4>Entry ${esc(d["Entry No."])} · ${esc(d["Customer Item Code"])} · confidence ${d._conf ?? "—"}%</h4>
        <table>
          <tr><th>QuickBooks</th><td>${esc(d["QB Description"])}</td></tr>
          <tr><th>Matched FS item</th><td>${esc(d["Item No."])} — ${esc(d["Matched FS Description"])}</td></tr>
          <tr><th>How matched</th><td>${esc(d["Match Method"])} (score ${esc(d["Match Score"])})</td></tr>
        </table>
        <b>Issues</b><ul>${errs}</ul>
      </div>
      <div>
        <b>Pick a different FS item</b> <span class="hint">(choosing one sets Item No., refills the fields and teaches the matcher)</span>
        <div class="find"><input id="findInput" value="${esc(d["QB Description"])}" placeholder="Search FS database by description or TPC…"><button id="findBtn">Search</button></div>
        <div id="cands" class="hint">Searching…</div>
      </div>
    </div>`;
  const run = async () => {
    const q = $("#findInput").value;
    const list = await api(`/api/reference/search?q=${encodeURIComponent(q)}&limit=12`);
    $("#cands").innerHTML = list.length
      ? `<table>${list.map((it) => `<tr><td><button data-use="${esc(it.item_no)}">Use</button></td><td><b>${esc(it.item_no)}</b></td><td>${esc(it.description)}</td><td>${esc(it.status)}</td></tr>`).join("")}</table>`
      : "No candidates.";
    $$("[data-use]", $("#cands")).forEach((b) => b.addEventListener("click", () => {
      const r = TABLES.bc.grid.getRow(currentDetailRid);
      if (r) { r.update({ "Item No.": b.dataset.use }); toast(`Item No. → ${b.dataset.use}. Saving and learning…`); }
    }));
  };
  $("#findBtn").addEventListener("click", run);
  $("#findInput").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  run().catch((e) => { $("#cands").textContent = e.message; });
}

// ---------------------------------------------------------------- spreadsheet layer (formulas, formats, undo/redo)
// Row fields kept with the data:  _f {column: "=formula"} anchored at Excel row _fr,
// _s {column: {b,i,u,color,fill,numFmt,align,wrap}} (key present = the platform owns that cell's format),
// _row = the row in the uploaded file. Cell addresses follow the uploaded sheet (T.layout).
const ROW_META = ["_rid", "_f", "_fr", "_s", "_row"];
const UNDO_LIMIT = 100;

const colLetter = (n) => { let s = ""; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; };
const clone = (o) => (typeof structuredClone === "function" ? structuredClone(o) : JSON.parse(JSON.stringify(o)));
const isFormula = (v) => typeof v === "string" && v.length > 1 && v[0] === "=";

/** Rows in stored (sheet) order — sorting/filtering in the grid never changes cell addresses. */
function orderedData(name) {
  const T = TABLES[name];
  if (!T.grid) return [];
  const rows = T.grid.getRows().map((r) => r.getData());
  const by = new Map(rows.map((d) => [d._rid, d]));
  T.order = (T.order || []).filter((id) => by.has(id));
  const known = new Set(T.order);
  rows.forEach((d) => { if (!known.has(d._rid)) { T.order.push(d._rid); known.add(d._rid); } });
  return T.order.map((id) => by.get(id));
}

function addrRow(name, rid) {
  const T = TABLES[name];
  const i = (T.order || []).indexOf(rid);
  return (T.layout?.header_row || 1) + 1 + (i < 0 ? T.order.length : i);
}

function cellAddress(name, row, field) {
  const j = TABLES[name].layout?.col_pos?.[field];
  return j ? `${colLetter(j)}${addrRow(name, row.getData()._rid)}` : "";
}

/** Move a formula down/up by dr rows like Excel copy/fill (relative rows shift, $rows stay). */
function shiftFormula(f, dr) {
  if (!dr || !f) return f;
  return f.split(/("(?:[^"]|"")*")/).map((part, i) => (i % 2 ? part : part.replace(
    /(^|[^A-Za-z0-9_.$])(\$?)([A-Za-z]{1,3})(\$?)(\d+)(?![A-Za-z0-9_(])/g,
    (m, pre, d1, col, d2, row) => {
      if (d2) return m;
      const n = parseInt(row, 10) + dr;
      return n < 1 ? `${pre}#REF!` : `${pre}${d1}${col}${d2}${n}`;
    }))).join("");
}

/** Re-anchor a row's formulas to where the row is now. */
function rebase(name, d) {
  const er = addrRow(name, d._rid);
  if (d._f && d._fr && d._fr !== er) for (const c of Object.keys(d._f)) d._f[c] = shiftFormula(d._f[c], er - d._fr);
  d._fr = er;
}

function setFormula(name, d, c, f) {
  if (d._f) rebase(name, d); else d._fr = addrRow(name, d._rid);
  d._f = { ...(d._f || {}), [c]: f };
}

function dropFormula(d, c) {
  if (!d._f) return;
  delete d._f[c];
  if (!Object.keys(d._f).length) { delete d._f; delete d._fr; }
}

function toEngine(v) {
  if (v == null || v === "") return null;
  const s = String(v).trim();
  if (/^-?(0|[1-9]\d*)(\.\d+)?$/.test(s)) return Number(s); // keeps codes like 0010967 as text
  if (s[0] === "=") return " " + s;
  return s;
}

/** Recalculate every formula with HyperFormula; returns the rows whose values changed. */
function recalc(name) {
  const T = TABLES[name];
  if (!T.layout || typeof HyperFormula === "undefined") return [];
  const rows = orderedData(name);
  if (!rows.some((d) => d._f && Object.keys(d._f).length)) return [];
  const { col_pos: pos, header_row: hr } = T.layout;
  const maxC = Math.max(1, ...Object.values(pos));
  const sheet = [];
  for (let r = 1; r < hr; r++) sheet.push([]);
  const head = new Array(maxC).fill(null);
  for (const [c, j] of Object.entries(pos)) head[j - 1] = c;
  sheet.push(head);
  rows.forEach((d, i) => {
    const er = hr + 1 + i;
    const line = new Array(maxC).fill(null);
    for (const [c, j] of Object.entries(pos)) {
      const f = d._f?.[c];
      line[j - 1] = f ? shiftFormula(f, er - (d._fr || er)) : toEngine(d[c]);
    }
    sheet.push(line);
  });
  let hf;
  try { hf = HyperFormula.buildFromArray(sheet, { licenseKey: "gpl-v3" }); } catch (e) { console.warn("formula engine:", e); return []; }
  const changed = new Set();
  rows.forEach((d, i) => {
    for (const c of Object.keys(d._f || {})) {
      if (!(c in pos)) continue;
      const v = hf.getCellValue({ sheet: 0, row: hr + i, col: pos[c] - 1 });
      let out;
      if (v !== null && typeof v === "object") {
        // refers to another sheet of the workbook: keep the value Excel calculated
        if (d._f[c].includes("!") && d[c] !== "" && d[c] != null && !isFormula(d[c])) continue;
        out = String(v.value || "#ERROR!");
      } else if (typeof v === "number") out = String(+v.toPrecision(15));
      else if (typeof v === "boolean") out = v ? "TRUE" : "FALSE";
      else out = v == null ? "" : String(v);
      if (String(d[c] ?? "") !== out) { d[c] = out; changed.add(d); }
    }
  });
  hf.destroy();
  return [...changed];
}

function formatNumber(v, fmt) {
  if (v == null || v === "" || !fmt || fmt === "General" || fmt === "@") return v;
  const s = String(v).trim();
  const n = Number(s);
  const bare = fmt.replace(/"[^"]*"|\[[^\]]*\]/g, "");
  if (/[dmy]/i.test(bare) && !/0/.test(bare)) {
    const dt = s && isFinite(n) ? new Date(Math.round((n - 25569) * 86400000)) : new Date(s.replace(" ", "T"));
    return isNaN(dt) ? v : dt.toLocaleDateString(undefined, s && isFinite(n) ? { timeZone: "UTC" } : {});
  }
  if (!s || !isFinite(n)) return v;
  const pct = bare.includes("%");
  const dec = (bare.match(/\.(0+)/) || [null, ""])[1].length;
  const txt = (pct ? n * 100 : n).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: bare.includes(",") });
  const cur = (fmt.match(/[$€£¥]/) || [""])[0];
  return cur + txt + (pct ? "%" : "");
}

function paintCell(el, st, hasFormula) {
  el.style.fontWeight = st?.b ? "700" : "";
  el.style.fontStyle = st?.i ? "italic" : "";
  el.style.textDecoration = st?.u ? "underline" : "";
  el.style.color = st?.color ? "#" + st.color : "";
  el.style.backgroundColor = st?.fill ? "#" + st.fill : "";
  el.style.textAlign = st?.align || "";
  el.style.whiteSpace = st?.wrap ? "normal" : "";
  el.classList.toggle("cell-fx", !!hasFormula);
}

/** In-cell editor that shows the formula (not its result) for formula cells. */
function cellEditor(cell, onRendered, success, cancel) {
  const d = cell.getRow().getData();
  const input = document.createElement("input");
  input.type = "text";
  input.className = "cell-edit";
  input.value = d._f?.[cell.getField()] ?? (cell.getValue() ?? "");
  onRendered(() => { input.focus({ preventScroll: true }); input.setSelectionRange(input.value.length, input.value.length); });
  let done = false;
  const ok = () => { if (!done) { done = true; success(input.value); } };
  input.addEventListener("blur", ok);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "Tab") ok();
    else if (e.key === "Escape") { done = true; cancel(); }
  });
  return input;
}

// ---- undo / redo (one stack per table; server actions undo via History restore)
function snapshot(name) {
  const T = TABLES[name];
  return { rows: clone(orderedData(name)), widths: { ...(T.widths || {}) } };
}

function pushUndo(name, entry) {
  const T = TABLES[name];
  T.undo.push(entry);
  if (T.undo.length > UNDO_LIMIT) T.undo.shift();
  T.redo = [];
  updateUndoButtons(name);
}

/** A grid change made by the user: record undo, turn "=..." entries into formulas, recalc, save. */
function onUserChange(name, label) {
  const T = TABLES[name];
  if (T.suppress || !T.grid) return;
  if (label) T.nextLabel = T.nextLabel || label;
  if (T.pending) return;
  T.pending = true;
  setTimeout(() => {
    T.pending = false;
    const lbl = T.nextLabel || "Edit";
    T.nextLabel = null;
    if (T.lastState) pushUndo(name, { label: lbl, snap: T.lastState });
    absorbEdits(name);
    const touched = new Set(recalc(name));
    touched.forEach((d) => T.grid.getRow(d._rid)?.reformat());
    T.lastState = snapshot(name);
    refreshBar(name);
    scheduleSave(name);
  }, 0);
}

/** Values typed/pasted/cleared since the last state: "=..." becomes a formula,
 *  a new value over an unchanged formula replaces it (like Excel). */
function absorbEdits(name) {
  const T = TABLES[name];
  const prev = new Map((T.lastState?.rows || []).map((r) => [r._rid, r]));
  for (const d of orderedData(name)) {
    const p = prev.get(d._rid);
    let hit = false;
    for (const c of T.columns || []) {
      const v = d[c];
      if (isFormula(v)) { setFormula(name, d, c, v.trim()); hit = true; continue; }
      if (d._f?.[c] && p && (p._f?.[c] === d._f[c]) && String(p[c] ?? "") !== String(v ?? "")) { dropFormula(d, c); hit = true; }
    }
    if (hit) T.grid.getRow(d._rid)?.reformat();
  }
}

function applySnapshot(name, snap) {
  const T = TABLES[name];
  T.suppress++;
  T.order = snap.rows.map((r) => r._rid);
  T.widths = { ...snap.widths };
  Promise.resolve(T.grid.replaceData(clone(snap.rows))).finally(() => {
    for (const col of T.grid.getColumns()) {
      const f = col.getField();
      if (T.widths[f]) col.setWidth(Math.round(T.widths[f] * 7 + 5));
    }
    T.suppress--;
    applyFilters(name);
    T.lastState = snapshot(name);
    refreshBar(name);
    scheduleSave(name);
  });
}

async function flushSave(name) {
  const T = TABLES[name];
  if (T.timer) { clearTimeout(T.timer); T.timer = null; await saveNow(name); }
}

async function restoreTo(name, version) {
  await flushSave(name);
  const p = version > 0
    ? await postJSON(`/api/table/${name}/restore`, { version })
    : await postJSON(`/api/table/${name}/save`, { columns: TABLES[name].columns, rows: [], source: "undo", note: "Undo to empty table" });
  makeGrid(name, p);
  return p;
}

function pushServer(name, label, from, to) {
  if (from === to) return;
  pushUndo(name, { label, server: { from, to } });
}

async function undo(name) {
  const T = TABLES[name];
  const e = T.undo.pop();
  if (!e) { toast("Nothing to undo."); return; }
  try {
    if (e.server) {
      await restoreTo(name, e.server.from);
      T.redo.push(e);
    } else {
      T.redo.push({ label: e.label, snap: snapshot(name) });
      applySnapshot(name, e.snap);
    }
    toast(`Undo: ${e.label}`, 1500);
  } catch (err) { T.undo.push(e); toast("Undo failed: " + err.message, 6000); }
  updateUndoButtons(name);
}

async function redo(name) {
  const T = TABLES[name];
  const e = T.redo.pop();
  if (!e) { toast("Nothing to redo."); return; }
  try {
    if (e.server) {
      await restoreTo(name, e.server.to);
      T.undo.push(e);
    } else {
      T.undo.push({ label: e.label, snap: snapshot(name) });
      applySnapshot(name, e.snap);
    }
    toast(`Redo: ${e.label}`, 1500);
  } catch (err) { T.redo.push(e); toast("Redo failed: " + err.message, 6000); }
  updateUndoButtons(name);
}

function updateUndoButtons(name) {
  const T = TABLES[name];
  const bar = $(`[data-sheetbar="${name}"]`);
  if (!bar) return;
  const u = $('[data-act="undo"]', bar), r = $('[data-act="redo"]', bar);
  const lu = T.undo[T.undo.length - 1], lr = T.redo[T.redo.length - 1];
  u.disabled = !lu; r.disabled = !lr;
  u.title = lu ? `Undo: ${lu.label} (Ctrl+Z)` : "Undo (Ctrl+Z)";
  r.title = lr ? `Redo: ${lr.label} (Ctrl+Y)` : "Redo (Ctrl+Y)";
}

// ---- formatting
function rangeParts(name) {
  const T = TABLES[name];
  return (T.grid?.getRanges ? T.grid.getRanges() : []).map((r) => ({
    rows: r.getRows(),
    fields: r.getColumns().map((c) => c.getField()).filter((f) => f && !f.startsWith("_")),
  })).filter((p) => p.rows.length && p.fields.length);
}

function applyFormat(name, label, fn) {
  const parts = rangeParts(name);
  if (!parts.length) { toast("Select cells first (click or drag in the table)."); return; }
  const cells = [];
  for (const p of parts) for (const row of p.rows) for (const f of p.fields) cells.push([row, f]);
  const all = cells.map(([row, f]) => row.getData()._s?.[f] || {});
  for (const [row, f] of cells) {
    const d = row.getData();
    d._s = d._s || {};
    const st = { ...(d._s[f] || {}) };
    fn(st, all);
    for (const k of Object.keys(st)) if (st[k] === undefined || st[k] === "" || st[k] === 0) delete st[k];
    d._s[f] = st; // an empty object still marks the format as set by the platform ("Clear formats")
  }
  new Set(cells.map(([row]) => row)).forEach((r) => r.reformat());
  onUserChange(name, `${label} (${cells.length} cell${cells.length === 1 ? "" : "s"})`);
}

const toggle = (key) => (st, all) => { st[key] = all.every((s) => s[key]) ? 0 : 1; };

function fillDown(name) {
  const parts = rangeParts(name);
  if (!parts.length) { toast("Select the cells to fill (the top row is copied down)."); return; }
  const T = TABLES[name];
  let n = 0;
  for (const p of parts) {
    let src = p.rows[0], targets = p.rows.slice(1);
    if (!targets.length) { // one row selected: copy from the row above, like Excel
      const view = T.grid.getRows("active");
      const i = view.indexOf(src);
      if (i <= 0) continue;
      targets = [src];
      src = view[i - 1];
    }
    const s = src.getData();
    const sAddr = addrRow(name, s._rid);
    for (const row of targets) {
      const d = row.getData();
      const tAddr = addrRow(name, d._rid);
      for (const f of p.fields) {
        const sf = s._f?.[f];
        if (sf) setFormula(name, d, f, shiftFormula(sf, sAddr - (s._fr || sAddr) + (tAddr - sAddr)));
        else { dropFormula(d, f); d[f] = s[f] ?? ""; }
        if (s._s && s._s[f] !== undefined) { d._s = d._s || {}; d._s[f] = { ...s._s[f] }; }
        n++;
      }
      row.reformat();
    }
  }
  if (n) onUserChange(name, `Fill down (${n} cells)`);
}

// ---- formula bar
function refreshBar(name, cell) {
  const T = TABLES[name];
  if (cell) T.barCell = cell;
  const bar = $(`[data-sheetbar="${name}"]`);
  if (!bar || !T.grid) return;
  const c = T.barCell;
  const input = $(".fx-input", bar), addr = $(".fx-addr", bar);
  let row = null;
  try { row = c ? T.grid.getRow(c.getRow().getData()._rid) : null; } catch (_) { row = null; }
  if (!row) { addr.textContent = "—"; if (document.activeElement !== input) input.value = ""; return; }
  const f = c.getField();
  const d = row.getData();
  addr.textContent = cellAddress(name, row, f) || f;
  if (document.activeElement !== input) input.value = d._f?.[f] ?? (d[f] ?? "");
}

function commitBar(name) {
  const T = TABLES[name];
  const c = T.barCell;
  if (!c) return;
  const row = T.grid.getRow(c.getRow().getData()._rid);
  if (!row) return;
  const f = c.getField();
  const d = row.getData();
  const text = $(`[data-sheetbar="${name}"] .fx-input`).value;
  if ((d._f?.[f] ?? String(d[f] ?? "")) === text) return;
  if (!isFormula(text) && d._f?.[f]) {
    dropFormula(d, f);
    if (String(d[f] ?? "") === text) { row.reformat(); onUserChange(name, "Edit"); return; }
  }
  row.update({ [f]: text });
}

const SWATCHES = ["000000", "FFFFFF", "C00000", "FF0000", "FFC000", "FFFF00", "92D050", "00B050", "00B0F0", "0070C0", "002060", "7030A0", "F2F2F2", "D9D9D9", "C6EFCE", "FFEB9C", "FFC7CE", "DDEBF7"];
const NUM_FORMATS = [["General", "General"], ["@", "Text"], ["0", "Number"], ["0.00", "Number 0.00"], ["#,##0.00", "Thousands 1,234.00"],
  ["$#,##0.00", "Currency $"], ["0%", "Percent"], ["0.00%", "Percent 0.00%"], ["m/d/yyyy", "Date"]];

function renderSheetbar(name) {
  const bar = $(`[data-sheetbar="${name}"]`);
  const sw = (kind) => `<div class="menu swatches">${SWATCHES.map((c) => `<button type="button" data-swatch="${kind}" data-color="${c}" style="background:#${c}" title="#${c}"></button>`).join("")}
      <button type="button" class="none" data-swatch="${kind}" data-color="">${kind === "fill" ? "No fill" : "Automatic"}</button>
      <label class="more">More… <input type="color" data-swatch-input="${kind}"></label></div>`;
  bar.innerHTML = `
    <div class="fmt-tools">
      <button type="button" data-act="undo" title="Undo (Ctrl+Z)" disabled>↶ Undo</button>
      <button type="button" data-act="redo" title="Redo (Ctrl+Y)" disabled>↷ Redo</button>
      <span class="sep"></span>
      <button type="button" data-act="b" title="Bold (Ctrl+B)"><b>B</b></button>
      <button type="button" data-act="i" title="Italic (Ctrl+I)"><i>I</i></button>
      <button type="button" data-act="u" title="Underline (Ctrl+U)"><u>U</u></button>
      <div class="dropdown"><button type="button" title="Font colour"><span class="ico-a">A</span>▾</button>${sw("color")}</div>
      <div class="dropdown"><button type="button" title="Fill colour"><span class="ico-fill"></span>▾</button>${sw("fill")}</div>
      <select data-act="numFmt" title="Number format">${NUM_FORMATS.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>
      <button type="button" data-act="left" title="Align left">⫷</button>
      <button type="button" data-act="center" title="Center">≡</button>
      <button type="button" data-act="right" title="Align right">⫸</button>
      <button type="button" data-act="wrap" title="Wrap text">Wrap</button>
      <button type="button" data-act="clear" title="Clear formats">Clear formats</button>
      <span class="sep"></span>
      <button type="button" data-act="fill" title="Fill down (Ctrl+D): copies the top cell's value or formula down the selection">Fill down</button>
    </div>
    <div class="fbar"><span class="fx-addr" title="Cell address in the uploaded sheet">—</span><span class="fx">fx</span>
      <input class="fx-input" placeholder="Select a cell — type a value, or a formula such as =D4*2, then Enter"></div>`;
  const act = {
    undo: () => undo(name), redo: () => redo(name), fill: () => fillDown(name),
    b: () => applyFormat(name, "Bold", toggle("b")), i: () => applyFormat(name, "Italic", toggle("i")),
    u: () => applyFormat(name, "Underline", toggle("u")), wrap: () => applyFormat(name, "Wrap text", toggle("wrap")),
    left: () => applyFormat(name, "Align left", (st) => { st.align = "left"; }),
    center: () => applyFormat(name, "Center", (st) => { st.align = "center"; }),
    right: () => applyFormat(name, "Align right", (st) => { st.align = "right"; }),
    clear: () => applyFormat(name, "Clear formats", (st) => { for (const k of Object.keys(st)) delete st[k]; }),
  };
  $$("button[data-act]", bar).forEach((b) => b.addEventListener("click", () => act[b.dataset.act]?.()));
  const setColor = (kind, hex) => applyFormat(name, kind === "fill" ? "Fill colour" : "Font colour", (st) => { st[kind] = hex || undefined; });
  $$("[data-swatch]", bar).forEach((b) => b.addEventListener("click", () => setColor(b.dataset.swatch, b.dataset.color)));
  $$("[data-swatch-input]", bar).forEach((inp) => inp.addEventListener("change", () => setColor(inp.dataset.swatchInput, inp.value.slice(1).toUpperCase())));
  const nf = $('[data-act="numFmt"]', bar);
  nf.addEventListener("change", () => {
    const v = nf.value;
    applyFormat(name, "Number format", (st) => { st.numFmt = v === "General" ? undefined : v; });
  });
  const input = $(".fx-input", bar);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commitBar(name); input.blur(); }
    else if (e.key === "Escape") { input.blur(); refreshBar(name); }
  });
  input.addEventListener("blur", () => setTimeout(() => refreshBar(name), 0));
  updateUndoButtons(name);
}

function activeSheet() {
  const p = $(".panel.active");
  return p && p.id === "tab-bc" ? "bc" : p && p.id === "tab-qb" ? "qb" : null;
}

function bindSheetKeys() {
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const name = activeSheet();
    if (!name || $("#historyDlg").open || $("#confirmDlg").open) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    const k = e.key.toLowerCase();
    const run = { z: e.shiftKey ? () => redo(name) : () => undo(name), y: () => redo(name),
      b: () => applyFormat(name, "Bold", toggle("b")), i: () => applyFormat(name, "Italic", toggle("i")),
      u: () => applyFormat(name, "Underline", toggle("u")), d: () => fillDown(name) }[k];
    if (run) { e.preventDefault(); e.stopPropagation(); run(); }
  }, true);
}

// ---------------------------------------------------------------- toolbar actions
async function uploadFile(name, file) {
  const fd = new FormData();
  fd.append("file", file);
  toast(`Uploading ${file.name}…`);
  try {
    await flushSave(name);
    const before = TABLES[name].version;
    const p = await api(`/api/table/${name}/upload`, { method: "POST", body: fd });
    makeGrid(name, p);
    pushServer(name, `Upload ${file.name}`, before, p.version_no);
    toast(`Uploaded ${file.name}: ${p.rows.length} rows → v${p.version_no}` + (p.learned?.length ? `\n🧠 ${p.learned.join("\n")}` : ""));
    if (p.learned?.length) loadRules();
    refreshState();
  } catch (e) { toast("Upload failed: " + e.message, 6000); }
}

function addRow(name) {
  const T = TABLES[name];
  const blank = Object.fromEntries(T.columns.map((c) => [c, ""]));
  blank._rid = Math.random().toString(16).slice(2, 12);
  if (name === "bc") {
    const nums = T.grid.getData().map((r) => parseInt(r["Entry No."], 10)).filter((n) => !isNaN(n));
    blank["Entry No."] = String((nums.length ? Math.max(...nums) : 0) + 1);
    blank["Match Score"] = 100;
    blank["Match Method"] = "Manual row";
  }
  T.grid.addRow(blank, false).then((row) => T.grid.scrollToRow(row, "center", false));
}

/** In-page replacement for window.confirm(): native popups are blocked in some
 *  embedded browsers (e.g. the Claude app's browser pane), which silently cancelled actions. */
let settleConfirm = null; // the open prompt's "answer" function, if any

function askConfirm(message, okLabel = "OK") {
  if (settleConfirm) settleConfirm(false); // never leave an earlier prompt (and its handlers) hanging
  const dlg = $("#confirmDlg");
  $("#confirmMsg").textContent = message;
  $("#confirmOk").textContent = okLabel;
  return new Promise((resolve) => {
    const done = (v) => {
      if (settleConfirm !== done) return;
      settleConfirm = null;
      $("#confirmOk").removeEventListener("click", ok);
      $("#confirmCancel").removeEventListener("click", no);
      dlg.removeEventListener("keydown", key);
      dlg.removeEventListener("cancel", esc);
      if (dlg.open) dlg.close();
      resolve(v);
    };
    const ok = () => done(true);
    const no = () => done(false);
    // Esc is handled here rather than via the dialog's "close" event, which some embedded browsers never fire
    const esc = (e) => { e.preventDefault(); done(false); };
    const key = (e) => { if (e.key === "Escape") esc(e); };
    settleConfirm = done;
    $("#confirmOk").addEventListener("click", ok);
    $("#confirmCancel").addEventListener("click", no);
    dlg.addEventListener("keydown", key);
    dlg.addEventListener("cancel", esc);
    dlg.showModal();
    $("#confirmOk").focus(); // Enter = OK
  });
}

async function deleteSelected(name) {
  const T = TABLES[name];
  const rows = new Set();
  (T.grid.getRanges ? T.grid.getRanges() : []).forEach((r) => r.getRows().forEach((row) => rows.add(row)));
  if (!rows.size) { toast("Select cells in the rows to delete first (click / drag on the row numbers)."); return; }
  if (!(await askConfirm(`Delete ${rows.size} row(s)? (A new version is saved; you can restore from History.)`, "Delete"))) return;
  T.suppress++;
  Promise.all([...rows].map((r) => r.delete())).finally(() => { T.suppress--; onUserChange(name, `Delete ${rows.size} row(s)`); });
}

async function generate() {
  const mode = $("#genMode").value;
  if (mode === "replace" && TABLES.bc.grid.getDataCount()
      && !(await askConfirm("Regenerate the whole Imperial label table from the QuickBooks source? (The current table stays in History.)", "Regenerate"))) return;
  $("#btnGenerate").disabled = true;
  toast("Matching…");
  try {
    await flushSave("bc");
    const before = TABLES.bc.version;
    const p = await postJSON("/api/generate", { mode });
    makeGrid("bc", p);
    pushServer("bc", "Generate", before, p.version_no);
    const s = p.stats;
    toast(`Generated v${p.version_no}: ${s.total} rows — ${s.code} by customer code, ${s.description} by description, ${s.learned} by learned rule, ${s.none} unmatched` + (s.skipped ? `, ${s.skipped} kept` : ""), 6000);
  } catch (e) { toast("Generate failed: " + e.message, 6000); }
  finally { $("#btnGenerate").disabled = false; }
}

// ---------------------------------------------------------------- history
let histName = "bc";
async function openHistory(name) {
  histName = name;
  $("#historyTitle").textContent = `History — ${name === "bc" ? "Imperial Label (BC)" : "QuickBooks Source"}`;
  $("#versionDiff").innerHTML = `<p class="hint">Pick a version to see what changed.</p>`;
  const list = await api(`/api/table/${name}/versions`);
  $("#versionList").innerHTML = list.length ? list.map((v, i) => `
    <div class="ver ${i === 0 ? "current" : ""}">
      <div><b>v${v.version_no}</b> · ${esc(v.source)} ${i === 0 ? "<i>(current)</i>" : ""}</div>
      <div class="meta">${new Date(v.created_at).toLocaleString()} · ${v.changed_cells} change(s)</div>
      <div class="meta">${esc(v.note || "")}</div>
      <div class="acts">
        <button data-vdiff="${v.version_no}">Changes</button>
        ${i === 0 ? "" : `<button data-vrestore="${v.version_no}">Restore</button>`}
        <a class="btn" href="/api/table/${name}/download?fmt=original&version=${v.version_no}" title="As uploaded, with formulas &amp; formats">Download</a>
        <a class="btn" href="/api/table/${name}/download?fmt=xlsx&version=${v.version_no}" title="Platform layout (.xlsx)">Platform .xlsx</a>
      </div>
    </div>`).join("") : "<p>No versions yet.</p>";
  $$("[data-vdiff]").forEach((b) => b.addEventListener("click", () => showDiff(+b.dataset.vdiff)));
  $$("[data-vrestore]").forEach((b) => b.addEventListener("click", () => restore(+b.dataset.vrestore)));
  $("#historyDlg").showModal();
}

async function showDiff(v) {
  const d = await api(`/api/table/${histName}/diff?version=${v}`);
  const rows = d.changed.map((c) => `<tr><td>${esc(c.key)}</td><td>${esc(c.col)}</td><td class="diff-old">${esc(c.old)}</td><td class="diff-new">${esc(c.new)}</td></tr>`).join("");
  $("#versionDiff").innerHTML = `
    <h4>v${d.version} vs v${d.against}</h4>
    <p>${d.added.length} row(s) added${d.added.length ? ": " + esc(d.added.slice(0, 30).join(", ")) + (d.added.length > 30 ? "…" : "") : ""}<br>
       ${d.removed.length} row(s) removed${d.removed.length ? ": " + esc(d.removed.slice(0, 30).join(", ")) : ""}<br>
       ${d.changed.length} cell(s) changed</p>
    ${d.changed.length ? `<table class="diff-table"><tr><th>Row</th><th>Column</th><th>Before</th><th>After</th></tr>${rows}</table>` : ""}`;
}

async function restore(v) {
  if (!(await askConfirm(`Restore v${v}? This saves it as a new version, so it can be undone.`, "Restore"))) return;
  await flushSave(histName);
  const before = TABLES[histName].version;
  const p = await postJSON(`/api/table/${histName}/restore`, { version: v });
  makeGrid(histName, p);
  pushServer(histName, `Restore v${v}`, before, p.version_no);
  toast(`Restored v${v} as v${p.version_no}`);
  openHistory(histName);
}

// ---------------------------------------------------------------- rules
let rulesGrid, logGrid;
const KIND = { code_override: "Customer code → item", desc_alias: "Description → item", field_override: "Field override", token_rule: "Token pattern" };
function ruleSummary(r) {
  const v = r.value;
  switch (r.kind) {
    case "code_override": return `${v.customer_code || r.rkey} → ${v.item_no}${v.was ? ` (was ${v.was})` : ""}`;
    case "desc_alias": return `“${v.description}” → ${v.item_no}`;
    case "field_override": return `${v.customer_code} · ${v.column}: “${v.was}” → “${v.value}” (item ${v.item_no})`;
    case "token_rule": return `${v.column}: “${v.old}” → “${v.new || "(remove)"}”`;
    default: return JSON.stringify(v);
  }
}
async function loadRules() {
  const { rules, edit_log } = await api("/api/rules");
  $("#badgeRules").textContent = rules.length ? String(rules.length) : "";
  const data = rules.map((r) => ({ ...r, kindLabel: KIND[r.kind] || r.kind, summary: ruleSummary(r) }));
  if (!rulesGrid) {
    rulesGrid = new Tabulator("#gridRules", {
      data, layout: "fitColumns", height: "100%", placeholder: "No learned rules yet — edit the Imperial Label table.",
      columns: [
        { title: "On", field: "enabled", width: 50, hozAlign: "center", formatter: "tickCross",
          cellClick: async (e, c) => { await postJSON(`/api/rules/${c.getRow().getData().id}`, { enabled: !c.getValue() }); loadRules(); } },
        { title: "Type", field: "kindLabel", width: 170 },
        { title: "Rule", field: "summary", minWidth: 300, formatter: "plaintext", tooltip: true },
        { title: "Used", field: "hits", width: 60, hozAlign: "right" },
        { title: "Learned from", field: "origin", width: 150 },
        { title: "Updated", field: "updated_at", width: 160 },
        { title: "", width: 70, headerSort: false, formatter: () => "<button>Delete</button>",
          cellClick: async (e, c) => { if (await askConfirm("Delete this rule?", "Delete")) { await api(`/api/rules/${c.getRow().getData().id}`, { method: "DELETE" }); loadRules(); } } },
      ],
    });
    logGrid = new Tabulator("#gridLog", {
      data: edit_log, layout: "fitColumns", height: "100%", placeholder: "No edits yet.",
      columns: [
        { title: "When", field: "created_at", width: 160 },
        { title: "Version", field: "version_no", width: 70 },
        { title: "Row (customer code)", field: "row_key", width: 140 },
        { title: "Column", field: "column_name", width: 150 },
        { title: "Before", field: "old_value", formatter: "plaintext", tooltip: true },
        { title: "After", field: "new_value", formatter: "plaintext", tooltip: true },
      ],
    });
  } else {
    rulesGrid.replaceData(data);
    logGrid.replaceData(edit_log);
  }
}

// ---------------------------------------------------------------- navigation / sidebar
const MOBILE = window.matchMedia("(max-width: 760px)");

function showTab(t) {
  $$(".tab[data-tab]").forEach((x) => x.classList.toggle("active", x.dataset.tab === t));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + t));
  if (TABLES[t]?.grid) TABLES[t].grid.redraw(true);
  if (t === "rules") loadRules();
  if (t === "fs") { if (fsData) fsGrid?.redraw(true); else loadFs(); }
  localStorage.setItem("qbbc.tab", t);
  if (MOBILE.matches) setNavOpen(false);
}

function setNavOpen(open) {
  document.body.classList.toggle("nav-open", open);
  $("#navToggle").setAttribute("aria-expanded", String(open));
}

function setCollapsed(collapsed, remember = true) {
  document.body.classList.toggle("nav-collapsed", collapsed);
  $("#collapseBtn").title = collapsed ? "Expand menu" : "Collapse menu";
  $("#collapseBtn").setAttribute("aria-label", $("#collapseBtn").title);
  $("#navToggle").setAttribute("aria-expanded", String(!collapsed));
  if (remember) localStorage.setItem("qbbc.navCollapsed", collapsed ? "1" : "0");
  setTimeout(redrawVisibleGrids, 220); // after the width transition
}

function redrawVisibleGrids() {
  Object.values(TABLES).forEach((T) => T.grid && T.grid.element.offsetParent && T.grid.redraw());
  [fsGrid, rulesGrid, logGrid].forEach((g) => g && g.element.offsetParent && g.redraw());
}

function bindSidebar() {
  const saved = localStorage.getItem("qbbc.navCollapsed");
  setCollapsed(saved == null ? window.innerWidth < 1100 : saved === "1", false);
  $("#navToggle").addEventListener("click", () => {
    if (MOBILE.matches) setNavOpen(!document.body.classList.contains("nav-open"));
    else setCollapsed(!document.body.classList.contains("nav-collapsed"));
  });
  $("#collapseBtn").addEventListener("click", () => setCollapsed(!document.body.classList.contains("nav-collapsed")));
  $("#navBackdrop").addEventListener("click", () => setNavOpen(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("nav-open")) setNavOpen(false); });
  MOBILE.addEventListener("change", () => { setNavOpen(false); setTimeout(redrawVisibleGrids, 50); });
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(redrawVisibleGrids, 150); });
}

// ---------------------------------------------------------------- FS Mix Database (read-only browser)
let fsGrid = null, fsData = null, fsTimer = null, fsTerms = [], fsRe = null;
const normH = (h) => String(h || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const reEsc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

async function loadFs() {
  $("#fsCount").textContent = "loading…";
  try {
    fsData = await api("/api/reference/sheet");
  } catch (e) { $("#fsCount").textContent = "failed: " + e.message; return; }
  const { columns, rows } = fsData;
  fsData.rowObjs = rows.map((r) => {
    const o = { _row: r[0] };
    columns.forEach((_, i) => { o["c" + i] = r[i + 1] ?? ""; });
    o._hay = r.slice(1).join("\u0001").toLowerCase();
    return o;
  });
  fsData.itemCol = columns.findIndex((c) => normH(c) === "tpcnumberbusinesscentral");
  fsData.statusCol = columns.findIndex((c) => normH(c) === "itemstatus");
  const sel = $("#fsStatus");
  const statuses = fsData.statusCol < 0 ? [] : [...new Set(fsData.rowObjs.map((o) => o["c" + fsData.statusCol]).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">All statuses</option>` + statuses.map((v) => `<option>${esc(v)}</option>`).join("");
  sel.hidden = !statuses.length;
  $("#fsNote").innerHTML = !columns.length
    ? `<span class="err-text">FS database not loaded yet.</span> Upload it under <b>Settings</b>.`
    : fsData.partial
      ? `Showing the key columns only. Re-upload the FS database under <b>Settings</b> to see every column of the <b>Mixed Database</b> sheet.`
      : "";
  buildFsGrid();
}

function fsHighlight(cell) {
  const v = esc(cell.getValue());
  return fsRe && v ? v.replace(fsRe, "<mark>$1</mark>") : v;
}

function buildFsGrid() {
  const { columns } = fsData;
  const filters = $("#fsColFilters").checked;
  const cols = [{ title: "Row", field: "_row", width: 58, hozAlign: "right", frozen: true, sorter: "number", cssClass: "cell-meta",
    tooltip: () => "Row number in the Excel sheet" }];
  const order = columns.map((_, i) => i);
  if (fsData.itemCol > 0) { order.splice(fsData.itemCol, 1); order.unshift(fsData.itemCol); }
  for (const i of order) {
    const col = {
      title: columns[i], field: "c" + i, maxWidth: 380, minWidth: 60, formatter: fsHighlight, sorter: "alphanum",
      tooltip: (e, c) => (String(c.getValue() || "").length > 40 ? String(c.getValue()) : ""),
      frozen: i === fsData.itemCol && !MOBILE.matches,
    };
    if (filters) { col.headerFilter = "input"; col.headerFilterPlaceholder = "filter…"; }
    cols.push(col);
  }
  if (fsGrid) fsGrid.destroy();
  fsGrid = new Tabulator("#gridFs", {
    data: fsData.rowObjs, index: "_row", columns: cols, layout: "fitData", height: "100%",
    nestedFieldSeparator: false, columnDefaults: { headerSort: true, resizable: "header", headerHozAlign: "left" },
    selectableRange: 1, selectableRangeColumns: true, selectableRangeRows: true,
    clipboard: "copy", clipboardCopyStyled: false, clipboardCopyRowRange: "range",
    clipboardCopyConfig: { rowHeaders: false, columnHeaders: false },
    placeholder: fsData.columns.length ? "No rows match your search." : "FS database not loaded — upload it under Settings.",
  });
  fsGrid.on("tableBuilt", applyFsFilter);
  fsGrid.on("dataFiltered", (f, rows) => updateFsCount(rows.length));
}

function applyFsFilter() {
  if (!fsGrid || !fsData) return;
  fsTerms = ($("#searchFs").value || "").toLowerCase().split(/\s+/).filter(Boolean);
  fsRe = fsTerms.length ? new RegExp("(" + fsTerms.map((t) => reEsc(esc(t))).join("|") + ")", "gi") : null;
  const status = $("#fsStatus").value;
  const sk = "c" + fsData.statusCol;
  if (!fsTerms.length && !status) fsGrid.clearFilter();
  else fsGrid.setFilter((d) => (!status || d[sk] === status) && fsTerms.every((t) => d._hay.includes(t)));
  fsGrid.getRows("visible").forEach((r) => r.reformat());
  updateFsCount(fsGrid.getDataCount("active"));
}

function updateFsCount(n) {
  if (!fsData) return;
  const total = fsData.rowObjs.length;
  $("#fsCount").textContent = `${n === total ? total : `${n} of ${total}`} rows` + (fsData.source_file ? ` · ${fsData.source_file}` : "");
}

// ---------------------------------------------------------------- settings / state
async function refreshState() {
  const s = await api("/api/state");
  const r = s.reference;
  $("#refStatus").textContent = r.loaded ? `FS database: ${r.count} items (${r.source_file || "cached"})` : "FS database not loaded — see Settings";
  $("#refInfo").innerHTML = r.loaded ? `Loaded <b>${r.count}</b> items from <b>${esc(r.source_file || "cache")}</b>.` : `<span class="err-text">Not loaded yet.</span>`;
  const f = $("#settingsForm");
  for (const [k, v] of Object.entries(s.settings)) if (f.elements[k]) f.elements[k].value = v;
  $("#badgeRules").textContent = s.rules ? String(s.rules) : "";
  return s;
}

function bind() {
  $$(".tab[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  bindSidebar();
  renderSheetbar("bc");
  renderSheetbar("qb");
  bindSheetKeys();
  $("#searchFs").addEventListener("input", () => { clearTimeout(fsTimer); fsTimer = setTimeout(applyFsFilter, 150); });
  $("#fsStatus").addEventListener("change", applyFsFilter);
  $("#fsColFilters").addEventListener("change", () => { if (fsData) buildFsGrid(); });
  $("#fsClear").addEventListener("click", () => {
    $("#searchFs").value = ""; $("#fsStatus").value = "";
    if (fsGrid) fsGrid.clearHeaderFilter();
    applyFsFilter();
  });
  $$("[data-upload]").forEach((inp) => inp.addEventListener("change", () => {
    if (inp.files[0]) uploadFile(inp.dataset.upload, inp.files[0]);
    inp.value = "";
  }));
  $$("[data-dl]").forEach((a) => a.addEventListener("click", () => {
    window.location.href = `/api/table/${a.dataset.dl}/download?fmt=${a.dataset.fmt}`;
  }));
  $$("[data-addrow]").forEach((b) => b.addEventListener("click", () => addRow(b.dataset.addrow)));
  $$("[data-delrows]").forEach((b) => b.addEventListener("click", () => deleteSelected(b.dataset.delrows)));
  $$("[data-history]").forEach((b) => b.addEventListener("click", () => openHistory(b.dataset.history)));
  $("[data-close]").addEventListener("click", () => $("#historyDlg").close());
  $("#btnGenerate").addEventListener("click", generate);
  $("#onlyIssues").addEventListener("change", () => applyFilters("bc"));
  $("#searchBc").addEventListener("input", () => applyFilters("bc"));
  $("#searchQb").addEventListener("input", () => applyFilters("qb"));
  $("#settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const vals = Object.fromEntries(new FormData(e.target).entries());
    await postJSON("/api/settings", vals);
    toast("Settings saved — applied on the next Generate.");
  });
  $("#refUpload").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    toast("Reading FS database… (large file, a few seconds)", 20000);
    try {
      const r = await api("/api/reference", { method: "POST", body: fd });
      toast(`FS database loaded: ${r.count} items`);
      refreshState();
      loadTable("bc");
      fsData = null;
      if ($("#tab-fs").classList.contains("active")) loadFs();
    } catch (err) { toast("Failed: " + err.message, 6000); }
    e.target.value = "";
  });
  $("#importRules").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const r = await api("/api/rules/import", { method: "POST", body: fd });
    toast(`Imported ${r.imported} rules`);
    loadRules();
    e.target.value = "";
  });
  window.addEventListener("beforeunload", (e) => {
    if (Object.values(TABLES).some((T) => T.timer && $(T.save).classList.contains("dirty"))) { e.preventDefault(); e.returnValue = ""; }
  });
}

(async function init() {
  bind();
  await refreshState();
  await Promise.all([loadTable("bc"), loadTable("qb")]);
  const last = localStorage.getItem("qbbc.tab");
  if (last && last !== "bc" && $("#tab-" + last)) showTab(last);
})();
