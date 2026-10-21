// Product drawer: pricing breakdown, latest Amazon check, edit form, check history and change log.
import { $, ago, api, esc, fmt, money, num, pill, toast } from "./util.js";
import { amazonSized, openCarousel } from "./carousel.js";

const FIELDS = [
  ["name", "Product name", "text"], ["category", "Category", "text"], ["variation", "Variation", "text"], ["upc", "UPC", "text"],
  ["source_price", "Source price ($)", "number"], ["weight", "Weight (oz)", "number"], ["link", "Source link", "text"], ["source", "Source", "text"],
];

// What the title check concluded, in words a person can act on.
function titleVerdict(p, threshold) {
  if (!p.title || p.title_score == null) return "";
  const accepted = p.title_ok === p.title;
  const note = p.title_note ? ` ${esc(p.title_note)}` : "";
  if (p.title_conflict) return `<div class="banner ${accepted ? "" : "bad"}" style="margin:6px 0 0"><b>${accepted ? "Accepted by the team." : "Listing changed."}</b>${note}</div>`;
  if (p.title_score < Number(threshold)) return `<div class="banner ${accepted ? "" : ""}" style="margin:6px 0 0"><b>${accepted ? "Accepted by the team." : `Low match (${p.title_score}%).`}</b>${note}</div>`;
  return `<div class="hint">Matches the catalog (${p.title_score}%).${note}</div>`;
}

export function closeDrawer() {
  document.querySelectorAll(".drawer, .scrim").forEach((n) => n.remove());
}

// onChanged(sku) is called after something was saved so the caller can refresh its rows.
export async function openDrawer(sku, ctx, onChanged = () => {}, scrollTop = 0) {
  closeDrawer();
  let d;
  try {
    d = await api("/api/products/" + encodeURIComponent(sku));
  } catch (e) {
    return toast(e.message, "bad");
  }
  const p = d.product;
  const m = ctx.meta;
  const b = p.breakdown;
  const prices = d.history.map((h) => num(String(h.price).replace(/[^0-9.]/g, ""))).filter((x) => !Number.isNaN(x)).reverse();
  const max = Math.max(...prices, 1);
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  const dr = document.createElement("aside");
  dr.className = "drawer";
  dr.setAttribute("role", "dialog");
  dr.setAttribute("aria-label", "Product " + p.sku);
  dr.innerHTML = `<div class="btnrow" style="justify-content:space-between"><span class="mono muted">${esc(p.sku)}${p.asin ? " · " + esc(p.asin) : ""}</span><button class="btn small" id="dClose">Close ✕</button></div>
    <h2>${esc(p.name)}</h2><div>${pill(p.status)} <span class="muted">${esc(p.why)}</span></div>
    ${p.images?.length || p.amazon_image ? `<div class="thumbs" aria-label="Marketplace images">${(p.images || []).slice(0, 8).map((u, n) => `<button type="button" data-img="${n}" title="Michaels image ${n + 1} of ${p.images.length}"><img src="${esc(u)}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>`).join("")}
      ${p.amazon_image ? `<button type="button" data-amz="1" title="Amazon main image: click to compare with Michaels" style="border-color:var(--brand)"><img src="${esc(amazonSized(p.amazon_image, 160))}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>` : ""}</div>` : ""}
    ${p.link ? `<p style="margin-top:8px"><a href="${esc(p.link)}" target="_blank" rel="noopener noreferrer">Open on Amazon ↗</a></p>` : ""}
    <section><h3>Pricing</h3><dl class="kv">
      <dt>Source price</dt><dd>${money(p.source_price)}</dd><dt>3PL fee (+$${m.pricing.fee_buffer})</dt><dd>${money(b.fee)}</dd>
      <dt>Cost</dt><dd>${money(b.cost)}</dd><dt>After ${m.pricing.markup}× markup</dt><dd>${money(b.selling)}</dd>
      <dt>Automation price</dt><dd><b>${money(b.auto)}</b>${b.margin != null ? ` <span class="muted">(${(b.margin * 100).toFixed(0)}% margin)</span>` : ""}</dd>
      <dt>Michaels SKU number</dt><dd class="mono">${p.michaels_sku_number ? esc(p.michaels_sku_number) : "—"}</dd>
      <dt>Michaels price</dt><dd>${money(p.michaels_price)}</dd><dt>Michaels quantity</dt><dd>${p.michaels_qty != null ? fmt(p.michaels_qty) : "—"}</dd>
      ${p.michaels_updated_at ? `<dt>Michaels updated</dt><dd>${esc(p.michaels_updated_at)}</dd>` : ""}</dl></section>
    <section><h3>Latest Amazon check</h3><dl class="kv"><dt>Price</dt><dd>${esc(p.price || "—")}${p.price_change_pct != null ? ` <span class="${p.price_change_pct > 0 ? "delta-up" : "delta-down"}">(${p.price_change_pct > 0 ? "+" : ""}${p.price_change_pct}% vs previous)</span>` : ""}</dd>
      <dt>Availability</dt><dd>${esc(p.availability || "—")}</dd><dt>Delivery</dt><dd>${esc(p.delivery || "—")}</dd>
      <dt>Checked</dt><dd>${esc(p.run_at || "never")}${p.checked_by ? " by " + esc(p.checked_by) : ""}</dd>
      <dt>Amazon title</dt><dd>${esc(p.title || "—")}${titleVerdict(p, m.settings.title_threshold)}</dd></dl>
      ${prices.length > 1 ? `<div class="price-tape" title="Recent Amazon prices (oldest → newest)">${prices.map((v) => `<i style="height:${Math.max(6, (v / max) * 100)}%" title="$${v.toFixed(2)}"></i>`).join("")}</div>` : ""}</section>
    <section><h3>Edit</h3><form id="editForm"><div class="formgrid">
      ${FIELDS.map(([k, l, t]) => `<label class="field"><span>${l}</span><input name="${k}" type="${t}" ${t === "number" ? 'step="any" min="0"' : ""} value="${esc(p[k] ?? "")}" list="${k === "category" ? "dcats" : ""}"></label>`).join("")}
      <label class="field"><span>Listing status</span><select name="listing_status"><option value=""></option>${m.listing_statuses.map((k) => `<option ${p.listing_status === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      <label class="field"><span>Status counter</span><select name="manual_status"><option value=""></option>${m.manual_statuses.map((k) => `<option ${p.manual_status === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
      <label class="field" style="grid-column:1/-1"><span>Optimizer remarks</span><input name="remarks" value="${esc(p.remarks || "")}"></label></div>
      <datalist id="dcats">${m.categories.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <div class="btnrow" style="margin:10px 0"><label class="check"><input type="checkbox" name="inventory" ${p.inventory ? "checked" : ""}> Inventory on</label>
        <label class="check"><input type="checkbox" name="listed" ${p.listed ? "checked" : ""}> Listed</label></div>
      <div class="btnrow"><button class="btn primary" id="dSave">Save changes</button>
        ${p.price_num ? `<button type="button" class="btn" id="dUseAmazon" title="Copy the Amazon price into Source price">Use Amazon price as source price</button>` : ""}
        ${p.title && p.title_ok !== p.title && p.title_score != null ? `<button type="button" class="btn" id="dTitleOk">Title is fine</button>` : ""}</div></form></section>
    <section><h3>Check history</h3>${d.history.length ? `<ul class="timeline">${d.history.slice(0, 12).map((h) => `<li>${pill(h.status)} <b>${esc(h.price || "—")}</b> · ${esc(h.availability || "")}<div class="muted">${esc(h.run_at || h.at)} · ${esc(h.agent || "")}</div></li>`).join("")}</ul>` : `<p class="muted">No checks yet.</p>`}</section>
    <section><h3>Changes</h3>${d.activity.length ? `<ul class="timeline">${d.activity.map((a) => `<li><b>${esc(a.user)}</b> ${esc(a.action)}<div class="muted">${esc(a.detail)} · ${esc(ago(a.at))}</div></li>`).join("")}</ul>` : `<p class="muted">No edits yet.</p>`}</section>`;
  const close = () => {
    closeDrawer();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => e.key === "Escape" && !document.querySelector("dialog[open]") && close();
  document.addEventListener("keydown", onKey);
  scrim.onclick = close;
  const refresh = async (msg) => {
    toast(msg);
    await onChanged(sku);
    await ctx.refreshCounts();
    openDrawer(sku, ctx, onChanged, dr.scrollTop);
  };
  dr.addEventListener("click", async (e) => {
    try {
      if (e.target.closest("#dClose")) return close();
      const th = e.target.closest("[data-img], [data-amz]");
      if (th) return openCarousel(p, th.dataset.img ? +th.dataset.img : 0);
      if (e.target.closest("#dTitleOk")) { await api("/api/products/bulk", { skus: [sku], action: "accept_title" }); refresh("Title accepted."); }
      if (e.target.closest("#dUseAmazon")) { await api("/api/sheet/use-amazon-price", { skus: [sku] }); refresh("Source price updated from Amazon."); }
    } catch (err) { toast(err.message, "bad"); }
  });
  dr.querySelector("#editForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const patch = {};
    for (const k of [...FIELDS.map((x) => x[0]), "listing_status", "manual_status", "remarks"]) patch[k] = f.get(k);
    patch.inventory = f.has("inventory");
    patch.listed = f.has("listed");
    try {
      const r = await api("/api/products/" + encodeURIComponent(sku), { patch });
      refresh(r.changed ? `Saved ${r.changed} change${r.changed === 1 ? "" : "s"}.` : "Nothing changed.");
    } catch (err) { toast(err.message, "bad"); }
  });
  document.body.append(scrim, dr);
  dr.scrollTop = scrollTop;
  dr.querySelector("#dClose").focus();
}
