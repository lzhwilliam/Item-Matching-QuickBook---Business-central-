/* QuickBooks → BC Imperial Label Matcher — front end */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TABLES = {
  bc: { grid: null, el: "#gridBc", save: "#saveBc", search: "#searchBc", version: 0, timer: null, saving: false, again: false, suppress: 0 },
  qb: { grid: null, el: "#gridQb", save: "#saveQb", search: "#searchQb", version: 0, timer: null, saving: false, again: false, suppress: 0 },
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
  return esc(cell.getValue());
}

function cellTooltip(e, cell) {
  const field = cell.getField();
  const errs = (cell.getRow().getData()._errs || []).filter((x) => x.col === field);
  if (errs.length) return errs.map((x) => `[${x.sev}] ${x.msg}`).join("\n");
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
      title: c, field: c, width: WIDTHS[c] || 120, editor: meta && c !== "Match Score" ? false : "input",
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
  T.grid = new Tabulator(T.el, {
    data: rows,
    index: "_rid",
    nestedFieldSeparator: false, // BC headers contain "." (Item No., Entry No.)
    headerSortClickElement: "icon",
    columns: buildColumns(name, payload.columns),
    layout: "fitData",
    height: "100%",
    reactiveData: false,
    history: true,
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
  T.grid.on("dataChanged", () => { if (!T.suppress) scheduleSave(name); });
  T.grid.on("rowDeleted", () => { if (!T.suppress) scheduleSave(name); });
  T.grid.on("rowAdded", () => { if (!T.suppress) scheduleSave(name); });
  if (name === "bc") T.grid.on("cellClick", (e, cell) => showDetail(cell.getRow()));
  T.grid.on("tableBuilt", () => applyFilters(name));
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
  });
  setVersion(name, payload);
  if (payload.learned && payload.learned.length) {
    toast("🧠 " + payload.learned.join("\n"));
    loadRules();
  }
}

function currentRows(name) {
  return TABLES[name].grid.getData().map((r) => {
    const o = {};
    for (const [k, v] of Object.entries(r)) if (!k.startsWith("_") || k === "_rid") o[k] = v ?? "";
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
    const p = await postJSON(`/api/table/${name}/save`, { columns: T.columns, rows: currentRows(name), note });
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

// ---------------------------------------------------------------- toolbar actions
async function uploadFile(name, file) {
  const fd = new FormData();
  fd.append("file", file);
  toast(`Uploading ${file.name}…`);
  try {
    const p = await api(`/api/table/${name}/upload`, { method: "POST", body: fd });
    makeGrid(name, p);
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

function deleteSelected(name) {
  const T = TABLES[name];
  const rows = new Set();
  (T.grid.getRanges ? T.grid.getRanges() : []).forEach((r) => r.getRows().forEach((row) => rows.add(row)));
  if (!rows.size) { toast("Select cells in the rows to delete first (click / drag on the row numbers)."); return; }
  if (!confirm(`Delete ${rows.size} row(s)? (A new version is saved; you can restore from History.)`)) return;
  T.suppress++;
  Promise.all([...rows].map((r) => r.delete())).finally(() => { T.suppress--; scheduleSave(name); });
}

async function generate() {
  const mode = $("#genMode").value;
  if (mode === "replace" && TABLES.bc.grid.getDataCount() && !confirm("Regenerate the whole Imperial label table from the QuickBooks source? (The current table stays in History.)")) return;
  $("#btnGenerate").disabled = true;
  toast("Matching…");
  try {
    const p = await postJSON("/api/generate", { mode });
    makeGrid("bc", p);
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
        <a class="btn" href="/api/table/${name}/download?fmt=xlsx&version=${v.version_no}">Download</a>
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
  if (!confirm(`Restore v${v}? This saves it as a new version, so it can be undone.`)) return;
  const p = await postJSON(`/api/table/${histName}/restore`, { version: v });
  makeGrid(histName, p);
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
          cellClick: async (e, c) => { if (confirm("Delete this rule?")) { await api(`/api/rules/${c.getRow().getData().id}`, { method: "DELETE" }); loadRules(); } } },
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
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
    const t = b.dataset.tab;
    if (TABLES[t]?.grid) TABLES[t].grid.redraw(true);
    if (t === "rules") loadRules();
  }));
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
})();
