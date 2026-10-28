// Catalog as a spreadsheet: the Listed Sheet's columns, select / type / copy / paste / fill, filter views that
// don't drop rows while you work, undo, and one-click bulk actions.
import { $, STATUS, ago, api, confirmBox, debounce, dialog, esc, fmt, money, priceFor, qs, store, toast } from "./util.js";
import { openDrawer } from "./drawer.js";
import { amazonSized, openCarousel } from "./carousel.js";
import { nextSeries } from "./fill.js";

const RH = 30, HEAD_H = 36, FILT_H = 32, NUM_W = 56;
const HEADERS_H = HEAD_H + FILT_H;
const PAGE_ROWS = 400;

// ---- new rows: blank rows at the bottom of the grid. They live in the browser until "Save new rows" creates the products.
const DRAFT = "__new:";
const isDraft = (id) => typeof id === "string" && id.startsWith(DRAFT);
const DRAFT_TEXT = new Set(["sku", "asin", "link", "name", "category", "variation", "upc", "source", "listing_status", "manual_status", "remarks"]);
const DRAFT_NUM = new Set(["source_price", "weight"]);
// Column protection (an admin sets it from the column header menu): who may edit each column.
const lockOf = (k) => ctx.meta?.locks?.[k];
function blockedMsg(k) {                                      // "" when I may edit the column, otherwise why not
  const l = lockOf(k);
  if (!l) return "";
  const me = ctx.meta.me || {}, admin = me.role === "admin";
  if (l.mode === "admins") return admin ? "" : "🔒 This column is protected: only admins can edit it.";
  if (l.mode === "people") return admin || l.users.includes(me.username) ? "" : "🔒 This column is protected: only admins and the people an admin chose can edit it.";
  return "🔒 This column is protected: nobody can type in it." + (l.note ? " " + l.note : "");
}
const lockTip = (k) => {
  const l = lockOf(k);
  const who = l.mode === "admins" ? "only admins" : l.mode === "people" ? `admins and ${l.users.length ? l.users.join(", ") : "no one else"}` : "nobody (imports only)";
  return `🔒 Protected: ${who} can edit this column.${l.note ? " " + l.note : ""}`;
};
const DRAFT_BOOL = new Set(["listed", "inventory"]);
const MAX_NEW_ROWS = 1000;

// Same columns and order as the Listed Sheet (SKU is pinned at the left so it stays visible while scrolling).
// t: text | num | money | pct | bool | status.  ro: calculated or filled by the scraper, so not editable here.
const COLS = [
  { k: "img", l: "🖼 M", w: 62, t: "img", pin: 1, tip: "Michaels product images (from the Michaels download). Click to view and compare." },
  { k: "amz_img", l: "🖼 A", w: 68, t: "amzimg", pin: 1, tip: "Main image on Amazon (read by the scraper). Click to compare it with the Michaels images." },
  { k: "sku", l: "SKU", w: 150, t: "text", pin: 1 },
  { k: "asin", l: "ASIN", w: 110, t: "text" },
  { k: "price", l: "Price", w: 80, t: "text", r: 1 },
  { k: "availability", l: "Availability", w: 170, t: "text" },
  { k: "delivery", l: "Estimated Delivery", w: 220, t: "text" },
  { k: "listed", l: "Listed", w: 72, t: "bool" },
  { k: "inventory", l: "Inventory", w: 84, t: "bool" },
  { k: "category", l: "CATEGORY", w: 190, t: "text", list: "categories" },
  { k: "name", l: "PRODUCT NAME", w: 340, t: "text" },
  { k: "link", l: "SOURCE LINK", w: 220, t: "text" },
  { k: "source", l: "SOURCE", w: 90, t: "text" },
  { k: "listing_status", l: "LISTING STATUS", w: 120, t: "text", list: "listing_statuses" },
  { k: "weight", l: "WEIGHT", w: 80, t: "num" },
  { k: "source_price", l: "SOURCE PRICE", w: 110, t: "money" },
  { k: "fee", l: "3PL FEE", w: 84, t: "money", calc: 1 },
  { k: "cost", l: "SOURCE PRICE + 3PL FEE", w: 150, t: "money", calc: 1 },
  { k: "selling", l: "SELLING PRICE", w: 110, t: "money", calc: 1 },
  { k: "markup_pct", l: "MARKUP %", w: 88, t: "pct", calc: 1 },
  { k: "auto_price", l: "AUTOMATION FEEDER PRICE", w: 170, t: "money", calc: 1 },
  { k: "variation", l: "VARIATION", w: 170, t: "text" },
  { k: "amazon_variation", l: "AMAZON VARIATION", w: 220, t: "text", tip: "The variation Amazon showed as selected at the last check (read by the scraper). Compare it with VARIATION." },
  { k: "upc", l: "UPC", w: 130, t: "text" },
  { k: "status", l: "SOURCE STATUS", w: 130, t: "status", calc: 1 },
  { k: "run_at", l: "Last Optimization Run", w: 160, t: "text" },
  { k: "checked_by", l: "SCRAPED BY (PC)", w: 160, t: "text", tip: "The scraper PC that ran the last Amazon check" },
  { k: "manual_status", l: "STATUS COUNTER", w: 140, t: "text", list: "manual_statuses" },
  { k: "remarks", l: "OPTIMIZER REMARKS", w: 180, t: "text" },
  { k: "michaels_sku_number", l: "SKU NUMBER", w: 190, t: "text" },
  { k: "michaels_price", l: "EXPORTED MARKETPLACE PRICE", w: 190, t: "money" },
  { k: "michaels_qty", l: "EXPORTED MARKETPLACE INVENTORY", w: 220, t: "num" },
  { k: "sold_total", l: "TOTAL PRODUCT SOLD", w: 150, t: "num", calc: 1 },
  { k: "sold_2025", l: "PRODUCT SOLD 2025", w: 140, t: "num" },
  { k: "sold_2026", l: "PRODUCT SOLD 2026", w: 140, t: "num" },
  { k: "title", l: "AMAZON TITLE", w: 340, t: "text", tip: "The product title Amazon shows now (from the last check)" },
  { k: "title_check", l: "TITLE CHECK", w: 340, t: "text", calc: 1, tip: "Does the Amazon title still describe the catalog item? Type OK to accept it." },
];
const SORTABLE = new Set(["sku", "asin", "name", "status", "source_price", "fee", "cost", "selling", "auto_price", "michaels_price",
  "michaels_qty", "michaels_sku_number", "weight", "sold_total", "sold_2025", "sold_2026", "run_at", "category", "listing_status",
  "manual_status", "listed", "inventory", "remarks", "variation", "upc", "source", "title", "title_check", "checked_by", "amazon_variation"]);
const NO_FILTER = new Set(["title_check", "markup_pct", "sold_2025", "sold_2026"]);
const NO_DROPDOWN = new Set(["sku", "name", "link", "title", "title_check"]);       // identity and read-only text columns can't be dropdowns
const PRESETS = {
  "All columns": null,
  "Daily work": ["img", "amz_img", "sku", "price", "availability", "delivery", "listed", "inventory", "category", "name", "variation", "amazon_variation", "listing_status", "weight", "source_price", "auto_price", "status", "run_at", "checked_by", "manual_status", "remarks", "michaels_sku_number", "michaels_price", "michaels_qty"],
  "Variation check": ["img", "amz_img", "sku", "name", "variation", "amazon_variation", "status", "run_at", "checked_by", "listing_status"],
  "Pricing": ["img", "amz_img", "sku", "name", "weight", "source_price", "fee", "cost", "selling", "markup_pct", "auto_price", "michaels_price"],
  "Michaels": ["img", "amz_img", "sku", "name", "listed", "inventory", "auto_price", "michaels_sku_number", "michaels_price", "michaels_qty", "sold_total"],
};

let S, ctx, root, el = {}, wired = false;

// ------------------------------------------------------------------ helpers
const visCols = () => S.cols;

// The sheet's built-in columns plus any custom columns an admin added (each placed after its anchor column).
function rebuildCols() {
  const custom = (ctx.meta.custom_columns || []).map((c) => ({
    k: c.key, l: c.label, w: 170, custom: 1, ctype: c.type, after: c.after,
    t: c.type === "number" ? "num" : c.type === "checkbox" ? "bool" : "text",
  }));
  const all = COLS.slice();
  let pending = custom.slice(), progress = true;
  while (pending.length && progress) {
    progress = false;
    pending = pending.filter((c) => {
      const i = all.findIndex((x) => x.k === c.after);
      if (i < 0) return true;
      let at = i + 1;
      while (all[at]?.custom && all[at].after === c.after) at++;      // keep several columns added after the same one in order
      all.splice(at, 0, c);
      progress = true;
      return false;
    });
  }
  all.push(...pending);                                              // anchor missing: end of the sheet
  S.all = all;
  S.cols = all.filter((c) => !S.hidden.has(c.k));
}
const colW = (c) => S.widths[c.k] || c.w;
const colX = () => { let x = NUM_W; return visCols().map((c) => { const v = x; x += colW(c); return v; }); };
const totalW = () => NUM_W + visCols().reduce((a, c) => a + colW(c), 0);
const rowAt = (i) => S.cache.get(S.ids[i]) ?? S.draftMap?.get(S.ids[i]);

// left offset of a pinned column: after the row number and any pinned columns before it
const pinLeft = (i) => visCols().slice(0, i).reduce((x, c) => x + (c.pin ? colW(c) : 0), NUM_W);
const pinStyle = (i) => (visCols()[i]?.pin ? `left:${pinLeft(i)}px` : "");
const pinnedWidth = () => visCols().reduce((x, c) => x + (c.pin ? colW(c) : 0), NUM_W);

// The arrow in the Price column compares the Amazon price with the source price itself, so it shows in every queue
// (a row with inventory off is "Check Inventory", but if Amazon is above its source price the ▲ is still there).
function priceArrow(row) {
  if (row._draft) return "";
  const amazon = Number(row.price_num), source = Number(row.source_price);
  if (!(amazon > 0) || row.source_price == null || row.source_price === "" || !Number.isFinite(source)) return "";
  const a = Math.round(amazon * 100), s = Math.round(source * 100);
  if (a > s) return `<span class="delta-up" title="High Price: Amazon ($${amazon.toFixed(2)}) is above your source price ($${source.toFixed(2)})">▲</span>`;
  if (a < s) return `<span class="delta-down" title="Low Price: Amazon ($${amazon.toFixed(2)}) is below your source price ($${source.toFixed(2)})">▼</span>`;
  return "";
}

function display(row, c) {
  if (c.t === "img") return (row.images || []).join(" ");
  if (c.t === "amzimg") return row.amazon_image || "";
  if (row._draft && c.k === "sku") return row._sku || "";
  if (row._draft && c.t === "status") return "";
  const v = row[c.k];
  switch (c.t) {
    case "bool": return row._draft && v == null ? "" : v ? "TRUE" : "FALSE";
    case "money": return v == null || v === "" ? "" : money(v);
    case "pct": return v == null ? "" : (v * 100).toFixed(0) + "%";
    default: return v == null ? "" : String(v);
  }
}
const rawValue = (row, c) => (row._draft && c.k === "sku" ? row._sku || "" : row._draft && c.t === "bool" && row[c.k] == null ? "" : c.t === "bool" ? (row[c.k] ? "TRUE" : "FALSE") : row[c.k] == null ? "" : String(row[c.k]));

// ---- value picker (the checklist under a column's filter button)
const PICK_KEYS = new Set(["sku", "asin", "name", "category", "link", "source", "listing_status", "variation", "upc", "manual_status",
  "remarks", "michaels_sku_number", "price", "availability", "delivery", "title", "checked_by", "amazon_variation"]);
const pickable = (c) => PICK_KEYS.has(c.k) || (c.custom && c.ctype !== "number");
// A picker filter is stored as `in:[ticked values]` or, when most values stay ticked, as the shorter `notin:[unticked values]`.
const pickedOf = (v) => {
  const m = /^(notin|in):/.exec(String(v));
  if (!m) return null;
  try { const a = JSON.parse(String(v).slice(m[0].length)); return { mode: m[1], vals: Array.isArray(a) ? a : [] }; } catch { return { mode: m[1], vals: [] }; }
};
const pickLabel = (p) => (p.mode === "notin" ? `all except ${p.vals.length}` : p.vals.length === 1 ? p.vals[0] : `${p.vals.length} selected`);

async function openPicker(c, anchor) {
  document.querySelector(".pickmenu")?.remove();
  const rect = anchor.getBoundingClientRect();
  const m = document.createElement("div");
  m.className = "menu hmenu pickmenu";
  m.style.cssText = `position:fixed;left:${Math.max(8, Math.min(rect.left, innerWidth - 320))}px;top:${Math.min(rect.bottom + 4, innerHeight - 420)}px;width:300px`;
  m.innerHTML = `<div class="mhead">${esc(c.l)}: loading…</div>`;
  document.body.append(m);
  const close = (ev) => { if (!m.contains(ev.target) || ev.type === "keydown" && ev.key === "Escape") { m.remove(); document.removeEventListener("mousedown", close, true); document.removeEventListener("keydown", close, true); } };
  setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", close, true); }, 0);
  let res;
  try {
    const p = filterParams();
    delete p["cf_" + c.k];
    res = await api("/api/sheet/values", { ...p, col: c.k });          // sent in the body: the filters can be long
  } catch (e) { m.remove(); return toast(e.message, "bad"); }
  const values = res.values, cur = S.F.cf[c.k] || "";
  const pk = pickedOf(cur), same = (list, x) => list.some((p) => String(p).toLowerCase() === x.v.toLowerCase());
  const on = new Set((pk ? values.filter((x) => (pk.mode === "notin" ? !same(pk.vals, x) : same(pk.vals, x)))
    : cur && (c.t === "status" || c.t === "bool") ? values.filter((x) => same([cur], x)) : values).map((x) => x.v));
  let term = "";
  const shown = () => values.filter((x) => x.v.toLowerCase().includes(term));
  const draw = () => {
    const list = shown();
    m.querySelector(".plist").innerHTML = list.length
      ? list.map((x) => `<label class="check prow"><input type="checkbox" data-v="${esc(x.v)}" ${on.has(x.v) ? "checked" : ""}> <span class="pv">${esc(x.v)}</span><span class="muted pn">${fmt(x.n)}</span></label>`).join("")
      : `<div class="mhead">Nothing matches.</div>`;
    m.querySelector("[data-pa='ok']").disabled = on.size === 0;
    m.querySelector(".pcount").textContent = `${on.size} of ${values.length} ticked`;
  };
  m.innerHTML = `<div class="mhead">${esc(c.l)}${res.truncated ? " (first 500 values)" : ""}</div>
    <input class="psearch" type="search" placeholder="Search values" aria-label="Search values">
    <div class="prow2"><button type="button" data-pa="all">Select all</button><button type="button" data-pa="none">Clear</button><span class="muted pcount"></span></div>
    <div class="plist"></div>
    <div class="prow2"><button type="button" class="btn small primary" data-pa="ok">Apply</button><button type="button" class="btn small" data-pa="cancel">Cancel</button>
      ${cur ? `<button type="button" data-pa="off">Remove filter</button>` : ""}</div>`;
  draw();
  const search = m.querySelector(".psearch");
  search.focus();
  search.oninput = () => { term = search.value.trim().toLowerCase(); draw(); };
  m.addEventListener("change", (e) => { const v = e.target.dataset.v; if (v !== undefined) { e.target.checked ? on.add(v) : on.delete(v); draw(); } });
  m.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pa]");
    if (!b || b.disabled) return;
    const a = b.dataset.pa;
    if (a === "all") { shown().forEach((x) => on.add(x.v)); return draw(); }
    if (a === "none") { on.clear(); return draw(); }                    // Clear unticks every value, even ones hidden by the search
    m.remove();
    document.removeEventListener("mousedown", close, true);
    document.removeEventListener("keydown", close, true);
    if (a === "cancel") return;
    const ticked = values.filter((x) => on.has(x.v)).map((x) => x.v), unticked = values.filter((x) => !on.has(x.v)).map((x) => x.v);
    S.F.cf[c.k] = a === "off" || on.size === values.length ? "" : on.size * 2 > values.length ? "notin:" + JSON.stringify(unticked) : "in:" + JSON.stringify(ticked);
    drawHead();
    loadView(true);
  });
}

function filterParams() {
  const p = { q: S.F.q, queue: S.F.queue, sort: S.F.sort };
  for (const [k, v] of Object.entries(S.F.cf)) if (v) p["cf_" + k] = v;
  return p;
}
// Ctrl+click / Ctrl+drag keep earlier blocks in S.multi; S.sel is always the block being worked on (the active cell is in it).
const rects = () => (S.sel ? [...S.multi, normSel()] : []);
const inRects = (rs, r, c) => rs.some((x) => r >= x.r1 && r <= x.r2 && c >= x.c1 && c <= x.c2);
function* pickedCells() {                                        // every highlighted cell once, even where blocks overlap
  const seen = new Set();
  for (const x of rects()) for (let r = x.r1; r <= x.r2; r++) for (let c = x.c1; c <= x.c2; c++) { const k = r * 4096 + c; if (!seen.has(k)) { seen.add(k); yield { r, c }; } }
}
// The dashed border Google Sheets draws round what you just copied: each cell on the outside edge of a copied block gets a side class.
function copyEdges(rs, r, c) {
  let s = "";
  for (const x of rs) {
    if (r < x.r1 || r > x.r2 || c < x.c1 || c > x.c2) continue;
    s += ` cp${r === x.r1 ? " cp-t" : ""}${r === x.r2 ? " cp-b" : ""}${c === x.c1 ? " cp-l" : ""}${c === x.c2 ? " cp-r" : ""}`;
  }
  return s;
}
const normSel = () => {
  if (!S.sel) return null;
  const { r1, c1, r2, c2 } = S.sel;
  return { r1: Math.min(r1, r2), r2: Math.max(r1, r2), c1: Math.min(c1, c2), c2: Math.max(c1, c2) };
};

// ------------------------------------------------------------------ mount
export async function mount(host, hashParams, c) {
  ctx = c;
  root = host;
  const hidden = (() => { try { return new Set(JSON.parse(store.get("gt_hidden_cols", "[]"))); } catch { return new Set(); } })();
  const widths = (() => { try { return JSON.parse(store.get("gt_col_w", "{}")); } catch { return {}; } })();
  S = { ids: [], cache: new Map(), sel: null, dim: new Set(), undo: [], redo: [], hidden, widths, views: [], taken: "", busy: false,
        F: { q: hashParams.get("q") || "", queue: hashParams.get("queue") || "", sort: hashParams.get("sort") || "", cf: {} },
        edit: null, drag: false, focused: false, fetching: new Set(), drafts: [], draftMap: new Map(), draftSeq: 0, draftAlias: new Map(), work: null, touched: new Set(), saving: false, resave: false, fill: null, multi: [], add: false, copied: null };
  for (const [k, v] of hashParams) if (k.startsWith("cf_")) S.F.cf[k.slice(3)] = v;  S.views = (await api("/api/views")).items;
  await ctx.refreshMeta();
  try { for (const d of JSON.parse(store.get("gt_drafts", "[]"))) newDraft(d); } catch { /* nothing saved */ }       // unsaved new rows survive a reload
  rebuildCols();
  skeleton();
  await loadView(true);
  await offerWork(hashParams.get("queue") || [...hashParams.keys()].some((k) => k.startsWith("cf_")) || hashParams.get("q"));   // resume a saved work list after a reload
  autoSaveDrafts();                                    // new rows left complete before a reload are added now
}

function skeleton() {
  const m = ctx.meta;
  const queueOpts = [`<option value="">All products</option>`,
    `<optgroup label="Amazon status">${Object.keys(STATUS).map((k) => `<option ${S.F.queue === k ? "selected" : ""}>${esc(k)}</option>`).join("")}</optgroup>`,
    ...["Listing check", "Michaels sync", "Data quality"].map((g) => `<optgroup label="${g}">${Object.entries(m.queues).filter(([, v]) => v.group === g).map(([k, v]) => `<option value="${k}" ${S.F.queue === k ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</optgroup>`)].join("");
  root.innerHTML = `<div class="sheetpage">
    <div class="toolbar sheetbar">
      <input class="grow" type="search" id="q" placeholder="Search SKU, ASIN, UPC, name…  ( / )" value="${esc(S.F.q)}" aria-label="Search" style="max-width:340px">
      <select id="fQueue" aria-label="Queue">${queueOpts}</select>
      <span class="sep"></span>
      <button class="btn small" id="clearFilters" title="Turn off every filter at once: the column filters, the search box and the queue" hidden>✕ Clear filters</button>
      <button class="btn small primary" id="startWork" title="Freeze the rows in this view and save them as a work list, so nothing is lost if the page reloads or the power goes out" hidden>📋 Work list</button>
      <button class="btn small" id="undo" title="Undo (Ctrl+Z)">↶ Undo</button><button class="btn small" id="redo" title="Redo (Ctrl+Y)">↷ Redo</button>
      <span class="menuwrap"><button class="btn small" id="actionsBtn">Actions ▾</button><div class="menu hidden" id="actionsMenu"></div></span>
      <button class="btn small" id="colsBtn">Columns</button>
      ${ctx.isAdmin() ? `<button class="btn small" id="addColBtn" title="Add a column of your own">+ Column</button>` : ""}
      <button class="btn small" id="exportBtn">Export view</button>
      <span class="sep"></span>
      <button class="btn small primary" id="refresh" title="Re-apply the filters and sort">⟳ Refresh view</button>
      <button class="btn small primary" id="newRows" title="Jump to the blank rows at the bottom of the sheet and type a new product there">+ New rows</button>
      <a class="btn small" href="#add">Add products page</a>
    </div>
    <div class="toolbar" id="viewsBar" style="margin-bottom:6px"></div>
    <div class="toolbar draftbar hidden" id="draftbar"></div>
    <div class="toolbar draftbar workbar hidden" id="workbar"></div>
    <div class="fbar mono" id="fbar">&nbsp;</div>
    <div class="scroller" id="scroller" tabindex="0" aria-label="Product sheet">
      <div class="inner" id="inner"><div class="ghead" id="ghead"></div><div class="gbody" id="gbody"></div></div>
    </div>
    <div class="statusbar"><span id="stLeft"></span><span id="stRight"></span></div>
    ${["categories", "listing_statuses", "manual_statuses"].map((n) => `<datalist id="dl_${n}">${(m[n] || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist>`).join("")}
    <datalist id="dl_source"><option value="Amazon"></datalist>
    <datalist id="dl_statuses">${Object.keys(STATUS).map((x) => `<option value="${esc(x)}">`).join("")}</datalist></div>`;
  el = { scroller: $("#scroller"), inner: $("#inner"), head: $("#ghead"), body: $("#gbody"), fbar: $("#fbar"), stL: $("#stLeft"), stR: $("#stRight") };
  fitHeight();
  drawViews();
  wire();
  drawDraftBar();
  drawWorkBar();
  drawClearButton();
}

const fitHeight = () => { const top = el.scroller.getBoundingClientRect().top; el.scroller.style.height = Math.max(320, innerHeight - top - 46) + "px"; };

function drawHead() {
  // The header is rebuilt after every filter change; keep the cursor in the filter box being typed in.
  const ae = document.activeElement;
  const keep = ae && el.head.contains(ae) && ae.dataset?.cf ? { k: ae.dataset.cf, a: ae.selectionStart, b: ae.selectionEnd } : null;
  drawHeadHtml();
  if (keep) {
    const inp = el.head.querySelector(`input[data-cf="${keep.k}"]`);
    if (inp) { inp.focus(); try { inp.setSelectionRange(keep.a, keep.b); } catch { /* not a text box */ } }
  }
}

function drawHeadHtml() {
  const cols = visCols(), xs = colX();
  const sortKey = S.F.sort.replace("-", ""), desc = S.F.sort.startsWith("-");
  el.head.style.width = totalW() + "px";
  const filterCell = (c) => {
    if (NO_FILTER.has(c.k)) return "";
    const v = S.F.cf[c.k] || "";
    const picked = pickedOf(v);
    if (c.t === "status" || c.t === "bool") {      // tick the values to show, like a Google Sheets filter view
      const label = picked ? pickLabel(picked) : v || "all";
      return `<button type="button" class="fpick wide ${v ? "on" : ""}" data-pick="${c.k}" title="Choose which values to show">${esc(label)} ▾</button>`;
    }
    if (pickable(c) && c.t === "text") {
      const ph = picked ? pickLabel(picked) : "contains…";
      return `<div class="fwrap"><input data-cf="${c.k}" value="${picked ? "" : esc(v)}" placeholder="${ph}" class="${picked ? "on" : ""}" aria-label="Filter ${esc(c.l)}">
        <button type="button" class="fpick ${picked ? "on" : ""}" data-pick="${c.k}" title="Choose which values to show" aria-label="Pick values for ${esc(c.l)}">▾</button></div>`;
    }
    if (c.t === "amzimg") return `<select data-cf="${c.k}"><option value="">all</option><option value="TRUE" ${v === "TRUE" ? "selected" : ""}>has image</option><option value="FALSE" ${v === "FALSE" ? "selected" : ""}>no image</option></select>`;
    if (c.t === "img") return `<select data-cf="${c.k}"><option value="">all</option><option value="TRUE" ${v === "TRUE" ? "selected" : ""}>has images</option><option value="FALSE" ${v === "FALSE" ? "selected" : ""}>no images</option></select>`;
    if (c.t === "bool") return `<select data-cf="${c.k}"><option value="">all</option><option ${v === "TRUE" ? "selected" : ""}>TRUE</option><option ${v === "FALSE" ? "selected" : ""}>FALSE</option></select>`;
    if (c.t === "status") return `<select data-cf="${c.k}"><option value="">all</option>${Object.keys(STATUS).map((s) => `<option ${v === s ? "selected" : ""}>${s}</option>`).join("")}</select>`;
    const ph = c.t === "money" || c.t === "num" ? ">10 or 5-9" : "contains…";
    return `<input data-cf="${c.k}" value="${esc(v)}" placeholder="${ph}" aria-label="Filter ${esc(c.l)}">`;
  };
  el.head.innerHTML = `<div class="hrow" style="height:${HEAD_H}px"><div class="hc rn" data-corner="1" style="width:${NUM_W}px" title="Select everything">◢</div>
    ${cols.map((c, i) => `<div class="hc ${c.pin ? "pin" : ""}" data-hc="${i}" style="width:${colW(c)}px;${pinStyle(i)}"><span class="hl" title="${esc(lockOf(c.k) ? lockTip(c.k) : c.tip || c.l)}">${lockOf(c.k) ? "🔒 " : ""}${esc(c.l)}</span>
      ${SORTABLE.has(c.k) || c.custom ? `<button class="sb ${sortKey === c.k ? "on" : ""}" data-sort="${c.k}" title="Sort" aria-label="Sort by ${esc(c.l)}">${sortKey === c.k ? (desc ? "↓" : "↑") : "⇅"}</button>` : ""}
      <button class="sb" data-hm="${i}" title="Column menu (or right-click)" aria-label="Menu for ${esc(c.l)}">▾</button>
      <i class="rz" data-rz="${i}"></i></div>`).join("")}</div>
    <div class="hrow frow" style="height:${FILT_H}px"><div class="hc rn" style="width:${NUM_W}px"></div>
    ${cols.map((c, i) => `<div class="hc fc ${c.pin ? "pin" : ""}" style="width:${colW(c)}px;${pinStyle(i)}">${filterCell(c)}</div>`).join("")}</div>`;
}

// ------------------------------------------------------------------ data
async function loadView(resetScroll) {
  if (S.work) {                                          // working through a saved list: a refresh keeps its rows, a new filter leaves it (it stays saved)
    if (!resetScroll) return refreshWork();
    leaveWork(true);
  }
  S.busy = true;
  statusLine("Loading…");
  try {
    const r = await api("/api/sheet/skus", filterParams());              // in the body: a long list of ticked values doesn't fit in a web address
    S.ids = r.skus;
    ensureBlankDraft();
    S.ids = S.ids.concat(S.drafts.map((d) => d.sku));      // the new rows always sit at the very bottom
    S.taken = r.taken_at;
    S.cache.clear();
    S.dim.clear();
    if (resetScroll) { el.scroller.scrollTop = 0; S.sel = null; } else if (S.sel) {
      const n = normSel(); if (n.r1 >= S.ids.length) S.sel = null;
    }
  } catch (e) { toast(e.message, "bad"); }
  S.busy = false;
  el.body.style.height = S.ids.length * RH + "px";
  el.inner.style.width = totalW() + "px";
  drawHead();
  render();
  statusLine();
  drawDraftBar();
  drawClearButton();
}

async function exportView() {                           // POST, so a long filter can't break the request
  statusLine("Preparing the export…");
  try {
    const r = await fetch("/api/products/export.csv", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(filterParams()) });
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) throw new Error(`Export failed (${r.status}).`);
    const blob = await r.blob();
    const name = /filename="?([^";]+)/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "catalog-view.csv";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    statusLine();
  } catch (e) { statusLine(); toast(e.message, "bad"); }
}

// "✕ Clear filters" shows only while something is filtering, with how many filters are on.
function drawClearButton() {
  const b = $("#clearFilters");
  if (!b) return;
  const n = Object.values(S.F.cf).filter((v) => String(v || "").trim()).length + (S.F.q.trim() ? 1 : 0) + (S.F.queue ? 1 : 0);
  b.hidden = !n;
  b.textContent = `✕ Clear filters (${n})`;
  const w = $("#startWork");                             // a work list starts from a filtered view (an issue queue, a column filter …)
  if (w) w.hidden = !n || !!S.work;
}
function clearAllFilters() {
  S.F.cf = {};
  S.F.q = "";
  S.F.queue = "";
  if ($("#q")) $("#q").value = "";
  if ($("#fQueue")) $("#fQueue").value = "";
  if (location.hash.includes("?")) history.replaceState(null, "", "#catalog");      // a reload must not bring the filters from the link back
  loadView(true);
}

async function ensureRows(a, b) {
  const need = [];
  for (let i = Math.max(0, a); i <= Math.min(S.ids.length - 1, b); i++) {
    const sku = S.ids[i];
    if (!isDraft(sku) && !S.cache.has(sku) && !S.fetching.has(sku)) need.push(sku);
  }
  for (let i = 0; i < need.length; i += PAGE_ROWS) {
    const chunk = need.slice(i, i + PAGE_ROWS);
    chunk.forEach((s) => S.fetching.add(s));
    try {
      const r = await api("/api/sheet/rows", { skus: chunk });
      r.rows.forEach((row) => S.cache.set(row.sku, row));
    } finally { chunk.forEach((s) => S.fetching.delete(s)); }
  }
  return need.length;
}

// ------------------------------------------------------------------ render
function visibleRange() {
  const st = el.scroller.scrollTop, vh = el.scroller.clientHeight;
  return [Math.max(0, Math.floor(st / RH) - 4), Math.min(S.ids.length - 1, Math.ceil((st + vh) / RH) + 4)];
}

function render() {
  const [a, b] = visibleRange();
  const cols = visCols(), xs = colX(), sel = normSel(), rs = rects();
  if (S.copied && S.copied.n !== S.ids.length) S.copied = null;          // the rows moved: the dashed border no longer points at the right cells
  const cpy = S.copied?.rects;
  const act = S.sel ? { r: S.sel.r2, c: S.sel.c2 } : null;
  let html = "";
  for (let i = a; i <= b; i++) {
    const row = rowAt(i);
    const rowSel = rs.some((x) => i >= x.r1 && i <= x.r2 && x.c1 === 0 && x.c2 === cols.length - 1);
    const draft = !!row?._draft;
    html += `<div class="gr ${S.dim.has(S.ids[i]) ? "gone" : ""} ${S.touched.has(S.ids[i]) ? "touched" : ""} ${row && !draft && !row.inventory && (S.dim.has(S.ids[i]) || S.touched.has(S.ids[i])) ? "invoff" : ""} ${draft ? "draft" : ""} ${row?._err ? "bad" : ""}" style="top:${i * RH}px;width:${totalW()}px">
      <div class="gc rn ${rowSel ? "s" : ""}" data-r="${i}" data-c="-1" style="width:${NUM_W}px" title="${draft ? esc(row._err || "A new row: fill it in, then press Save new rows") : "Click to select row · double-click for details"}">${draft ? (draftBlank(row) ? "＋" : "new") : i + 1}</div>`;
    cols.forEach((c, ci) => {
      const inSel = inRects(rs, i, ci);
      const isAct = act && act.r === i && act.c === ci;
      const cls = `gc ${c.r || c.t === "num" || c.t === "money" || c.t === "pct" ? "r" : ""} ${c.t === "bool" ? "ctr" : ""} ${c.pin ? "pin" : ""} ${c.calc ? "calc" : ""} ${blockedMsg(c.k) ? "ro locked" : ""} ${row?._ovr?.includes(c.k) ? "ov" : ""} ${inSel ? "s" : ""} ${isAct ? "a" : ""} ${cpy ? copyEdges(cpy, i, ci) : ""}`;
      let inner = "";
      if (row) {
        if (c.t === "amzimg") inner = row.amazon_image
          ? `<button class="imgbtn thumbbtn" data-amz="${i}" title="Amazon main image: click to compare with Michaels"><img src="${esc(amazonSized(row.amazon_image, 160))}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>`
          : `<span class="muted" title="No Amazon image yet. The scraper reads it on its next check of this product.">–</span>`;
        else if (c.t === "img") inner = row.images?.length
          ? `<button class="imgbtn" data-img="${i}" title="View ${row.images.length} image${row.images.length === 1 ? "" : "s"}">🖼 ${row.images.length}</button>`
          : `<span class="muted" title="No images yet. They come with the Michaels Download Listing import.">–</span>`;
        else if (c.t === "bool") inner = `<input type="checkbox" class="cbx" tabindex="-1" ${row[c.k] ? "checked" : ""} aria-label="${esc(c.l)}" title="${esc(c.l)}: click to switch, or select and press Space">`;
        else if (c.t === "status") inner = draft ? "" : `<span class="pill s-${(STATUS[row.status] || STATUS["Not checked"]).cls}">${esc(row.status || "Not checked")}</span>`;
        else if (c.k === "price" && priceArrow(row))        // ▲ Amazon above the source price (High Price), ▼ below it (Low Price), whatever queue is open
          inner = `${esc(display(row, c))} ${priceArrow(row)}`;
        else inner = esc(display(row, c));
      } else inner = ci === 0 ? '<span class="muted">…</span>' : "";
      html += `<div class="${cls}" data-r="${i}" data-c="${ci}" style="width:${colW(c)}px;${pinStyle(ci)}">${inner}</div>`;
    });
    html += "</div>";
  }
  el.body.innerHTML = html;
  const missing = [];
  for (let i = a; i <= b; i++) if (!S.cache.has(S.ids[i]) && !isDraft(S.ids[i])) { missing.push(i); }
  if (missing.length) ensureRows(missing[0], missing[missing.length - 1]).then((n) => n && render());
  drawFillHandle();
  drawFbar();
  statusLine();
}

function drawFbar() {
  const sel = normSel();
  if (!sel) return (el.fbar.innerHTML = "&nbsp;");
  const c = visCols()[S.sel.c2], row = rowAt(S.sel.r2);
  const note = !c.calc ? "" : row?._ovr?.includes(c.k) ? "  (typed over the formula · Delete reverts it)" : "  (calculated · type to override)";
  el.fbar.textContent = row ? `${row._draft ? "(new row)" : row.sku}  ·  ${c.l}${note}:  ${display(row, c)}` : "";
}

function statusLine(msg) {
  const sel = normSel();
  const left = msg || `${fmt(S.ids.length - S.drafts.length)} rows${S.dim.size ? (S.work ? ` · ${fmt(S.dim.size)} resolved (they stay in your work list)` : ` · ${fmt(S.dim.size)} no longer match the filter (click Refresh view to drop them)`) : ""}`;
  el.stL.textContent = left;
  let right = "";
  if (sel) {
    const cells = (sel.r2 - sel.r1 + 1) * (sel.c2 - sel.c1 + 1);
    right = `${fmt(cells)} cell${cells === 1 ? "" : "s"} selected`;
    if (cells > 1 && cells <= 20000) {
      let sum = 0, n = 0, ok = true;
      const cols = visCols();
      for (let r = sel.r1; r <= sel.r2 && ok; r++) {
        const row = rowAt(r);
        if (!row) { ok = false; break; }
        for (let c = sel.c1; c <= sel.c2; c++) {
          const col = cols[c];
          if (["money", "num"].includes(col.t) && typeof row[col.k] === "number") { sum += row[col.k]; n++; }
        }
      }
      if (ok && n) right += ` · Sum ${sum.toLocaleString(undefined, { maximumFractionDigits: 2 })} · Avg ${(sum / n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    }
  }
  el.stR.textContent = right;
  $("#undo").disabled = !S.undo.length;
  $("#redo").disabled = !S.redo.length;
}

function drawViews() {
  const bar = $("#viewsBar");
  bar.innerHTML = `<span class="muted">Saved views:</span>
    ${S.views.map((v) => `<span class="btnrow" style="gap:0"><button class="btn small" data-view="${v.id}" title="${v.shared ? "Shared by " + esc(v.username) : "Only you"}">${v.shared ? "👥 " : ""}${esc(v.name)}</button>${v.mine || ctx.isAdmin() ? `<button class="btn small danger" data-delview="${v.id}" title="Delete view" aria-label="Delete view ${esc(v.name)}">×</button>` : ""}</span>`).join("") || `<span class="muted">none yet</span>`}
    <button class="btn small" id="saveView">+ Save current filters</button>`;
}

// ------------------------------------------------------------------ selection & movement
function setSel(r1, c1, r2 = r1, c2 = c1, scroll = true) {
  const nr = S.ids.length - 1, nc = visCols().length - 1;
  if (!S.add) S.multi = [];                                   // a plain click or arrow key drops the extra (Ctrl-picked) blocks
  S.sel = { r1: Math.max(0, Math.min(nr, r1)), c1: Math.max(0, Math.min(nc, c1)), r2: Math.max(0, Math.min(nr, r2)), c2: Math.max(0, Math.min(nc, c2)) };
  if (scroll) reveal(S.sel.r2, S.sel.c2);
  render();
}
function reveal(r, c) {
  const sc = el.scroller, st = sc.scrollTop, vh = sc.clientHeight - HEADERS_H;
  if (r * RH < st) sc.scrollTop = r * RH;
  else if ((r + 1) * RH > st + vh) sc.scrollTop = (r + 1) * RH - vh;
  const x = colX()[c], w = colW(visCols()[c]), sl = sc.scrollLeft, vw = sc.clientWidth;
  const pinned = pinnedWidth();
  if (c > 0 && x - pinned < sl) sc.scrollLeft = x - pinned;
  else if (x + w > sl + vw) sc.scrollLeft = x + w - vw;
}
const move = (dr, dc, extend) => {
  if (!S.sel) return setSel(0, 0);
  const s = S.sel;
  if (extend) setSel(s.r1, s.c1, s.r2 + dr, s.c2 + dc);
  else setSel(s.r2 + dr, s.c2 + dc);
};
async function openLink(r) {
  if (!rowAt(r)) await ensureRows(r, r);
  let url = String(rowAt(r)?.link || "").trim();
  if (url && !/^[a-z][a-z0-9+.-]*:/i.test(url) && /^[\w-]+(\.[\w-]+)+(\/|$)/.test(url)) url = "https://" + url;       // "www.amazon.com/dp/…" without the https://
  if (!/^https?:\/\//i.test(url)) return toast(url ? "That isn't a web address, so it can't be opened. Press Enter to fix it." : "There's no link in this cell yet. Press Enter to type one.", "bad");
  window.open(url, "_blank", "noopener");
}
function cellFromEvent(e) {
  const t = e.target.closest(".gc");
  return t ? { r: +t.dataset.r, c: +t.dataset.c } : null;
}

// ------------------------------------------------------------------ new rows (drafts) and drag-fill
const draftEditable = (c) => (DRAFT_TEXT.has(c.k) || DRAFT_NUM.has(c.k) || DRAFT_BOOL.has(c.k) || !!c.custom) && !blockedMsg(c.k);

function newDraft(fields = {}) {
  const id = DRAFT + ++S.draftSeq;
  const d = { listed: null, inventory: null, listing_status: "", source: "", _sku: "", ...fields, sku: id, _draft: true };      // everything starts blank
  S.drafts.push(d);
  S.draftMap.set(id, d);
  recalcDraft(d);
  return d;
}
function recalcDraft(d) {                                // the price columns, worked out as you type like the Add products page
  const p = priceFor(d.weight, d.source_price, ctx.meta.pricing);
  d.fee = p.fee; d.cost = p.cost; d.selling = p.selling; d.auto_price = p.auto;
}
function draftBlank(d) {
  const filled = (v) => v != null && v !== "" && v !== false;
  return !filled(String(d._sku).trim()) && ![...DRAFT_TEXT].some((k) => k !== "sku" && filled(String(d[k] ?? "").trim()))
    && ![...DRAFT_NUM].some((k) => filled(d[k])) && ![...DRAFT_BOOL].some((k) => d[k] != null) && !(ctx.meta.custom_columns || []).some((c) => filled(d[c.key]));
}
function ensureBlankDraft() {
  if (!S.drafts.length || !draftBlank(S.drafts[S.drafts.length - 1])) newDraft();
}
function growTo(r) {                                     // make sure row r exists, adding blank new rows at the bottom
  while (S.ids.length <= r && S.drafts.length < MAX_NEW_ROWS) S.ids.push(newDraft().sku);
  el.body.style.height = S.ids.length * RH + "px";
}
function persistDrafts() {
  const keep = S.drafts.filter((d) => !draftBlank(d)).map(({ sku, ...rest }) => rest);
  store.set("gt_drafts", keep.length ? JSON.stringify(keep) : "[]");
}

// Put typed / pasted / filled values into new rows. Returns how many cells were taken and how many were refused.
function applyDraftEdits(edits) {
  const custom = new Map((ctx.meta.custom_columns || []).map((c) => [c.key, c]));
  const yes = /^(true|1|yes|y|x|checked|on)$/i, money = (s) => Number(String(s).replace(/[$,\s]/g, ""));
  let changed = 0, rejected = 0, why = "";
  for (const e of edits) {
    const d = S.draftMap.get(e.sku), f = e.field;
    let raw = e.value == null ? "" : String(e.value).trim();
    if (!d) continue;
    const dd = f !== "sku" && ctx.meta.dropdowns?.[f];
    if (dd && raw) {                                          // a dropdown column takes only its listed values
      const m = dd.find((s) => s.toLowerCase() === raw.toLowerCase());
      if (!m) { rejected++; why = `“${raw}” isn't in the dropdown. Choose one of: ${dd.slice(0, 12).join(", ")}${dd.length > 12 ? "…" : ""}.`; continue; }
      raw = m;
    }
    if (f === "sku") d._sku = raw;
    else if (DRAFT_BOOL.has(f)) d[f] = raw === "" ? null : yes.test(raw);
    else if (DRAFT_NUM.has(f)) {
      if (raw !== "" && Number.isNaN(money(raw))) { rejected++; why = "Weight and prices must be numbers."; continue; }
      d[f] = raw === "" ? null : money(raw);
    } else if (f === "listing_status") {
      const m = ctx.meta.listing_statuses.find((s) => s.toLowerCase() === raw.toLowerCase());
      if (raw && !m) { rejected++; why = "Listing status must be " + ctx.meta.listing_statuses.join(", ") + "."; continue; }
      d[f] = m || "";
    } else if (DRAFT_TEXT.has(f)) d[f] = raw;
    else if (custom.has(f)) {
      const t = custom.get(f).type;
      if (t === "number" && raw !== "" && Number.isNaN(money(raw))) { rejected++; why = "That column takes numbers."; continue; }
      d[f] = t === "checkbox" ? yes.test(raw) : t === "number" ? (raw === "" ? null : money(raw)) : raw;
    } else { rejected++; why = "Calculated and scraper columns fill in after the product is saved."; continue; }
    d._err = "";
    d._v = (d._v || 0) + 1;
    changed++;
    if (f === "weight" || f === "source_price") recalcDraft(d);
  }
  ensureBlankDraft();
  if (changed) autoSaveDrafts();
  S.ids = S.ids.filter((id) => !isDraft(id)).concat(S.drafts.map((d) => d.sku));
  el.body.style.height = S.ids.length * RH + "px";
  persistDrafts();
  drawDraftBar();
  return { changed, rejected, why };
}

function drawDraftBar() {
  const bar = $("#draftbar");
  if (!bar) return;
  const used = S.drafts.filter((d) => !draftBlank(d));
  bar.classList.toggle("hidden", !used.length);
  if (used.length) {
    const bad = used.filter((d) => d._err).length, waiting = used.length - bad;
    bar.innerHTML = `<b>${fmt(used.length)} new row${used.length === 1 ? "" : "s"}</b> being added. A row is added by itself as soon as you fill in anything; nothing is required (a row without a SKU gets a temporary one like NEW-000001 that you can rename).
      ${bad ? `<span style="color:var(--crit)">${fmt(bad)} couldn't be added (hover the row's “new” tag to see why).</span>` : waiting ? `` : ""}
      <span class="grow"></span><button class="btn small" data-draft="discard">Discard these rows</button>`;
  }
  fitHeight();
}

// A new row is added by itself as soon as its required cells are filled in (no Save button, no confirmation).
// Rows that are still incomplete wait; a row the server refuses (duplicate SKU, …) turns red until it is edited.
const autoSaveDrafts = debounce(() => saveDrafts(), 600);

async function saveDrafts() {
  if (S.saving) { S.resave = true; return; }
  const rows = S.drafts.filter((d) => !draftBlank(d) && !d._err);          // anything typed is enough: nothing is required
  if (!rows.length) return;
  S.saving = true;
  const payload = (d) => ({ sku: d._sku, asin: d.asin, link: d.link, name: d.name, category: d.category, variation: d.variation, upc: d.upc,
    source: d.source || "", listing_status: d.listing_status || "", listed: !!d.listed, inventory: !!d.inventory, source_price: d.source_price, weight: d.weight });
  const version = new Map(rows.map((d) => [d, d._v || 0]));
  statusLine(`Adding ${fmt(rows.length)} new row${rows.length === 1 ? "" : "s"}…`);
  let done = null, refused = 0;
  try {
    const prev = await api("/api/products/preview-batch", { rows: rows.map(payload), loose: true });
    rows.forEach((d, i) => {
      const x = prev.rows[i];
      d._err = x.ok ? "" : [...Object.values(x.errors), ...x.dupes.map((q) => `${q.field} matches ${q.sku}`)].join(" · ");
    });
    refused = rows.filter((d) => d._err).length;
    const ok = rows.filter((d) => !d._err);
    if (ok.length) {
      done = await api("/api/products/create-batch", { rows: ok.map(payload), loose: true });
      const extras = [], createdRows = [];
      for (const m of done.rows || []) { const d = ok[m.i]; d._sku = m.sku; createdRows.push(d); }      // a row typed without a SKU got a temporary one (NEW-000001…)
      for (const d of createdRows) {
        // fields the create step has no slot for, and anything typed into the row while it was being added
        const more = d._v !== version.get(d) ? ["asin", "variation", "upc", "category", "name", "link", "source_price", "weight"] : [];
        for (const k of ["manual_status", "remarks", ...more]) if (d[k] != null && d[k] !== "") extras.push({ sku: d._sku, field: k, value: d[k] });
        for (const c of ctx.meta.custom_columns || []) if (d[c.key] != null && d[c.key] !== "" && d[c.key] !== false) extras.push({ sku: d._sku, field: c.key, value: d[c.key] === true ? "TRUE" : d[c.key] });
        S.draftAlias.set(d.sku, d._sku);                     // an edit still aimed at the new row now goes to the real product
      }
      if (extras.length) await api("/api/sheet/cells", { edits: extras });
      for (const d of createdRows) { S.drafts.splice(S.drafts.indexOf(d), 1); S.draftMap.delete(d.sku); }
    }
  } catch (e) { S.saving = false; statusLine(); return toast(e.message, "bad"); }
  S.saving = false;
  persistDrafts();
  if (done?.created) {
    await loadView(false);
    ctx.refreshCounts();
    toast(`Added ${fmt(done.created)} product${done.created === 1 ? "" : "s"}.${S.F.q || S.F.queue || Object.values(S.F.cf).some(Boolean) ? " A filter is on, so it may not show in this view." : ""}`);
  } else { drawDraftBar(); render(); statusLine(); }
  if (refused) toast(`${fmt(refused)} new row${refused === 1 ? "" : "s"} couldn't be added. Hover the red “new” tag to see why.`, "bad");
  if (S.resave) { S.resave = false; autoSaveDrafts(); }
}

async function discardDrafts() {
  const n = S.drafts.filter((d) => !draftBlank(d)).length;
  if (!n || !(await confirmBox(`Discard ${fmt(n)} new row${n === 1 ? "" : "s"}?`, "They have not been saved yet, so they will be lost.", "Discard"))) return;
  S.drafts = []; S.draftMap.clear();
  persistDrafts();
  await loadView(false);
}

// ---- work lists: a saved, frozen list of rows being resolved (survives reloads and power cuts until someone confirms it is done)
const workKey = () => "gt_work_" + (ctx.meta?.me?.username || "");
let pendingWork = [];                                   // my unfinished lists on the server (offered after a reload)

function applyWorkIds() {                               // the grid shows the frozen rows (or only the ones still open)
  const w = S.work, base = w.openOnly ? w.skus.filter((s) => !S.dim.has(s)) : w.skus;
  S.ids = base.concat(S.drafts.map((d) => d.sku));
  el.body.style.height = S.ids.length * RH + "px";
  S.sel = null;
  render();
  drawWorkBar();
}

async function enterWork(id, openOnly = false) {
  const w = await api("/api/work/" + id);
  S.work = { id: w.id, name: w.name, skus: w.skus, total: w.total, openOnly };
  S.F = { q: w.params.q || "", queue: w.params.queue || "", sort: w.params.sort || "", cf: {} };
  for (const [k, v] of Object.entries(w.params)) if (k.startsWith("cf_")) S.F.cf[k.slice(3)] = v;
  if ($("#q")) $("#q").value = S.F.q;
  if ($("#fQueue")) $("#fQueue").value = S.F.queue;
  S.dim = new Set(w.resolved);
  S.touched = new Set(w.edited);
  S.cache.clear();
  store.set(workKey(), String(w.id));
  drawHead();
  el.scroller.scrollTop = 0;
  applyWorkIds();
  drawClearButton();
}

async function refreshWork() {                          // re-read what is edited / resolved from the server (rows stay where they are)
  if (!S.work) return;
  try {
    const w = await api("/api/work/" + S.work.id);
    S.dim = new Set(w.resolved);
    S.touched = new Set(w.edited);
    S.cache.clear();
    applyWorkIds();
  } catch (e) { toast(e.message, "bad"); }
}
const refreshWorkSoon = debounce(() => S.work && refreshWork(), 1500);

function leaveWork(announce) {                          // go back to the normal view; the list stays saved on the server
  if (!S.work) return;
  const name = S.work.name;
  S.work = null;
  S.touched.clear();
  S.dim.clear();
  store.set(workKey(), "");
  drawWorkBar();
  if (announce) toast(`“${name}” is saved. Continue it any time from the banner at the top of the Catalog.`);
}

async function startWork() {
  const rows = S.ids.filter((id) => !isDraft(id));
  if (!rows.length) return toast("There are no rows in this view to work through.", "bad");
  const label = S.F.queue ? (ctx.meta.queues[S.F.queue]?.label || S.F.queue) : "Filtered list";
  const r = await dialog({ title: "Start a work list", body: `<p class="muted">Freezes these <b>${fmt(rows.length)}</b> rows and saves the list on the server. Rows you fix stay in the list (marked), even if you reload or the power goes out. When you are done, press <b>Finish &amp; confirm</b>.</p>
    <label class="field"><span>Name</span><input name="name" autofocus value="${esc(label + " · " + new Date().toLocaleDateString())}" maxlength="80"></label>`,
    actions: [["Cancel", "close"], ["Start", "ok", "primary"]] });
  if (r.value !== "ok") return;
  try {
    const res = await api("/api/work", { name: new FormData(r.el.querySelector("form")).get("name"), params: filterParams(), skus: rows });
    await enterWork(res.id);
    toast(`Work list started: ${fmt(res.total)} rows are saved.`);
  } catch (e) { toast(e.message, "bad"); }
}

async function finishWork() {
  if (!S.work) return;
  let r;
  try { r = await api(`/api/work/${S.work.id}/finish`, {}); } catch (e) { return toast(e.message, "bad"); }
  if (!r.ok) {
    const d = await dialog({ title: "Not everything is resolved", body: `<p><b>${fmt(r.unresolved)}</b> of ${fmt(r.total)} rows still have this issue.</p>
      <p class="muted">You can keep working, look at only the rows that are still open, or finish anyway (those rows are recorded as skipped).</p>`,
      actions: [["Keep working", "close"], ["Show the open rows", "open"], ["Finish anyway", "force", "danger"]] });
    if (d.value === "open") { S.work.openOnly = true; return applyWorkIds(); }
    if (d.value !== "force") return;
    try { r = await api(`/api/work/${S.work.id}/finish`, { force: true }); } catch (e) { return toast(e.message, "bad"); }
  }
  const name = S.work.name, id = S.work.id;
  pendingWork = pendingWork.filter((x) => x.id !== id);              // first forget it, then redraw the banner
  leaveWork(false);
  toast(`“${name}” is confirmed: ${fmt(r.resolved)} of ${fmt(r.total)} resolved${r.skipped ? `, ${fmt(r.skipped)} skipped` : ""}.`);
  loadView(true);
}

async function discardWork(id, name) {
  let pv;
  try { pv = await api(`/api/work/${id}/revert-preview`); } catch (e) { return toast(e.message, "bad"); }
  const lines = [];
  if (pv.cells) lines.push(`${fmt(pv.cells)} change${pv.cells === 1 ? "" : "s"} in ${fmt(pv.products)} product${pv.products === 1 ? "" : "s"} will be put back to how they were when you started.`);
  else lines.push("You haven't changed anything in this list that can be undone.");
  if (pv.conflicts) lines.push(`${fmt(pv.conflicts)} cell${pv.conflicts === 1 ? " was" : "s were"} changed again by someone else (or the scraper) since, so ${pv.conflicts === 1 ? "it" : "they"} stay as they are.`);
  if (pv.unrecorded) lines.push(`${fmt(pv.unrecorded)} older edit${pv.unrecorded === 1 ? " has" : "s have"} no saved before-value and can't be undone.`);
  const actions = [["Cancel", "close"]];
  if (pv.cells) actions.push(["Discard, keep my changes", "keep"]);
  actions.push(pv.cells ? ["Undo my changes and discard", "undo", "primary"] : ["Discard", "keep", "primary"]);
  const choice = (await dialog({ title: `Discard “${name}”?`, body: `<p>${esc(lines.join(" "))}</p>`, actions })).value;
  if (choice !== "undo" && choice !== "keep") return;
  let res;
  try { res = await api(`/api/work/${id}/discard`, { revert: choice === "undo" }); } catch (e) { return toast(e.message, "bad"); }
  if (res.undone) toast(`Undid ${fmt(res.undone)} change${res.undone === 1 ? "" : "s"} in ${fmt(res.products)} product${res.products === 1 ? "" : "s"}.`);
  pendingWork = pendingWork.filter((x) => x.id !== id);
  if (S.work?.id === id) { leaveWork(false); loadView(true); } else drawWorkBar();
}

function drawWorkBar() {
  const bar = $("#workbar");
  if (!bar) return;
  if (S.work) {
    const w = S.work, resolved = Math.min(S.dim.size, w.total), open = w.total - resolved;
    bar.classList.remove("hidden");
    bar.innerHTML = `📋 <b>${esc(w.name)}</b> · ${fmt(w.total)} rows · <b>${fmt(S.touched.size)}</b> edited · <b>${fmt(resolved)}</b> resolved · <b>${fmt(open)}</b> still open
      <span class="grow"></span><button class="btn small" data-work="toggle">${w.openOnly ? "Show all rows" : "Show open rows only"}</button>
      <button class="btn small primary" data-work="finish" title="Confirm that this issue has been resolved">✔ Finish &amp; confirm</button>
      <button class="btn small" data-work="leave" title="Close the view but keep the list saved to continue later">Leave (keep saved)</button>
      <button class="btn small danger" data-work="discard">Discard</button>`;
  } else if (pendingWork.length) {
    bar.classList.remove("hidden");
    bar.innerHTML = `📋 You have <b>${pendingWork.length === 1 ? "an unfinished work list" : pendingWork.length + " unfinished work lists"}</b>:
      ${pendingWork.slice(0, 3).map((x) => `<span class="btnrow" style="gap:4px"><button class="btn small primary" data-work="resume" data-id="${x.id}" title="${fmt(x.edited)} of ${fmt(x.total)} edited, ${fmt(x.open)} still open">Continue “${esc(x.name)}” (${fmt(x.open)} open)</button><button class="btn small" data-work="drop" data-id="${x.id}" aria-label="Discard ${esc(x.name)}">✕</button></span>`).join(" ")}`;
  } else bar.classList.add("hidden");
  fitHeight();
}

async function offerWork(openFromLink) {                // after a reload or a power cut: resume the list that was open, or offer the saved ones
  try { pendingWork = (await api("/api/work")).items; } catch { pendingWork = []; }
  const pointer = Number(store.get(workKey(), "")) || 0;
  if (!openFromLink && pointer && pendingWork.some((x) => x.id === pointer)) {
    try { await enterWork(pointer); toast("Back on your work list. Nothing was lost."); return; } catch { store.set(workKey(), ""); }
  }
  drawWorkBar();
}

// ---- deleting rows
function dropRows(gone) {                                // take rows out of the sheet (after they were deleted, or unsaved new rows were thrown away)
  const top = S.sel ? normSel().r1 : 0, col = S.sel ? S.sel.c2 : 0;
  S.ids = S.ids.filter((id) => !gone.has(id));
  gone.forEach((id) => { S.cache.delete(id); S.dim.delete(id); S.draftMap.delete(id); });
  S.drafts = S.drafts.filter((d) => !gone.has(d.sku));
  ensureBlankDraft();
  S.ids = S.ids.filter((id) => !isDraft(id)).concat(S.drafts.map((d) => d.sku));
  persistDrafts();
  el.body.style.height = S.ids.length * RH + "px";
  if (S.sel) setSel(Math.min(top, S.ids.length - 1), col, Math.min(top, S.ids.length - 1), col, false); else render();
  drawDraftBar();
}

async function deleteRows(r1, r2) {
  const ids = S.ids.slice(r1, r2 + 1);
  const real = ids.filter((id) => !isDraft(id)), loose = ids.filter(isDraft);
  let res = null;
  if (real.length) {
    const shown = real.slice(0, 5).join(", ") + (real.length > 5 ? ` and ${fmt(real.length - 5)} more` : "");
    if (!(await confirmBox(`Delete ${fmt(real.length)} product${real.length === 1 ? "" : "s"}?`, `${shown}. They leave the catalog and the scraper stops checking them. Ctrl+Z brings them back.`, "Delete"))) return;
    try { res = await api("/api/products/delete", { skus: real }); } catch (e) { return toast(e.message, "bad"); }
  }
  dropRows(new Set([...(res?.skus || []), ...loose]));
  if (res?.deleted) {
    S.undo.push({ kind: "delete", skus: res.skus, label: "delete" });
    if (S.undo.length > 50) S.undo.shift();
    S.redo = [];
    ctx.refreshCounts();
  }
  el.scroller.focus();                                  // so Ctrl+Z works straight away
  statusLine(res?.deleted ? `Deleted ${fmt(res.deleted)} product${res.deleted === 1 ? "" : "s"}. Ctrl+Z brings ${res.deleted === 1 ? "it" : "them"} back.` : "");
  if (res?.deleted) {                                   // a toast with an Undo button: the status line alone is easy to miss
    const t = $("#toasts");
    t.innerHTML = `<div class="toast" role="status">Deleted ${fmt(res.deleted)} product${res.deleted === 1 ? "" : "s"}. <button class="btn small" id="undoDelete" style="margin-left:10px">Undo</button></div>`;
    $("#undoDelete").onclick = () => { t.innerHTML = ""; undo(false); };
    setTimeout(() => { if (t.querySelector("#undoDelete")) t.innerHTML = ""; }, 12000);
  }
}

// Recently deleted products, restorable even after a reload (Ctrl+Z only lasts until the page is refreshed).
async function restoreDialog() {
  let items;
  try { items = (await api("/api/products/deleted")).items; } catch (e) { return toast(e.message, "bad"); }
  if (!items.length) return toast("Nothing has been deleted recently.");
  const r = await dialog({ title: "Restore deleted products", wide: true, body: `<p class="muted">Tick the products to bring back. They return exactly as they were.</p>
    <div style="max-height:340px;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:4px">${items.map((x) => `<label class="check" style="display:flex;gap:8px;padding:4px 6px"><input type="checkbox" name="s" value="${esc(x.sku)}">
      <span class="mono" style="min-width:130px">${esc(x.sku)}</span><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.name)}</span><span class="muted">${esc(x.by)} · ${esc(x.at)}</span></label>`).join("")}</div>`,
    actions: [["Cancel", "close"], ["Restore ticked", "ok", "primary"]] });
  if (r.value !== "ok") return;
  const skus = [...r.el.querySelectorAll('input[name="s"]:checked')].map((x) => x.value);
  if (!skus.length) return;
  try {
    const res = await api("/api/products/restore", { skus });
    await loadView(false);
    ctx.refreshCounts();
    toast(`Restored ${fmt(res.restored)} product${res.restored === 1 ? "" : "s"}.${res.skipped ? ` ${fmt(res.skipped)} skipped (the SKU is in use again).` : ""}`);
  } catch (e) { toast(e.message, "bad"); }
}

// ---- right-click a cell: its edit history (who changed it, when, from what to what)
function openCellMenu(x, y, r, c) {
  document.querySelector(".hmenu")?.remove();
  const row = rowAt(r), col = visCols()[c];
  if (!row || !col) return;
  const m = document.createElement("div");
  m.className = "menu hmenu";
  m.style.cssText = `position:fixed;left:${Math.min(x, innerWidth - 270)}px;top:${Math.min(y + 4, innerHeight - 140)}px;min-width:230px`;
  m.innerHTML = `<div class="mhead">${esc(row._draft ? "New row" : row.sku)} · ${esc(col.l)}</div>
    <button data-cma="history" ${row._draft ? "disabled" : ""}>🕘 Show edit history</button>
    <button data-cma="open" ${row._draft ? "disabled" : ""}>Open product details</button>`;
  document.body.append(m);
  const close = (ev) => { if (!m.contains(ev.target) || ev.type === "keydown") { m.remove(); document.removeEventListener("mousedown", close, true); document.removeEventListener("keydown", close, true); } };
  setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", close, true); }, 0);
  m.addEventListener("click", (e) => {
    const b = e.target.closest("[data-cma]");
    if (!b || b.disabled) return;
    m.remove();
    if (b.dataset.cma === "history") showHistory(row, col);
    else openDrawer(row.sku, ctx, onRowChanged);
  });
}

async function showHistory(row, col) {
  if (col.t === "img" || col.t === "amzimg") return toast("Images come from the Michaels import and the scraper, so they have no edit history.");
  let h;
  try { h = await api("/api/sheet/history?" + qs({ sku: row.sku, field: col.k })); } catch (e) { return toast(e.message, "bad"); }
  const val = (v) => (v === "" ? `<span class="muted">(blank)</span>` : esc(v));
  const body = h.events.length
    ? `<div style="max-height:380px;overflow:auto"><table style="min-width:0"><thead><tr><th>When</th><th>Who</th><th>Changed</th></tr></thead><tbody>
        ${h.events.map((e) => `<tr><td style="white-space:nowrap" title="${esc(e.at)}">${esc(ago(e.at))}<div class="muted" style="font-size:12px">${esc(e.at)}</div></td>
          <td>${e.kind === "scraper" ? `<span class="pill s-info">${esc(e.who)}</span>` : `<b>${esc(e.who)}</b>`}</td>
          <td>${val(e.old)} <span class="muted">→</span> ${val(e.new)}${e.action && e.kind !== "scraper" ? `<div class="muted" style="font-size:12px">${esc(e.action)}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>
       <p class="hint">Edits made in the app are listed with the person's name; the Amazon scraper's changes show the scraper PC. Changes made by imports aren't listed per cell.</p>`
    : `<p class="muted">No changes recorded for this cell yet. Edits made from now on will show here, with who made them.</p>`;
  await dialog({ title: `Edit history · ${row.sku} · ${col.l}`, body, wide: true, actions: [["Close", "close", "primary"]] });
  el.scroller.focus();
}

function openRowMenu(x, y) {
  document.querySelector(".hmenu")?.remove();
  const sel = normSel();
  const n = sel.r2 - sel.r1 + 1;
  const m = document.createElement("div");
  m.className = "menu hmenu";
  m.style.cssText = `position:fixed;left:${Math.min(x, innerWidth - 270)}px;top:${Math.min(y + 4, innerHeight - 120)}px;min-width:220px`;
  m.innerHTML = `<button data-rma="delete" class="danger">Delete ${n === 1 ? "this row" : fmt(n) + " rows"}…</button>`;
  document.body.append(m);
  const close = (ev) => { if (!m.contains(ev.target) || ev.type === "keydown") { m.remove(); document.removeEventListener("mousedown", close, true); document.removeEventListener("keydown", close, true); } };
  setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", close, true); }, 0);
  m.addEventListener("click", () => { m.remove(); deleteRows(sel.r1, sel.r2); });
}

function goToNewRows() {
  const first = S.drafts.find(draftBlank) || S.drafts[0];
  const i = S.ids.indexOf(first.sku), skuCol = Math.max(0, visCols().findIndex((c) => c.k === "sku"));
  el.scroller.scrollTop = Math.max(0, (i + 2) * RH - el.scroller.clientHeight + HEADERS_H);
  setSel(i, skuCol);
  el.scroller.focus();
}

// ---- the fill handle: drag the little square at the corner of the selection down to copy or continue a series
function drawFillHandle() {
  el.inner.querySelector(".fillh")?.remove();
  const sel = normSel();
  if (!sel || S.edit || S.fill) return;
  const c = visCols()[sel.c2];
  if (!c || c.t === "img" || c.t === "amzimg") return;
  const x = c.pin ? el.scroller.scrollLeft + pinLeft(sel.c2) + colW(c) : colX()[sel.c2] + colW(c);
  const h = document.createElement("i");
  h.className = "fillh" + (c.pin ? " pinned" : "");
  h.title = "Drag down to fill. SKU123 continues as SKU124, SKU125…, numbers continue their step, other values repeat. Drag past the last row to add new rows.";
  h.style.cssText = `left:${x - 5}px;top:${HEADERS_H + (sel.r2 + 1) * RH - 5}px`;
  el.inner.append(h);
}

function startFill(e) {
  const sel = normSel();
  if (!sel) return;
  e.preventDefault();
  const sc = el.scroller, baseH = S.ids.length * RH;
  const box = document.createElement("div");
  box.className = "fillbox";
  el.inner.append(box);
  S.fill = { sel, end: sel.r2 };
  let lastY = e.clientY;
  const place = () => {
    const rect = sc.getBoundingClientRect();
    const row = Math.floor((lastY - rect.top + sc.scrollTop - HEADERS_H) / RH);
    S.fill.end = Math.max(sel.r2, Math.min(row, S.ids.length - 1 + MAX_NEW_ROWS));
    el.body.style.height = Math.max(S.ids.length, S.fill.end + 1) * RH + "px";          // room to drag past the last row
    const xs = colX(), cols = visCols();
    box.style.cssText = `left:${xs[sel.c1]}px;width:${xs[sel.c2] + colW(cols[sel.c2]) - xs[sel.c1]}px;top:${HEADERS_H + sel.r1 * RH}px;height:${(S.fill.end - sel.r1 + 1) * RH}px`;
    statusLine(S.fill.end > sel.r2 ? `Fill ${fmt(S.fill.end - sel.r2)} row${S.fill.end - sel.r2 === 1 ? "" : "s"}${S.fill.end >= S.ids.length ? ` (${fmt(S.fill.end - S.ids.length + 1)} new)` : ""}` : "");
  };
  const mv = (ev) => { lastY = ev.clientY; place(); };
  const timer = setInterval(() => {                                                        // scroll while the mouse rests near an edge
    const rect = sc.getBoundingClientRect();
    if (lastY > rect.bottom - 36) { sc.scrollTop += 24; place(); } else if (lastY < rect.top + HEADERS_H + 24) { sc.scrollTop -= 24; place(); }
  }, 40);
  const up = () => {
    removeEventListener("mousemove", mv); removeEventListener("mouseup", up); clearInterval(timer);
    const { end } = S.fill;
    box.remove();
    S.fill = null;
    el.body.style.height = baseH + "px";
    finishFill(sel, end);
  };
  addEventListener("mousemove", mv);
  addEventListener("mouseup", up);
  place();
}

async function finishFill(sel, end) {
  const cols = visCols();
  if (end <= sel.r2) { statusLine(); return render(); }
  await ensureRows(sel.r1, sel.r2);
  if (end >= S.ids.length) growTo(end);
  const n = end - sel.r2, edits = [];
  let skippedSku = 0;
  for (let c = sel.c1; c <= sel.c2; c++) {
    const col = cols[c];
    if (col.t === "img" || col.t === "amzimg") continue;
    const src = [];
    for (let r = sel.r1; r <= sel.r2; r++) { const row = rowAt(r); src.push(row ? rawValue(row, col) : ""); }
    const out = nextSeries(src, n);
    out.forEach((v, i) => {
      const id = S.ids[sel.r2 + 1 + i];
      if (col.k === "sku" && !isDraft(id)) { skippedSku++; return; }                       // never rename existing products by dragging
      edits.push({ sku: id, field: col.k, value: v });
    });
  }
  const real = edits.filter((x) => !isDraft(x.sku)).length;
  if (real > 300 && !(await confirmBox(`Fill ${fmt(real)} cells?`, "This overwrites existing products. You can undo with Ctrl+Z.", "Fill"))) { statusLine(); return render(); }
  await doEdits(edits, { label: "fill", action: "filled in the sheet" });
  setSel(sel.r1, sel.c1, end, sel.c2, false);
  if (skippedSku) toast("SKUs of existing products were left alone. Drag a SKU into new rows at the bottom to continue the series.");
}

// ------------------------------------------------------------------ images
function openImages(r) {
  const row = rowAt(r);
  if (!row) return;
  if (!row.images?.length && !row.amazon_image) return toast("No images for this product yet. Michaels images arrive with the Michaels import, the Amazon image with the scraper.");
  openCarousel(row);
}

// ------------------------------------------------------------------ editing
const dropdownOf = (col) => ctx.meta.dropdowns?.[col.k];              // the allowed values when an admin made the column a dropdown
function startEdit(r, c, replaceWith) {
  const col = visCols()[c], row = rowAt(r);
  if (!row || !col) return;
  if (col.t === "img" || col.t === "amzimg") return openImages(r);
  if (blockedMsg(col.k)) return toast(blockedMsg(col.k), "bad");
  if (row._draft && !draftEditable(col)) return toast("This column fills in after the product is saved.");
  if (col.t === "bool") return doEdits([{ sku: row.sku, field: col.k, value: row[col.k] ? "FALSE" : "TRUE" }]);
  closeEditor(false);
  const dd = dropdownOf(col);
  const inp = document.createElement(dd ? "select" : "input");
  inp.className = "editor";
  if (dd) {                                                          // a fixed dropdown: pick a value, no typing
    const cur = String(rawValue(row, col) ?? "");
    const vals = ["", ...dd, ...(cur && !dd.some((x) => x.toLowerCase() === cur.toLowerCase()) ? [cur] : [])];
    inp.innerHTML = vals.map((v) => `<option value="${esc(v)}">${esc(v || "(empty)")}</option>`).join("");
    const typed = replaceWith !== undefined ? vals.find((v) => v && v.toLowerCase().startsWith(replaceWith.toLowerCase())) : undefined;
    inp.value = typed ?? vals.find((v) => v.toLowerCase() === cur.toLowerCase()) ?? "";
    inp.addEventListener("change", () => { closeEditor(true); el.scroller.focus(); });
  } else {
    inp.value = replaceWith !== undefined ? replaceWith : rawValue(row, col);
    if (col.t === "status") inp.setAttribute("list", "dl_statuses");
    else if (col.list) inp.setAttribute("list", "dl_" + col.list);
    else if (col.k === "source") inp.setAttribute("list", "dl_source");
  }
  if (col.pin) inp.classList.add("pinned");                          // a frozen column's editor stays put while the sheet is scrolled
  inp.style.cssText = `left:${col.pin ? el.scroller.scrollLeft + pinLeft(c) : colX()[c]}px;top:${HEADERS_H + r * RH}px;width:${Math.max(colW(col), 120)}px;height:${RH}px`;
  S.edit = { r, c, sku: row.sku, field: col.k, original: rawValue(row, col), input: inp, done: false };
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); closeEditor(true); move(e.shiftKey ? -1 : 1, 0, false); el.scroller.focus(); }
    else if (e.key === "Tab") { e.preventDefault(); e.stopPropagation(); closeEditor(true); move(0, e.shiftKey ? -1 : 1, false); el.scroller.focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeEditor(false); el.scroller.focus(); }
    else e.stopPropagation();
  });
  inp.addEventListener("blur", () => closeEditor(true));
  el.inner.append(inp);
  inp.focus();
  if (dd) { try { inp.showPicker(); } catch {} }                    // open the list at once
  else if (replaceWith === undefined) inp.select(); else inp.setSelectionRange(inp.value.length, inp.value.length);
}
function closeEditor(commit) {
  const ed = S.edit;
  if (!ed || ed.done) return;
  ed.done = true;
  S.edit = null;
  const val = ed.input.value;
  ed.input.remove();
  if (commit && val !== ed.original) doEdits([{ sku: ed.sku, field: ed.field, value: val }]);
}

async function doEdits(edits, opts = {}) {
  if (!edits.length) return null;
  if (S.copied && opts.label !== "paste") S.copied = null;                  // typing or clearing ends the dashed "copied" border; pasting keeps it, as in Sheets
  const lockedEdits = edits.filter((e) => blockedMsg(e.field));      // paste, fill and Delete never touch a column that is protected against me
  if (lockedEdits.length) {
    edits = edits.filter((e) => !blockedMsg(e.field));
    toast(`${blockedMsg(lockedEdits[0].field)}${edits.length ? ` (${fmt(lockedEdits.length)} protected cell${lockedEdits.length === 1 ? "" : "s"} skipped, the rest were saved.)` : ""}`, "bad");
    if (!edits.length) return null;
  }
  edits = edits.map((e) => (S.draftAlias.has(e.sku) ? { ...e, sku: S.draftAlias.get(e.sku) } : e));     // a new row that has just been added
  const mine = edits.filter((e) => isDraft(e.sku));
  if (mine.length) {                                    // cells of new rows stay in the browser until Save new rows
    const r = applyDraftEdits(mine);
    edits = edits.filter((e) => !isDraft(e.sku));
    if (r.rejected) toast(`${fmt(r.rejected)} cell${r.rejected === 1 ? "" : "s"} skipped on new rows. ${r.why}`, "bad");
    if (!edits.length) { render(); statusLine(); return { changed: r.changed, errors: [], error_count: 0, updated_skus: [], before: [], rows: [], left_view: [] }; }
  }
  statusLine(`Saving ${fmt(edits.length)} cell${edits.length === 1 ? "" : "s"}…`);
  let res;
  try {
    res = await api("/api/sheet/cells", { edits, filter: filterParams(), action: opts.action });
  } catch (e) { toast(e.message, "bad"); statusLine(); return null; }
  absorb(res);
  if (res.changed && opts.undo !== false) {
    const done = new Set([...res.updated_skus, ...Object.keys(res.renamed || {})]);
    S.undo.push({ before: res.before, after: edits.filter((e) => done.has(e.sku)), label: opts.label || "edit" });
    if (S.undo.length > 50) S.undo.shift();
    S.redo = [];
  }
  render();
  if (res.error_count) {
    const e = res.errors[0];
    toast(`${fmt(res.changed)} saved. ${fmt(res.error_count)} cell${res.error_count === 1 ? "" : "s"} skipped: ${e.sku} ${e.error}`, "bad");
  } else if (res.changed) statusLine(`Saved ${fmt(res.changed)} product${res.changed === 1 ? "" : "s"}.`);
  else statusLine("Nothing changed.");
  ctx.refreshCounts();
  return res;
}

// Take a save response into the view: refreshed rows, rows that left the filter, and rows too many to send back.
function absorb(res) {
  for (const [from, to] of Object.entries(res.renamed || {})) {        // SKU renamed: the row keeps its place in the view
    const i = S.ids.indexOf(from);
    if (i >= 0) S.ids[i] = to;
    S.cache.delete(from);
    S.dim.delete(from);
  }
  const sent = new Set(res.rows.map((r) => r.sku));
  res.rows.forEach((row) => S.cache.set(row.sku, row));
  res.updated_skus.forEach((s) => { if (!sent.has(s)) S.cache.delete(s); });   // re-read when scrolled into view
  res.left_view.forEach((s) => S.dim.add(s));
  if (S.work) {                                          // rows edited during a work list are marked, and the saved counts are re-read shortly
    res.updated_skus.forEach((s) => S.touched.add(s));
    drawWorkBar();
    refreshWorkSoon();
  }
}

async function undo(redo = false) {
  const stack = redo ? S.redo : S.undo;
  const entry = stack.pop();
  if (!entry) return;
  if (entry.kind === "delete") {                        // undo = bring the products back, redo = delete them again
    try {
      if (redo) dropRows(new Set((await api("/api/products/delete", { skus: entry.skus })).skus));
      else { await api("/api/products/restore", { skus: entry.skus }); await loadView(false); }
      (redo ? S.undo : S.redo).push(entry);
      statusLine(redo ? "Deleted again." : `Restored ${fmt(entry.skus.length)} product${entry.skus.length === 1 ? "" : "s"}.`);
      ctx.refreshCounts();
    } catch (e) { toast(e.message, "bad"); }
    return;
  }
  const res = await doEdits(redo ? entry.after : entry.before, { undo: false, action: redo ? "redid an edit" : "undid an edit" });
  if (res) (redo ? S.undo : S.redo).push(entry);
  statusLine(redo ? "Redone." : "Undone.");
}

// ------------------------------------------------------------------ clipboard
function parseTSV(text) {
  const rows = [];
  let row = [], cell = "", q = false, i = 0;
  text = text.replace(/\r\n?/g, "\n");
  for (; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch;
    } else if (ch === '"' && cell === "") q = true;
    else if (ch === "\t") { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}
const tsvCell = (s) => (/[\t\n"]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s);

async function selectionText(sel = normSel()) {
  const cols = visCols();
  await ensureRows(sel.r1, sel.r2);
  const lines = [];
  for (let r = sel.r1; r <= sel.r2; r++) {
    const row = rowAt(r);
    lines.push(cols.slice(sel.c1, sel.c2 + 1).map((c) => tsvCell(row ? display(row, c) : "")).join("\t"));
  }
  return lines.join("\n");
}
// Space over a highlighted block: flip every TRUE/FALSE cell in it together, like checkboxes in Google Sheets.
// If every one is TRUE they all become FALSE, otherwise they all become TRUE. Other columns in the block are left alone.
async function toggleChecks() {
  if (!S.sel) return;
  const sel = normSel(), cols = visCols();
  const picked = [...pickedCells()].filter((p) => cols[p.c].t === "bool");
  if (!picked.length) return;
  statusLine(`Loading ${fmt(new Set(picked.map((p) => p.r)).size)} rows…`);
  for (const x of rects()) await ensureRows(x.r1, x.r2);
  const cells = [];
  for (const p of picked) {
    const row = rowAt(p.r);
    if (row) cells.push({ sku: row.sku, field: cols[p.c].k, on: !!row[cols[p.c].k] });
  }
  if (!cells.length) return;
  const value = cells.every((x) => x.on) ? "FALSE" : "TRUE";
  const edits = cells.filter((x) => x.on !== (value === "TRUE")).map((x) => ({ sku: x.sku, field: x.field, value }));
  if (edits.length) await doEdits(edits);
}

function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;left:-9999px";
  document.body.append(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  el.scroller.focus();
  return ok;
}
async function copySelection(cut) {
  if (!S.sel) return;
  const rs = rects();
  const sameRows = rs.every((x) => x.r1 === rs[0].r1 && x.r2 === rs[0].r2), sameCols = rs.every((x) => x.c1 === rs[0].c1 && x.c2 === rs[0].c2);
  if (rs.length > 1 && !sameRows && !sameCols) return toast("Can't copy several blocks unless they share the same rows or the same columns.", "bad");
  const cells = rs.reduce((n, x) => n + (x.r2 - x.r1 + 1) * (x.c2 - x.c1 + 1), 0);
  statusLine(`Copying ${fmt(cells)} cells…`);
  // blocks that share rows are laid side by side, blocks that share columns are stacked (what Google Sheets does)
  const parts = [];
  for (const x of rs.slice().sort((a, b) => a.r1 - b.r1 || a.c1 - b.c1)) parts.push(await selectionText(x));
  const text = rs.length > 1 && sameRows ? parts.map((p) => p.split("\n")).reduce((a, b) => a.map((l, i) => l + "\t" + b[i])).join("\n") : parts.join("\n");
  let ok = false;
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); ok = true; } } catch {}
  if (!ok) ok = legacyCopy(text);
  statusLine(ok ? `Copied ${fmt(cells)} cell${cells === 1 ? "" : "s"}.` : "Couldn't copy. Your browser blocked clipboard access.");
  if (ok && cut) { S.copied = null; clearCells(); }
  else if (ok) { S.copied = { rects: rs.map((x) => ({ ...x })), n: S.ids.length }; render(); }
}

async function pasteText(text) {
  if (!S.sel || !text) return;
  const cols = visCols(), sel = normSel();
  let grid = parseTSV(text);
  if (grid.length > 1 && grid[grid.length - 1].length === 1 && grid[grid.length - 1][0] === "") grid.pop();
  const single = grid.length === 1 && grid[0].length === 1;
  const edits = [];
  let skippedRO = 0;
  const put = (r, c, v) => {
    if (r >= S.ids.length && c < cols.length) growTo(r);               // pasting below the last row adds new rows
    if (r >= S.ids.length || c >= cols.length) return;
    if (cols[c].t === "img" || cols[c].t === "amzimg") { skippedRO++; return; }        // images come from the imports and the scraper
    edits.push({ sku: S.ids[r], field: cols[c].k, value: v });
  };
  if (single && (sel.r2 > sel.r1 || sel.c2 > sel.c1)) {
    for (let r = sel.r1; r <= sel.r2; r++) for (let c = sel.c1; c <= sel.c2; c++) put(r, c, grid[0][0]);
  } else {
    grid.forEach((line, dr) => line.forEach((v, dc) => put(sel.r1 + dr, sel.c1 + dc, v)));
  }
  if (!edits.length) return toast(skippedRO ? "Those columns are read-only, so nothing was pasted." : "Nothing to paste.", "bad");
  if (edits.length > 300 && !(await confirmBox(`Paste ${fmt(edits.length)} cells?`, `This changes up to ${fmt(new Set(edits.map((e) => e.sku)).size)} products. You can undo it with Ctrl+Z.`, "Paste"))) return;
  const res = await doEdits(edits, { label: "paste", action: "pasted into the sheet" });
  if (res && skippedRO) toast(`Pasted. ${fmt(skippedRO)} cells in read-only columns were skipped.`);
}

async function clearCells() {
  const cols = visCols(), edits = [];
  for (const { r, c } of pickedCells()) if ((isDraft(S.ids[r]) || !["sku", "name"].includes(cols[c].k)) && cols[c].t !== "bool" && cols[c].t !== "img" && cols[c].t !== "amzimg") edits.push({ sku: S.ids[r], field: cols[c].k, value: "" });
  if (edits.length > 300 && !(await confirmBox(`Clear ${fmt(edits.length)} cells?`, "You can undo this with Ctrl+Z.", "Clear"))) return;
  await doEdits(edits, { label: "clear" });
}
async function fillDown() {
  const sel = normSel(), cols = visCols();
  if (sel.r2 === sel.r1) return;
  await ensureRows(sel.r1, sel.r1);
  const top = rowAt(sel.r1), edits = [];
  for (let c = sel.c1; c <= sel.c2; c++) {
    if (cols[c].k === "sku" || cols[c].t === "img" || cols[c].t === "amzimg") continue;   // filling a SKU down would rename every row to the same SKU
    for (let r = sel.r1 + 1; r <= sel.r2; r++) edits.push({ sku: S.ids[r], field: cols[c].k, value: rawValue(top, cols[c]) });
  }
  if (edits.length > 300 && !(await confirmBox(`Fill ${fmt(edits.length)} cells?`, "The top row's values are copied down. You can undo with Ctrl+Z.", "Fill down"))) return;
  await doEdits(edits, { label: "fill", action: "filled down in the sheet" });
}

// ------------------------------------------------------------------ bulk actions
function scope() {
  const sel = normSel();
  if (sel && sel.r2 > sel.r1) {
    const skus = S.ids.slice(sel.r1, sel.r2 + 1).filter((id) => !isDraft(id));            // unsaved new rows aren't products yet
    return { skus, label: `the ${fmt(skus.length)} selected rows`, selected: true };
  }
  const skus = S.ids.filter((id) => !isDraft(id));
  return { skus, label: `all ${fmt(skus.length)} rows in this view`, selected: false };
}
function openMenu() {
  const sc = scope();
  const menu = $("#actionsMenu");
  menu.innerHTML = `<div class="mhead">Applies to ${sc.label}</div>
    <button data-act="amazon">Use Amazon price as SOURCE PRICE</button>
    <hr><button data-act="set:listing_status">Set LISTING STATUS…</button><button data-act="set:manual_status">Set STATUS COUNTER…</button>
    <button data-act="set:category">Set CATEGORY…</button><button data-act="set:remarks">Set OPTIMIZER REMARKS…</button>
    <hr><button data-act="flag:inventory:TRUE">Inventory ON</button><button data-act="flag:inventory:FALSE">Inventory OFF</button>
    <button data-act="flag:listed:TRUE">Mark Listed</button><button data-act="flag:listed:FALSE">Mark not Listed</button>
    <hr><button data-act="recheck">Check again on Amazon (scraper)</button><button data-act="accept_title">Titles are fine (hide mismatch)</button>
    <hr><button data-act="restore">Restore deleted products…</button><button data-act="delete" class="danger">Delete the selected rows…</button>`;
  menu.classList.toggle("hidden");
}
async function runAction(act) {
  $("#actionsMenu").classList.add("hidden");
  if (act === "restore") return restoreDialog();
  if (act === "delete") {                               // works on the highlighted rows, never on "everything in the view"
    const sel = normSel();
    return sel ? deleteRows(sel.r1, sel.r2) : toast("Click a row number to select the row(s) to delete first.", "bad");
  }
  const sc = scope();
  if (!sc.skus.length) return toast("There are no rows to change.", "bad");
  if (act === "amazon") {
    if (!(await confirmBox("Use the Amazon price as source price?", `For ${sc.label}, SOURCE PRICE becomes the SCRAP PRICE (rows with no scraped price are skipped). The rows stay in your view, and you can undo with Ctrl+Z.`, "Update source price"))) return;
    statusLine("Updating…");
    const res = await api("/api/sheet/use-amazon-price", { skus: sc.skus, filter: filterParams() }).catch((e) => { toast(e.message, "bad"); return null; });
    if (!res) return statusLine();
    absorb(res);
    if (res.changed) { S.undo.push({ before: res.before, after: res.after, label: "amazon" }); S.redo = []; }
    render();
    toast(`${fmt(res.changed)} source price${res.changed === 1 ? "" : "s"} updated${res.skipped_no_price ? `, ${fmt(res.skipped_no_price)} skipped (no Amazon price)` : ""}.`);
    ctx.refreshCounts();
    return;
  }
  if (act === "recheck") {
    if (!(await confirmBox(`Check ${fmt(sc.skus.length)} product${sc.skus.length === 1 ? "" : "s"} on Amazon again?`, `For ${sc.label}. They go to the front of the scraper queue. A scraper PC does the checking, so if no scraper is running, press Start on one. The new results replace the old ones.`, "Check again"))) return;
    const r = await api("/api/scraper/recheck", { skus: sc.skus }).catch((e) => { toast(e.message, "bad"); return null; });
    if (r) toast(`${fmt(r.queued)} queued for a re-check${r.skipped_no_link ? ` (${fmt(r.skipped_no_link)} have no Amazon link)` : ""}. ${r.joined_open_run ? "They are first in line in the current run." : "Press Start on a scraper PC to begin."}`);
    return;
  }
  if (act === "accept_title") {
    if (!(await confirmBox("Apply to " + fmt(sc.skus.length) + " products?", "This can't be undone with Ctrl+Z.", "Apply"))) return;
    const r = await api("/api/products/bulk", { skus: sc.skus, action: act });
    toast(`${fmt(r.changed)} updated.`);
    S.cache.clear(); render(); ctx.refreshCounts();
    return;
  }
  let field, value, label;
  if (act.startsWith("flag:")) { [, field, value] = act.split(":"); label = `${field === "inventory" ? "Inventory" : "Listed"} → ${value}`; }
  else {
    field = act.split(":")[1];
    const names = { listing_status: "LISTING STATUS", manual_status: "STATUS COUNTER", category: "CATEGORY", remarks: "OPTIMIZER REMARKS" };
    const list = field === "listing_status" ? ctx.meta.listing_statuses : field === "manual_status" ? ctx.meta.manual_statuses : field === "category" ? ctx.meta.categories : [];
    const fixed = ctx.meta.dropdowns?.[field];                           // a fixed dropdown: only its values can be chosen
    const input = fixed
      ? `<select name="v" autofocus><option value=""></option>${fixed.map((x) => `<option>${esc(x)}</option>`).join("")}</select>`
      : `<input name="v" list="dlg_list" autofocus placeholder="Type or pick a value"><datalist id="dlg_list">${list.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>`;
    const r = await dialog({ title: `Set ${names[field]} for ${sc.label}`, body: input, actions: [["Cancel", "close"], ["Continue", "ok", "primary"]] });
    if (r.value !== "ok") return;
    value = new FormData(r.el.querySelector("form")).get("v");
    label = `${names[field]} → ${value || "(blank)"}`;
  }
  if (!(await confirmBox(`Apply to ${fmt(sc.skus.length)} products?`, `${label}. You can undo with Ctrl+Z.`, "Apply"))) return;
  await doEdits(sc.skus.map((sku) => ({ sku, field, value })), { label, action: "bulk edited in the sheet" });
  S.cache.clear(); render();
}

// ------------------------------------------------------------------ events
function wire() {
  const sc = el.scroller;
  if (!wired) {          // document-level handlers are added once; they always act on the current grid
    wired = true;
    addEventListener("resize", () => el.scroller?.isConnected && fitHeight());
    addEventListener("mouseup", () => { if (S) { S.drag = false; S.add = false; } });
    document.addEventListener("keydown", (e) => {                  // Ctrl+Z / Ctrl+Y work even when the grid doesn't have the focus (for example right after a dialog closes)
      if (!el.scroller?.isConnected || el.scroller.contains(e.target) || document.querySelector("dialog[open]")) return;
      if (e.target.closest?.("input, select, textarea") || !(e.ctrlKey || e.metaKey) || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "z" || k === "y") { e.preventDefault(); undo(k === "y" || e.shiftKey); }
    });
    addEventListener("beforeunload", (e) => {                       // unsaved new rows: ask before the page closes
      if (el.scroller?.isConnected && S?.drafts.some((d) => !draftBlank(d))) { e.preventDefault(); e.returnValue = ""; }
    });
    document.addEventListener("paste", (e) => {
      if (!el.scroller?.isConnected || S.edit || !S.focused) return;
      if (e.target.closest?.("input, select, textarea")) return;   // pasting into a filter box stays in the box
      e.preventDefault();
      pasteText(e.clipboardData.getData("text/plain"));
    });
  }
  el.inner.addEventListener("mousedown", (e) => { if (e.target.closest(".fillh")) startFill(e); });
  el.body.addEventListener("contextmenu", (e) => {                    // right-click a cell: Show edit history; a row number: delete the row(s)
    const rn = e.target.closest(".gc.rn");
    if (!rn) {
      const cell = cellFromEvent(e);
      if (!cell || cell.c < 0) return;
      e.preventDefault();
      const sel = S.sel && normSel();
      if (!sel || cell.r < sel.r1 || cell.r > sel.r2 || cell.c < sel.c1 || cell.c > sel.c2) setSel(cell.r, cell.c, cell.r, cell.c, false);
      return openCellMenu(e.clientX, e.clientY, cell.r, cell.c);
    }
    e.preventDefault();
    const r = +rn.dataset.r, sel = S.sel && normSel();
    if (!sel || r < sel.r1 || r > sel.r2) setSel(r, 0, r, visCols().length - 1, false);
    openRowMenu(e.clientX, e.clientY);
  });
  sc.addEventListener("scroll", () => { requestAnimationFrame(render); closeEditor(true); });
  sc.addEventListener("focus", () => (S.focused = true));
  sc.addEventListener("blur", () => setTimeout(() => (S.focused = el.scroller.contains(document.activeElement)), 0));

  el.body.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || e.target.closest(".editor")) return;
    const cell = cellFromEvent(e);
    if (!cell) return;
    closeEditor(true);
    const cols = visCols();
    if (e.target.closest("[data-img], [data-amz]")) {          // an image button: select the cell and open the viewer
      e.preventDefault();                          // (on mouse-down: selecting redraws the row, which would swallow a click)
      setSel(cell.r, cell.c, cell.r, cell.c, false);
      openImages(cell.r);
      return;
    }
    if (e.detail === 2 && !e.shiftKey && !e.ctrlKey && !e.metaKey && cols[cell.c]?.k === "link") {          // SOURCE LINK: double-click opens the page
      e.preventDefault();                                          // (read on the second mouse-down: selecting redraws the cells, which can swallow the browser's own double-click event)
      setSel(cell.r, cell.c, cell.r, cell.c, false);
      openLink(cell.r);
      return;
    }
    const add = (e.ctrlKey || e.metaKey) && !e.shiftKey && !!S.sel;       // Ctrl+click: add another block, keep the ones already picked
    const keep = add ? rects() : null;
    S.add = add;
    if (cell.c === -1) {                       // row number: select the whole row
      if (e.shiftKey && S.sel) setSel(S.sel.r1, 0, cell.r, cols.length - 1, false); else setSel(cell.r, 0, cell.r, cols.length - 1, false);
      S.drag = "row";
    } else {
      if (e.shiftKey && S.sel) setSel(S.sel.r1, S.sel.c1, cell.r, cell.c, false); else setSel(cell.r, cell.c, cell.r, cell.c, false);
      S.drag = "cell";
    }
    if (keep) { S.multi = keep; render(); }
    sc.focus();
    e.preventDefault();
  });
  el.body.addEventListener("mouseover", (e) => {
    if (!S.drag) return;
    const cell = cellFromEvent(e);
    if (!cell || !S.sel) return;
    if (S.drag === "row") setSel(S.sel.r1, 0, cell.r, visCols().length - 1, false);
    else if (cell.c >= 0) setSel(S.sel.r1, S.sel.c1, cell.r, cell.c, false);
  });
  el.body.addEventListener("click", (e) => {                        // a click on the checkbox itself switches that one row
    const cb = e.target.closest(".cbx");
    if (!cb) return;
    e.preventDefault();
    const cell = cellFromEvent(e), row = cell && rowAt(cell.r), col = cell && visCols()[cell.c];
    if (row && col?.t === "bool") doEdits([{ sku: row.sku, field: col.k, value: row[col.k] ? "FALSE" : "TRUE" }]);
  });
  el.body.addEventListener("dblclick", (e) => {
    if (e.target.closest(".cbx")) return;                           // two quick clicks on a checkbox are two switches, not an edit
    const cell = cellFromEvent(e);
    if (!cell) return;
    if (cell.c === -1) { const row = rowAt(cell.r); if (row && !row._draft) openDrawer(row.sku, ctx, onRowChanged); }
    else if (visCols()[cell.c]?.k !== "link") startEdit(cell.r, cell.c);       // SOURCE LINK opens its page on the second mouse-down instead (see openLink)
  });

  el.head.addEventListener("click", (e) => {
    const sb = e.target.closest("[data-sort]");
    if (sb) {
      const k = sb.dataset.sort;
      S.F.sort = S.F.sort === k ? "-" + k : S.F.sort === "-" + k ? "" : k;
      return loadView(true);
    }
    const hm = e.target.closest("[data-hm]");
    if (hm) { const r = hm.getBoundingClientRect(); return openHeaderMenu(+hm.dataset.hm, r.left, r.bottom); }
    if (e.target.closest("[data-corner]")) return S.ids.length && setSel(0, 0, S.ids.length - 1, visCols().length - 1, false);
    const hc = e.target.closest("[data-hc]");
    if (hc && !e.target.closest("input,select,.rz,button") && S.ids.length) { const i = +hc.dataset.hc, keep = (e.ctrlKey || e.metaKey) && S.sel ? rects() : null; S.add = !!keep; setSel(0, i, S.ids.length - 1, i, false); S.add = false; if (keep) { S.multi = keep; render(); } sc.focus(); }
  });
  el.head.addEventListener("contextmenu", (e) => {
    const hc = e.target.closest("[data-hc]");
    if (!hc) return;
    e.preventDefault();
    openHeaderMenu(+hc.dataset.hc, e.clientX, e.clientY);
  });
  const applyFilters = debounce(() => loadView(true), 350);
  el.head.addEventListener("input", (e) => {
    const k = e.target.dataset.cf;
    if (!k) return;
    S.F.cf[k] = e.target.value;
    applyFilters();
  });
  el.head.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pick]");
    if (b) openPicker(visCols().find((c) => c.k === b.dataset.pick), b);
  });
  el.head.addEventListener("mousedown", (e) => {          // drag a column edge to resize
    const rz = e.target.closest("[data-rz]");
    if (!rz) return;
    e.preventDefault();
    const c = visCols()[+rz.dataset.rz], startX = e.clientX, w0 = colW(c);
    const mv = (ev) => { S.widths[c.k] = Math.max(50, w0 + ev.clientX - startX); drawHead(); el.inner.style.width = totalW() + "px"; render(); };
    const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); store.set("gt_col_w", JSON.stringify(S.widths)); };
    addEventListener("mousemove", mv);
    addEventListener("mouseup", up);
  });

  sc.addEventListener("keydown", (e) => {
    if (S.edit) return;
    if (e.target.closest("input, select, textarea")) return;     // typing in a filter box is not a grid shortcut
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key;
    if (mod && k.toLowerCase() === "z") { e.preventDefault(); return undo(e.shiftKey); }
    if (mod && k.toLowerCase() === "y") { e.preventDefault(); return undo(true); }
    if (mod && k.toLowerCase() === "d") { e.preventDefault(); return S.sel && fillDown(); }
    if (mod && k.toLowerCase() === "a") { e.preventDefault(); return S.ids.length && setSel(0, 0, S.ids.length - 1, visCols().length - 1, false); }
    if (mod && (k.toLowerCase() === "c" || k.toLowerCase() === "x")) { e.preventDefault(); return copySelection(k.toLowerCase() === "x"); }
    if (mod && k.toLowerCase() === "v") return;                     // handled by the paste event
    if (mod && k === "ArrowDown") { e.preventDefault(); return setSel(e.shiftKey && S.sel ? S.sel.r1 : S.ids.length - 1, S.sel?.c1 ?? 0, S.ids.length - 1, S.sel?.c2 ?? 0); }
    if (mod && k === "ArrowUp") { e.preventDefault(); return setSel(e.shiftKey && S.sel ? S.sel.r1 : 0, S.sel?.c1 ?? 0, 0, S.sel?.c2 ?? 0); }
    const nav = { ArrowDown: [1, 0], ArrowUp: [-1, 0], ArrowRight: [0, 1], ArrowLeft: [0, -1] }[k];
    if (nav) { e.preventDefault(); return move(nav[0], nav[1], e.shiftKey); }
    if (k === "PageDown" || k === "PageUp") { e.preventDefault(); return move((k === "PageDown" ? 1 : -1) * Math.floor(sc.clientHeight / RH - 3), 0, e.shiftKey); }
    if (k === "Home") { e.preventDefault(); return S.sel && setSel(S.sel.r2, 0); }
    if (k === "End") { e.preventDefault(); return S.sel && setSel(S.sel.r2, visCols().length - 1); }
    if (!S.sel) return;
    if (k === "Tab") { e.preventDefault(); return move(0, e.shiftKey ? -1 : 1, false); }
    if (k === "Enter" && e.shiftKey) { e.preventDefault(); const row = rowAt(S.sel.r2); return row && !row._draft && openDrawer(row.sku, ctx, onRowChanged); }
    if (k === "Enter" || k === "F2") { e.preventDefault(); return startEdit(S.sel.r2, S.sel.c2); }
    if (k === " " && e.shiftKey && !mod) { e.preventDefault(); return setSel(S.sel.r1, 0, S.sel.r2, visCols().length - 1, false); }   // Shift+Space: whole row(s)
    if (k === " " && mod) { e.preventDefault(); return setSel(0, S.sel.c1, S.ids.length - 1, S.sel.c2, false); }                      // Ctrl+Space: whole column(s)
    if (k === " " && !e.altKey && rects().some((x) => visCols().slice(x.c1, x.c2 + 1).some((c) => c.t === "bool"))) { e.preventDefault(); return toggleChecks(); }
    if (k === "Delete" || k === "Backspace") { e.preventDefault(); return clearCells(); }
    if (k === "Escape") { S.multi = []; S.copied = null; S.sel = { ...S.sel, r1: S.sel.r2, c1: S.sel.c2 }; return render(); }
    if (k.length === 1 && !mod && !e.altKey) { e.preventDefault(); startEdit(S.sel.r2, S.sel.c2, k); }
  });
  // toolbar
  root.onclick = async (e) => {
    const t = e.target;
    if (!t.closest(".menuwrap")) $("#actionsMenu")?.classList.add("hidden");
    if (t.closest("#undo")) return undo(false);
    if (t.closest("#redo")) return undo(true);
    if (t.closest("#refresh")) return loadView(false);
    if (t.closest("#newRows")) return goToNewRows();
    if (t.closest("#clearFilters")) return clearAllFilters();
    if (t.closest("#startWork")) return startWork();
    const wk = t.closest("[data-work]");
    if (wk) {
      const a = wk.dataset.work, id = Number(wk.dataset.id);
      if (a === "finish") return finishWork();
      if (a === "toggle") { S.work.openOnly = !S.work.openOnly; return applyWorkIds(); }
      if (a === "leave") { leaveWork(true); return loadView(true); }
      if (a === "discard") return discardWork(S.work.id, S.work.name);
      if (a === "resume") return enterWork(id).catch((e) => toast(e.message, "bad"));
      if (a === "drop") return discardWork(id, pendingWork.find((x) => x.id === id)?.name || "this list");
    }
    const dr = t.closest("[data-draft]");
    if (dr) return discardDrafts();
    if (t.closest("#actionsBtn")) return openMenu();
    const a = t.closest("[data-act]");
    if (a) return runAction(a.dataset.act);
    if (t.closest("#colsBtn")) return columnsDialog();
    if (t.closest("#addColBtn")) return addColumnDialog(S.sel ? visCols()[S.sel.c2]?.k : null);
    if (t.closest("#exportBtn")) return exportView();
    const vb = t.closest("[data-view]");
    if (vb) {
      const v = S.views.find((x) => x.id == vb.dataset.view);
      S.F = { q: v.params.q || "", queue: v.params.queue || "", sort: v.params.sort || "", cf: {} };
      for (const [k, val] of Object.entries(v.params)) if (k.startsWith("cf_")) S.F.cf[k.slice(3)] = val;
      skeleton();
      return loadView(true);
    }
    const dv = t.closest("[data-delview]");
    if (dv) { await api(`/api/views/${dv.dataset.delview}/delete`, {}); S.views = (await api("/api/views")).items; return drawViews(); }
    if (t.closest("#saveView")) {
      const r = await dialog({ title: "Save current filters", body: `<label class="field"><span>Name</span><input name="name" required autofocus placeholder="e.g. High price – Home Decor"></label>
        ${ctx.isAdmin() ? `<label class="check"><input type="checkbox" name="shared"> Share with the whole team</label>` : ""}`, actions: [["Cancel", "close"], ["Save view", "ok", "primary"]] });
      if (r.value === "ok") {
        const f = new FormData(r.el.querySelector("form"));
        await api("/api/views", { name: f.get("name"), params: filterParams(), shared: !!f.get("shared") });
        S.views = (await api("/api/views")).items; drawViews(); toast("View saved.");
      }
    }
  };
  root.onchange = (e) => { if (e.target.id === "fQueue") { S.F.queue = e.target.value; loadView(true); } else if (e.target.dataset.cf !== undefined) { S.F.cf[e.target.dataset.cf] = e.target.value; loadView(true); } };
  root.oninput = debounce((e) => { if (e.target.id === "q") { S.F.q = e.target.value; loadView(true); } }, 300);
}

async function onRowChanged(sku) {
  const r = await api("/api/sheet/rows", { skus: [sku] });
  r.rows.forEach((row) => S.cache.set(row.sku, row));
  render();
}

// Re-read the custom columns from the server and redraw the grid around them.
async function reloadColumns(keepFilters = true) {
  await ctx.refreshMeta();
  const known = new Set(ctx.meta.custom_columns.map((c) => c.key));
  for (const k of Object.keys(S.F.cf)) if (k.startsWith("x_") && !known.has(k)) delete S.F.cf[k];
  if (S.F.sort.replace("-", "").startsWith("x_") && !known.has(S.F.sort.replace("-", ""))) S.F.sort = "";
  rebuildCols();
  S.sel = null;
  await loadView(!keepFilters);
}

function openHeaderMenu(ci, x, y) {
  document.querySelector(".hmenu")?.remove();
  const c = visCols()[ci];
  const admin = ctx.isAdmin();
  const m = document.createElement("div");
  m.className = "menu hmenu";
  m.style.cssText = `position:fixed;left:${Math.min(x, innerWidth - 270)}px;top:${Math.min(y + 4, innerHeight - 280)}px`;
  m.innerHTML = `<div class="mhead">${esc(c.l)}</div>
    <button data-hma="asc">Sort A → Z</button><button data-hma="desc">Sort Z → A</button><button data-hma="nosort">Clear sort</button><hr>
    ${c.t === "img" || c.t === "amzimg" ? "" : `<button data-hma="protect" ${admin ? "" : "disabled"}>🔒 ${lockOf(c.k) ? "Change protection…" : "Protect this column…"}${admin ? "" : " (admins only)"}</button>
      ${lockOf(c.k) && !admin ? `<div class="mhead">${esc(lockTip(c.k))}</div>` : ""}<hr>`}
    ${c.t === "text" && !c.calc && !NO_DROPDOWN.has(c.k) ? `<button data-hma="dropdown" ${admin ? "" : "disabled"}>▾ ${dropdownOf(c) ? "Edit the dropdown values…" : "Make this a dropdown…"}${admin ? "" : " (admin)"}</button>` : ""}
    <button data-hma="insert" ${admin ? "" : "disabled"}>Insert a column to the right…${admin ? "" : " (admin)"}</button>
    ${c.pin ? "" : `<button data-hma="hide">${c.custom ? "Hide this column" : "Remove from view (hide)"}</button>`}
    ${c.custom ? `<button data-hma="delete" class="danger" ${admin ? "" : "disabled"}>Delete this column and its values…${admin ? "" : " (admin)"}</button>`
               : `<div class="mhead">A built-in column can't be deleted (pricing and statuses depend on it). Removing it only hides it, and <b>Columns</b> brings it back.</div>`}`;
  document.body.append(m);
  const close = (ev) => { if (!m.contains(ev.target) || ev.type === "keydown") { m.remove(); document.removeEventListener("mousedown", close, true); document.removeEventListener("keydown", close, true); } };
  setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", close, true); }, 0);
  m.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-hma]");
    if (!b || b.disabled) return;
    m.remove();
    const a = b.dataset.hma;
    if (a === "asc") { S.F.sort = c.k; loadView(true); }
    else if (a === "desc") { S.F.sort = "-" + c.k; loadView(true); }
    else if (a === "nosort") { S.F.sort = ""; loadView(true); }
    else if (a === "hide") hideColumns([c.k]);
    else if (a === "insert") addColumnDialog(c.k);
    else if (a === "protect") protectDialog(c);
    else if (a === "dropdown") dropdownDialog(c);
    else if (a === "delete") deleteColumn(c);
  });
}

// Turn a text column into a fixed dropdown (people pick a value instead of typing), edit its values, or turn it off (admins only).
async function dropdownDialog(c) {
  const on = dropdownOf(c);
  let values = on, more = false;
  if (!on) {
    try { const r = await api("/api/dropdowns/values?col=" + encodeURIComponent(c.k)); values = r.values; more = r.more; } catch (e) { return toast(e.message, "bad"); }
  }
  const body = `<p class="muted">${on ? `People can only pick one of these values in “${esc(c.l)}”.` : `People will pick a value in “${esc(c.l)}” instead of typing it. The values the column holds now are listed; add, change or remove lines as you like.`}</p>
    <label class="field"><span>Values, one per line</span><textarea name="v" rows="10" spellcheck="false">${esc(values.join("\n"))}</textarea></label>
    ${more ? `<p class="hint">Only the first 300 values the column holds are listed.</p>` : ""}
    <p class="hint">A row that already holds a value not on the list keeps it until someone changes it. Typing, pasting or filling a value that isn't on the list is refused. Imports and the scraper are not checked.</p>`;
  const r = await dialog({ title: on ? `Dropdown values for “${c.l}”` : `Make “${c.l}” a dropdown`, body,
    actions: on ? [["Cancel", "close"], ["Back to free typing", "off", "danger"], ["Save", "ok", "primary"]] : [["Cancel", "close"], ["Make it a dropdown", "ok", "primary"]] });
  if (r.value !== "ok" && r.value !== "off") return;
  const list = new FormData(r.el.querySelector("form")).get("v").split("\n");
  try {
    await api("/api/dropdowns", { col: c.k, values: list, enabled: r.value === "ok" });
    await ctx.refreshMeta();
    drawHead();
    render();
    toast(r.value === "ok" ? `“${c.l}” is a dropdown now.` : `“${c.l}” is back to free typing.`);
  } catch (e) { toast(e.message, "bad"); }
}

// Protect a column like "Protect range" in Google Sheets: choose who may edit it (admins only).
async function protectDialog(c) {
  const l = lockOf(c.k) || { mode: "", users: [], note: "" };
  let team = [];
  try { team = (await api("/api/team")).items.filter((u) => !u.disabled && u.role !== "admin"); } catch (e) { return toast(e.message, "bad"); }
  const opt = (v, label, hint) => `<label class="check" style="display:block;margin:8px 0"><input type="radio" name="mode" value="${v}" ${l.mode === v ? "checked" : ""}> <b>${label}</b><div class="hint" style="margin-left:24px">${hint}</div></label>`;
  const body = `<p class="muted">Admins always keep control of the protection. Imports and the scraper still fill the column.</p>
    ${opt("", "Not protected", "Everyone can edit this column.")}
    ${opt("nobody", "Nobody (imports only)", "No one can type in it, not even admins. Use it for data that must only come from an import, like the Michaels download.")}
    ${opt("admins", "Only admins", "Members can see the column but not change it.")}
    ${opt("people", "Admins and chosen people", "Pick the team members who may also edit it.")}
    <div id="whoBox" style="margin:2px 0 8px 24px;${l.mode === "people" ? "" : "display:none"}">${team.length
      ? team.map((u) => `<label class="check" style="display:block"><input type="checkbox" name="u" value="${esc(u.username)}" ${l.users.includes(u.username) ? "checked" : ""}> ${esc(u.name)} <span class="muted mono">${esc(u.username)}</span></label>`).join("")
      : `<span class="muted">There are no members yet. Add team members on the Team tab.</span>`}</div>
    <label class="field"><span>Note shown to people (optional)</span><input name="note" maxlength="200" value="${esc(l.note || "")}" placeholder="e.g. Comes from the Michaels download listing"></label>`;
  setTimeout(() => {
    const dlg = document.querySelector("dialog[open]");
    dlg?.addEventListener("change", () => {
      const mode = dlg.querySelector('input[name="mode"]:checked')?.value;
      dlg.querySelector("#whoBox").style.display = mode === "people" ? "" : "none";
    });
  }, 0);
  const r = await dialog({ title: `Protect “${c.l}”`, body, actions: [["Cancel", "close"], ["Save", "ok", "primary"]] });
  if (r.value !== "ok") return;
  const f = new FormData(r.el.querySelector("form"));
  try {
    await api("/api/locks", { col: c.k, mode: f.get("mode") || "none", users: f.getAll("u"), note: f.get("note") });
    await ctx.refreshMeta();
    drawHead();
    render();
    toast(f.get("mode") ? `“${c.l}” is protected.` : `“${c.l}” is no longer protected.`);
  } catch (e) { toast(e.message, "bad"); }
}

function hideColumns(keys) {
  keys.forEach((k) => S.hidden.add(k));
  store.set("gt_hidden_cols", JSON.stringify([...S.hidden]));
  rebuildCols();
  S.sel = null;
  el.inner.style.width = totalW() + "px";
  drawHead();
  render();
}

async function addColumnDialog(afterKey) {
  const r = await dialog({
    title: "Add a column",
    body: `<label class="field"><span>Column name</span><input name="label" required maxlength="40" autofocus placeholder="e.g. Buyer notes"></label>
      <label class="field"><span>Type</span><select name="type"><option value="text">Text</option><option value="number">Number</option><option value="checkbox">Checkbox (TRUE / FALSE)</option><option value="date">Date</option></select></label>
      <label class="field"><span>Place it after</span><select name="after">${S.all.map((c) => `<option value="${esc(c.k)}" ${c.k === afterKey ? "selected" : ""}>${esc(c.l)}</option>`).join("")}</select></label>
      <p class="hint">The column is added for everyone. You can type, paste, filter and sort in it like any other column.</p>`,
    actions: [["Cancel", "close"], ["Add column", "ok", "primary"]],
  });
  if (r.value !== "ok") return;
  const f = new FormData(r.el.querySelector("form"));
  try {
    const res = await api("/api/sheet/columns/add", { label: f.get("label"), type: f.get("type"), after: f.get("after") });
    await reloadColumns();
    const i = visCols().findIndex((c) => c.k === res.key);
    if (i >= 0) { reveal(0, i); setSel(0, i, 0, i, false); el.scroller.focus(); }
    toast(`Added column “${f.get("label")}”.`);
  } catch (e) { toast(e.message, "bad"); }
}

async function deleteColumn(c) {
  if (!(await confirmBox(`Delete the column “${c.l}”?`, "Its values are removed for every product, permanently. This can't be undone.", "Delete column"))) return;
  try {
    const r = await api("/api/sheet/columns/delete", { key: c.k });
    S.hidden.delete(c.k);
    await reloadColumns();
    toast(`Deleted “${c.l}” (${fmt(r.values_removed)} values removed).`);
  } catch (e) { toast(e.message, "bad"); }
}

async function columnsDialog() {
  const body = `<div class="btnrow">${Object.keys(PRESETS).map((p) => `<button type="button" class="btn small" data-preset="${esc(p)}">${esc(p)}</button>`).join("")}</div>
    <div class="btnrow" style="margin-top:8px"><button type="button" class="btn small" data-colsall="all">Check all</button><button type="button" class="btn small" data-colsall="none" title="Hides every column except the three that always stay">Uncheck all</button>
      <span class="muted" style="font-size:12.5px">The image columns and SKU always stay, so you can tick just the ones you want.</span></div>
    <div class="colgrid">${S.all.map((c) => `<div class="colrow"><label class="check"><input type="checkbox" name="${esc(c.k)}" ${S.hidden.has(c.k) ? "" : "checked"} ${c.pin ? "disabled" : ""}> ${esc(c.l)}${c.custom ? ' <span class="pill s-info">custom</span>' : ""}</label>${c.custom && ctx.isAdmin() ? `<button type="button" class="btn small danger" data-delcol="${esc(c.k)}" title="Delete this column and its values">Delete</button>` : ""}</div>`).join("")}</div>
    <p class="hint">Untick a column to hide it (any column can be shown again here). <b>Custom</b> columns, the ones you added, can also be deleted with their Delete button${ctx.isAdmin() ? "" : " (admins only)"}. The sheet's built-in columns can only be hidden, because pricing and statuses depend on them.</p>`;
  const p = dialog({ title: "Columns", body, wide: true, actions: [["Cancel", "close"], ["Apply", "ok", "primary"]] });
  setTimeout(() => {
    const dlg = document.querySelector("dialog[open]");
    dlg?.addEventListener("click", (e) => {
      const del = e.target.closest("[data-delcol]");
      if (del) {                                          // close this dialog, then ask for confirmation
        const col = S.all.find((c) => c.k === del.dataset.delcol);
        dlg.close("close");
        if (col) setTimeout(() => deleteColumn(col), 0);
        return;
      }
      const all = e.target.closest("[data-colsall]");
      if (all) {
        document.querySelectorAll("dialog[open] .colgrid input").forEach((i) => { if (!i.disabled) i.checked = all.dataset.colsall === "all"; });
        return;
      }
      const b = e.target.closest("[data-preset]");
      if (!b) return;
      const keep = PRESETS[b.dataset.preset];
      document.querySelectorAll("dialog[open] .colgrid input").forEach((i) => { if (!i.disabled) i.checked = !keep || keep.includes(i.name); });
    });
  }, 0);
  const r = await p;
  if (r.value !== "ok") return;
  const f = new FormData(r.el.querySelector("form"));
  S.hidden = new Set(S.all.filter((c) => !c.pin && !f.has(c.k)).map((c) => c.k));
  hideColumns([]);
}




