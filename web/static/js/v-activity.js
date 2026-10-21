// Activity: every change, who made it and when (like "Show edit history" in Google Sheets).
import { $, api, debounce, esc, qs, toast } from "./util.js";

const KINDS = [["", "Everything"], ["rows", "Rows deleted / restored"], ["columns", "Columns added / deleted"], ["added", "New products"], ["edits", "Cell edits"]];

export async function mount(el, params, ctx) {
  const f = { q: "", user: "", kind: "" };
  const load = async () => {
    const r = await api("/api/activity?" + qs(f));
    $("#users").innerHTML = `<option value="">Everyone</option>${r.users.map((u) => `<option ${f.user === u ? "selected" : ""}>${esc(u)}</option>`).join("")}`;
    $("#rows").innerHTML = r.items.length
      ? r.items.map((a) => {
        const gone = a.action === "deleted", restored = a.action === "restored", col = a.action === "deleted a column";
        const tag = gone || col ? `<span class="pill s-crit">${esc(a.action)}</span>` : restored ? `<span class="pill s-ok">restored</span>` : esc(a.action);
        const sku = a.sku ? (gone ? `<span class="mono">${esc(a.sku)}</span>` : `<a class="mono" href="#catalog?q=${encodeURIComponent(a.sku)}">${esc(a.sku)}</a>`) : "";
        return `<tr><td style="white-space:nowrap">${esc(a.at)}</td><td><b>${esc(a.user)}</b></td><td>${tag}</td><td>${sku}</td><td>${esc(a.detail)}${gone ? ` <button class="btn small" data-restore="${esc(a.sku)}">Restore</button>` : ""}</td></tr>`;
      }).join("")
      : `<tr><td colspan="5" class="empty">Nothing yet.</td></tr>`;
  };
  el.innerHTML = `<div class="section-head"><div><h2>Activity</h2><p>Every change made in the app, newest first (latest 300): who did it, when, and what changed. Use the filter to see who deleted rows or columns.</p></div></div>
    <div class="panel"><div class="toolbar"><input class="grow" id="q" type="search" placeholder="Search SKU, action or detail"><select id="kind" aria-label="What happened">${KINDS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select><select id="users"></select></div>
    <div class="tablewrap"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>SKU</th><th>Detail</th></tr></thead><tbody id="rows"></tbody></table></div></div>`;
  el.oninput = debounce((e) => { if (e.target.id === "q") { f.q = e.target.value; load(); } });
  el.onchange = (e) => {
    if (e.target.id === "users") { f.user = e.target.value; load(); }
    if (e.target.id === "kind") { f.kind = e.target.value; load(); }
  };
  el.onclick = async (e) => {
    const b = e.target.closest("[data-restore]");
    if (!b) return;
    try {
      const r = await api("/api/products/restore", { skus: [b.dataset.restore] });
      toast(r.restored ? `Restored ${b.dataset.restore}.` : `${b.dataset.restore} couldn't be restored (already back, or the SKU is in use again).`, r.restored ? "" : "bad");
      ctx.refreshCounts?.();
      load();
    } catch (err) { toast(err.message, "bad"); }
  };
  await load();
}
