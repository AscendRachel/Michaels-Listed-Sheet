// Add products: one at a time with live pricing, or pasted from a spreadsheet.
import { $, api, debounce, esc, fmt, money, priceFor, qs, toast } from "./util.js";

const BULK_COLS = ["asin", "name", "category", "link", "weight", "source_price", "variation", "upc", "sku"];
const BULK_LABELS = ["ASIN", "PRODUCT NAME", "CATEGORY", "SOURCE LINK", "WEIGHT", "SOURCE PRICE", "VARIATION", "UPC", "SKU"];

export async function mount(el, params, ctx) {
  const pr = ctx.meta.pricing;
  let mode = "single";
  const draw = () => {
    el.innerHTML = `<div class="section-head"><div><h2>Add products</h2><p>Replaces typing rows into the Listed Sheet. The price is calculated as you type, duplicates are caught before saving, and new products join the next scraper run.</p></div>
      <div class="btnrow"><button class="btn ${mode === "single" ? "primary" : ""}" data-mode="single">One product</button><button class="btn ${mode === "bulk" ? "primary" : ""}" data-mode="bulk">Paste many</button></div></div>
      ${mode === "single" ? single() : bulk()}`;
    mode === "single" ? wireSingle() : wireBulk();
  };

  const single = () => `<div class="two"><form class="card" id="f" autocomplete="off"><div class="formgrid">
      ${[["sku", "SKU *", "text"], ["name", "Product name *", "text"], ["link", "Source link (Amazon) *", "text"], ["source_price", "Source price ($) *", "number"], ["weight", "Weight (oz) *", "number"], ["category", "Category *", "text"], ["upc", "UPC", "text"], ["variation", "Variation", "text"]]
        .map(([k, l, t]) => `<label class="field" ${k === "name" || k === "link" ? 'style="grid-column:1/-1"' : ""}><span>${l}</span><input name="${k}" type="${t}" ${t === "number" ? 'step="any" min="0"' : ""} ${k === "category" ? 'list="cats"' : ""}><div class="err" data-err="${k}"></div></label>`).join("")}
      <label class="field"><span>Listing status</span><select name="listing_status">${ctx.meta.listing_statuses.map((s) => `<option>${s}</option>`).join("")}</select></label></div>
      <datalist id="cats">${ctx.meta.categories.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <div id="dupes"></div><div class="btnrow" style="margin-top:14px"><button class="btn primary">Add product</button><button type="reset" class="btn">Clear</button></div></form>
      <div class="card" id="calc"></div></div>`;

  function wireSingle() {
    const f = $("#f");
    const read = () => Object.fromEntries(new FormData(f));
    const calc = () => {
      const v = read();
      const p = priceFor(v.weight, v.source_price, pr);
      $("#calc").innerHTML = `<h3>Price</h3><dl class="kv" style="margin-top:10px"><dt>Source</dt><dd>${money(v.source_price)}</dd><dt>3PL fee</dt><dd>${p.fee == null ? (v.weight ? "Weight outside the fee table" : "—") : money(p.fee)}</dd>
        <dt>Cost</dt><dd>${money(p.cost)}</dd><dt>× ${pr.markup}</dt><dd>${money(p.selling)}</dd><dt>Automation price</dt><dd style="font-size:20px"><b>${money(p.auto)}</b></dd>
        <dt>Margin</dt><dd>${p.margin == null ? "—" : (p.margin * 100).toFixed(0) + "%"}</dd></dl>`;
    };
    const dupes = debounce(async () => {
      const v = read();
      if (!v.sku && !v.link && !v.upc) return ($("#dupes").innerHTML = "");
      const r = await api("/api/products/dupes?" + qs({ sku: v.sku, link: v.link, upc: v.upc }));
      $("#dupes").innerHTML = r.dupes.length ? `<div class="banner bad" style="margin-top:12px"><b>Possible duplicate:</b> ${r.dupes.map((d) => `${d.field} matches <a href="#catalog?q=${encodeURIComponent(d.sku)}">${esc(d.sku)}</a> (${esc(d.name)})`).join("; ")}</div>` : "";
    }, 350);
    f.addEventListener("input", () => { calc(); dupes(); });
    f.addEventListener("reset", () => setTimeout(() => { calc(); $("#dupes").innerHTML = ""; }));
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      document.querySelectorAll("[data-err]").forEach((x) => (x.textContent = ""));
      const r = await api("/api/products/create", { product: read() });
      if (r.ok) {
        toast(`Added ${r.sku}.`);
        f.reset();
        ctx.refreshCounts();
      } else if (r.errors) {
        for (const [k, msg] of Object.entries(r.errors)) { const n = document.querySelector(`[data-err="${k}"]`); if (n) n.textContent = msg; }
        toast("Fix the highlighted fields.", "bad");
      } else {
        toast("This looks like a duplicate. Check the warning below the form.", "bad");
        $("#dupes").innerHTML = `<div class="banner bad" style="margin-top:12px"><b>Duplicate:</b> ${r.dupes.map((d) => `${d.field} matches ${esc(d.sku)} (${esc(d.name)})`).join("; ")}</div>`;
      }
    });
    calc();
  }

  const bulk = () => `<div class="card"><p class="muted">Copy rows from your sheet with these columns in this order, with or without the header:</p>
      <p class="mono">${BULK_LABELS.join(" · ")}</p><textarea id="paste" rows="8" placeholder="Paste here…"></textarea>
      <div class="btnrow" style="margin-top:10px"><button class="btn primary" id="check">Check rows</button></div></div><div id="bulkOut"></div>`;

  let parsed = [];
  function wireBulk() {
    $("#check").onclick = async () => {
      const lines = $("#paste").value.split(/\r?\n/).filter((l) => l.trim());
      if (lines.length && /^asin\b/i.test(lines[0])) lines.shift();
      parsed = lines.map((l) => Object.fromEntries(l.split("\t").map((v, i) => [BULK_COLS[i], (v || "").trim()]).filter(([k]) => k)));
      if (!parsed.length) return toast("Paste some rows first.", "bad");
      const r = await api("/api/products/preview-batch", { rows: parsed });
      const bad = r.rows.length - r.ok_count;
      $("#bulkOut").innerHTML = `<div class="panel" style="margin-top:16px"><div class="btnrow" style="justify-content:space-between;margin-bottom:10px"><div><b>${fmt(r.ok_count)}</b> ready${bad ? `, <b style="color:var(--crit)">${fmt(bad)}</b> need fixing (they'll be skipped)` : ""}</div>
        <button class="btn primary" id="commit" ${r.ok_count ? "" : "disabled"}>Add ${fmt(r.ok_count)} products</button></div>
        <div class="tablewrap"><table><thead><tr><th>SKU</th><th>Product</th><th class="num">Weight</th><th class="num">Source $</th><th class="num">Auto $</th><th>Check</th></tr></thead><tbody>
        ${parsed.map((p, i) => { const x = r.rows[i]; const msgs = [...Object.values(x.errors), ...x.dupes.map((d) => `${d.field} matches ${d.sku}`)];
          return `<tr><td class="mono">${esc(p.sku)}</td><td class="name"><div class="t">${esc(p.name)}</div></td><td class="num">${esc(p.weight)}</td><td class="num">${esc(p.source_price)}</td><td class="num">${money(x.auto_price)}</td>
            <td>${x.ok ? '<span class="pill s-ok">OK</span>' : `<span class="pill s-crit">${esc(msgs.join(" · "))}</span>`}</td></tr>`; }).join("")}</tbody></table></div></div>`;
      const c = $("#commit");
      if (c) c.onclick = async () => {
        c.disabled = true;
        const done = await api("/api/products/create-batch", { rows: parsed });
        toast(`Added ${done.created} products${done.skipped ? `, skipped ${done.skipped}` : ""}.`);
        $("#paste").value = "";
        $("#bulkOut").innerHTML = "";
        ctx.refreshCounts();
      };
    };
  }

  el.onclick = (e) => {
    const b = e.target.closest("[data-mode]");
    if (b) { mode = b.dataset.mode; draw(); }
  };
  draw();
}
