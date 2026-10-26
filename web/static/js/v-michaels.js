// Michaels sync: upload the Michaels "Download Listing", preview what changes, then apply.
import { $, api, ago, confirmBox, download, esc, fmt, money, toast } from "./util.js";
import { go } from "./main.js";

export async function mount(el, params, ctx) {
  const admin = ctx.isAdmin();
  const hist = (await api("/api/michaels/history")).items;
  await ctx.refreshCounts();
  const c = ctx.counts.counts;
  let preview = null, file = null, clear = true, job = null, watching = false;

  const draw = () => {
    el.innerHTML = `<div class="section-head"><div><h2>Michaels sync</h2><p>Replaces pasting the Download Listing into the Michaels_Data sheet. Products are matched by SKU, then their Michaels SKU number, price and quantity are updated.</p></div>
      <div class="btnrow"><button class="btn" id="fixes" ${c["mz-price"] ? "" : "disabled"}>Export ${fmt(c["mz-price"] || 0)} price fixes (CSV)</button></div></div>
      <div class="qgrid" style="margin-bottom:16px">${["mz-missing", "mz-stock-off", "mz-zero-on", "mz-price"].map((k) => `<button class="qcard ${c[k] ? "warn" : "zero"}" data-queue="${k}"><span class="qn">${fmt(c[k] || 0)}</span><span class="ql">${esc(ctx.meta.queues[k].label)}</span></button>`).join("")}</div>
      ${admin ? `<div class="card"><h3>Upload a download</h3><p class="muted">CSV or Excel. Nothing changes until you review the preview and click Apply.</p>
        <input type="file" id="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm"></div>` : `<div class="banner">Only admins can import a Michaels download.</div>`}
      <div id="job"></div>
      <div id="pv"></div>
      <div class="panel" style="margin-top:16px"><h3 style="margin-bottom:8px">Recent imports</h3>${hist.length ? `<div class="tablewrap"><table style="min-width:0"><thead><tr><th>When</th><th>By</th><th>File</th><th class="num">Updated</th><th class="num">Cleared</th></tr></thead><tbody>${hist.map((h) => `<tr><td>${esc(ago(h.at))}</td><td>${esc(h.user)}</td><td>${esc(h.filename)}</td><td class="num">${fmt(h.summary.updated)}</td><td class="num">${fmt(h.summary.cleared)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">No imports yet.</p>`}</div>`;
    if (preview) drawPreview();
    if (job) showJob(job);
  };

  // The column pickers: one select per field, and a multi-select for the image columns.
  const mapForm = (p) => `<div class="formgrid">
    ${["sku", "sku_number", "price", "qty"].map((k) => `<label class="field"><span>${{ sku: "SKU column", sku_number: "Michaels SKU number (optional)", price: "Price column", qty: "Quantity column" }[k]}</span>
      <select data-map="${k}"><option value="-1">— none —</option>${p.headers.map((h, i) => `<option value="${i}" ${p.mapping[k] === i ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label>`).join("")}
    <label class="field"><span>Image link columns (optional, Ctrl+click for several)</span>
      <select data-map="images" multiple size="4">${p.headers.map((h, i) => `<option value="${i}" ${(p.mapping.images || []).includes(i) ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label></div>`;

  const drawPreview = () => {
    const p = preview;
    if (!p.ok) {
      $("#pv").innerHTML = `<div class="card" style="margin-top:16px"><div class="banner bad">${esc(p.error)}</div>${mapForm(p)}
        <div class="btnrow" style="margin-top:12px"><button class="btn primary" id="remap">Try again with these columns</button></div></div>`;
      return;
    }
    $("#pv").innerHTML = `<div class="card" style="margin-top:16px"><h3>Preview: ${esc(p.filename)}</h3>
      <div class="stats"><div class="stat"><div class="v">${fmt(p.rows)}</div><div class="l">rows in file</div></div><div class="stat"><div class="v">${fmt(p.matched)}</div><div class="l">matched to catalog</div></div>
        <div class="stat"><div class="v">${fmt(p.price_changes)}</div><div class="l">price changes</div></div><div class="stat"><div class="v">${fmt(p.qty_changes)}</div><div class="l">quantity changes</div></div>
        <div class="stat"><div class="v">${fmt(p.not_in_catalog)}</div><div class="l">not in catalog</div></div><div class="stat"><div class="v">${fmt(p.missing_listed)}</div><div class="l">listed but not in file</div></div></div>
      <p>${p.has_images
        ? `🖼 <b>${fmt(p.with_images)}</b> matched products get images (${fmt(p.image_urls)} links from ${p.image_columns.map((c) => `“${esc(c)}”`).join(", ")}).`
        : `🖼 <b>No image columns found</b>, so product images won't change. If the file has image links, pick their columns below.`}</p>
      <details style="margin:6px 0 10px"><summary class="muted" style="cursor:pointer">Columns used (change if something looks wrong)</summary>${mapForm(p)}
        <div class="btnrow" style="margin-top:10px"><button class="btn small" id="remap">Re-check with these columns</button></div></details>
      ${p.rounded_sku_numbers ? `<div class="banner"><b>${fmt(p.rounded_sku_numbers)} SKU numbers in this file are rounded</b> (like 4.36087E+17: Excel dropped the digits), so they were <b>ignored</b> and won't overwrite anything.
        ${fmt(p.rounded_kept)} of those products keep the SKU number they already have${p.rounded_missing ? `; <b>${fmt(p.rounded_missing)}</b> have none yet and stay blank until you re-export the file with the SKU number column formatted as Text` : ""}.</div>` : ""}
      ${p.changes.length ? `<div class="tablewrap" style="max-height:260px"><table><thead><tr><th>SKU</th><th>Product</th><th class="num">Price</th><th class="num">Qty</th></tr></thead><tbody>${p.changes.map((x) => `<tr><td class="mono">${esc(x.sku)}</td><td class="name"><div class="t">${esc(x.name)}</div></td><td class="num">${money(x.old_price)} → <b>${money(x.new_price)}</b></td><td class="num">${x.old_qty ?? "—"} → <b>${x.new_qty ?? "—"}</b></td></tr>`).join("")}</tbody></table></div><p class="hint">Showing the first ${p.changes.length} changes.</p>` : ""}
      <label class="check" style="margin-top:10px"><input type="checkbox" id="clear" ${clear ? "checked" : ""}> Clear Michaels price/qty for the <b>${fmt(p.will_clear)}</b> products that have values but aren't in this file</label>
      <div class="btnrow end"><button class="btn" id="cancel">Cancel</button><button class="btn primary" id="apply">Apply to ${fmt(p.matched)} products</button></div></div>`;
  };

  // Reading and applying a big download takes a while, so both run on the server as a background job; this only shows
  // how it is going (the same progress bar as the Listed Sheet import) and survives leaving the page.
  const busy = (on) => { el.querySelectorAll("#file, #apply, #remap").forEach((x) => (x.disabled = on)); };
  function showJob(j) {
    const box = $("#job");
    if (!box) return;
    busy(j.state === "running");
    if (j.state === "running") {
      const pct = j.total ? Math.min(100, Math.round((j.done / j.total) * 100)) : 0;
      box.innerHTML = `<div class="banner" style="margin-top:16px"><b>${j.kind === "apply" ? "Applying" : "Reading"} ${esc(j.filename || "")}…</b> ${esc(j.stage || "")}${j.total ? ` (${fmt(j.done)} of ${fmt(j.total)})` : j.done ? ` (${fmt(j.done)} rows so far)` : ""}
        <div class="progress" style="margin:8px 0 2px" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
        <div class="hint">Started by ${esc(j.user || "?")} ${esc(ago(j.started_at))}. You can leave this page; it keeps going.</div></div>`;
    } else if (j.state === "error") {
      box.innerHTML = `<div class="banner bad" style="margin-top:16px"><b>${j.kind === "apply" ? "The download was not applied." : "The file could not be read."}</b> ${esc(j.error || "")}<div class="hint">${esc(j.filename || "")}</div></div>`;
    } else box.innerHTML = "";
  }
  async function watch() {
    if (watching) return;
    watching = true;
    try {
      for (let i = 0; i < 3600 && el.isConnected && $("#job"); i++) {                    // up to about an hour
        let j;
        try { j = await api("/api/michaels/job"); } catch { await new Promise((r) => setTimeout(r, 3000)); continue; }   // a dropped connection is not the end of the job
        job = j;
        showJob(j);
        if (j.state !== "running") {
          if (j.state === "done" && j.kind === "preview") { preview = j.result; drawPreview(); }
          if (j.state === "done" && j.kind === "apply") {
            toast(`Updated ${fmt(j.result.updated)} products.`);
            preview = null; job = null;
            await mount(el, params, ctx);
          }
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } finally { watching = false; }
  }
  async function upload(mapping) {
    const buf = await file.arrayBuffer();
    const headers = { "X-Filename": encodeURIComponent(file.name) };
    if (mapping) headers["X-Mapping"] = encodeURIComponent(JSON.stringify(mapping));
    try {
      job = await api("/api/michaels/preview/start", buf, { headers });
      showJob(job);
      watch();
    } catch (e) { toast(e.message, "bad"); }
  }

  el.onchange = (e) => {
    if (e.target.id === "file" && e.target.files[0]) { file = e.target.files[0]; upload(); }
    if (e.target.id === "clear") clear = e.target.checked;
  };
  el.onclick = async (e) => {
    const t = e.target;
    const q = t.closest("[data-queue]");
    if (q) return go("catalog", { queue: q.dataset.queue });
    if (t.closest("#fixes")) return download("/api/export/michaels-fixes.csv");
    if (t.closest("#cancel")) { preview = null; $("#pv").innerHTML = ""; }
    if (t.closest("#remap")) {
      const m = {};
      el.querySelectorAll("[data-map]").forEach((s) => (m[s.dataset.map] = s.multiple ? [...s.selectedOptions].map((o) => +o.value) : s.value));
      upload(m);
    }
    if (t.closest("#apply")) {
      if (!(await confirmBox("Apply this download?", `${fmt(preview.matched)} products get new Michaels price and quantity.${clear && preview.will_clear ? ` ${fmt(preview.will_clear)} products not in the file will have theirs cleared.` : ""}`, "Apply"))) return;
      try {
        job = await api("/api/michaels/apply/start", { token: preview.token, clear_missing: clear });
        showJob(job);
        watch();
      } catch (err) { toast(err.message, "bad"); }
    }
  };
  draw();
  try {                                                         // a job that is still running (or a preview waiting for Apply) after leaving the page
    const j = await api("/api/michaels/job");
    if (j.state === "running") { job = j; showJob(j); watch(); }
    else if (j.state === "done" && j.kind === "preview") { preview = j.result; drawPreview(); }
  } catch { /* not an admin screen */ }
}
