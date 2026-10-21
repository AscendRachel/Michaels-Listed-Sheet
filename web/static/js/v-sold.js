// Sold counts: PRODUCT SOLD 2025 / 2026 read from the sold-tracker Google Sheets (the sheet's IMPORTRANGE + COUNTIF).
import { $, api, ago, esc, fmt, toast } from "./util.js";

const YEARS = ["2025", "2026"];

export async function mount(el) {
  let info = await api("/api/sold");

  const statusBlock = () => {
    const s = info.status;
    if (!s.at) return `<p class="muted">Not synced yet.</p>`;
    return `<p>Last sync <b>${esc(ago(s.at))}</b> by ${esc(s.by)} (${esc(s.at)}).</p>
      <div class="tablewrap"><table style="min-width:0"><thead><tr><th>Year</th><th class="num">Tracker rows read</th><th class="num">Products with sales</th><th class="num">Sales counted</th><th class="num">Products changed</th><th>Source</th></tr></thead><tbody>
      ${YEARS.filter((y) => s.years?.[y]).map((y) => {
        const r = s.years[y];
        return r.error ? `<tr><td>${y}</td><td colspan="5"><span class="pill s-crit">${esc(r.error)}</span></td></tr>`
          : `<tr><td>${y}</td><td class="num">${fmt(r.rows)}</td><td class="num">${fmt(r.matched)}</td><td class="num">${fmt(r.sales)}</td><td class="num">${fmt(r.changed)}</td><td>${esc(r.source)}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  };

  const draw = () => {
    const c = info.config;
    el.innerHTML = `<div class="section-head"><div><h2>Sold counts</h2><p>Replaces the IMPORTRANGE + COUNTIF formulas in PRODUCT SOLD 2025 and 2026. For each product the app counts how many cells in the tracker equal its SKU, then TOTAL PRODUCT SOLD adds the two years.</p></div></div>
      <div class="card"><h3>Tracker sheets</h3>
        <p class="muted">Paste the Google Sheets link of each tracker. Each sheet must be shared as <b>Anyone with the link can view</b> (Share → General access) so the app can read it without signing in to Google. Link to the tab you want (the address ends in <span class="mono">gid=…</span>).</p>
        ${YEARS.map((y) => `<div class="formgrid" style="margin:12px 0;grid-template-columns:90px 1fr 140px"><div style="padding-top:26px"><b>${y}</b></div>
          <label class="field"><span>Sheet link</span><input data-url="${y}" value="${esc(c[y].url)}" placeholder="https://docs.google.com/spreadsheets/d/…/edit?gid=…"></label>
          <label class="field"><span>Range</span><input data-range="${y}" value="${esc(c[y].range)}" placeholder="A4:L"></label></div>`).join("")}
        <div class="btnrow" style="margin-top:8px;gap:14px;align-items:center">
          <label class="check"><input type="checkbox" id="auto" ${c.auto ? "checked" : ""}> Sync automatically every</label>
          <select id="hours" aria-label="Hours between syncs">${[1, 3, 6, 12, 24].map((h) => `<option value="${h}" ${c.every_hours === h ? "selected" : ""}>${h} hour${h === 1 ? "" : "s"}</option>`).join("")}</select>
          <span class="muted">(only while this PC and the app are running)</span></div>
        <div class="btnrow" style="margin-top:14px"><button class="btn primary" id="syncNow">Save &amp; sync now</button><button class="btn" id="save">Save only</button></div></div>
      <div class="panel" style="margin-top:16px"><h3 style="margin-bottom:8px">Last sync</h3><div id="status">${""}</div></div>
      <div class="card" style="margin-top:16px"><h3>Can't share the sheet by link?</h3>
        <p class="muted">Download the tracker tab (File → Download → CSV or Excel) and upload it here. The same range and counting rules are used.</p>
        <div class="btnrow">${YEARS.map((y) => `<label class="btn small">${y} file… <input type="file" data-file="${y}" accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden></label>`).join("")}</div></div>`;
    $("#status").innerHTML = statusBlock();
  };

  const read = () => ({
    ...Object.fromEntries(YEARS.map((y) => [y, { url: el.querySelector(`[data-url="${y}"]`).value, range: el.querySelector(`[data-range="${y}"]`).value }])),
    auto: $("#auto").checked, every_hours: +$("#hours").value,
  });
  const run = async (fn, busyMsg) => {
    toast(busyMsg);
    try { await fn(); } catch (e) { toast(e.message, "bad"); }
  };
  const report = (s) => {
    info.status = s;
    $("#status").innerHTML = statusBlock();
    const errs = YEARS.filter((y) => s.years?.[y]?.error);
    toast(errs.length ? `${errs.length} of ${Object.keys(s.years).length} sheets had a problem. See Last sync below.` : "Sold counts updated.", errs.length ? "bad" : "");
  };

  el.onclick = async (e) => {
    if (e.target.closest("#save")) return run(async () => { info.config = (await api("/api/sold/config", read())).config; toast("Saved."); }, "Saving…");
    if (e.target.closest("#syncNow")) return run(async () => {
      info.config = (await api("/api/sold/config", read())).config;
      report((await api("/api/sold/sync", {})).status);
    }, "Reading the tracker sheets…");
  };
  el.onchange = async (e) => {
    const f = e.target.dataset.file && e.target.files[0];
    if (!f) return;
    run(async () => {
      const r = await api("/api/sold/upload", await f.arrayBuffer(), { headers: { "X-Filename": encodeURIComponent(f.name), "X-Year": e.target.dataset.file } });
      report(r.status);
      e.target.value = "";
    }, "Reading the file…");
  };
  draw();
}
