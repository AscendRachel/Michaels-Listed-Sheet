// Version history: saved copies of the catalog, what changed since each one, and putting products back (like Google Sheets).
import { $, api, confirmBox, debounce, dialog, esc, fmt, qs, toast } from "./util.js";

const KINDS = [["", "Everything that differs"], ["changed", "Edited products"], ["deleted", "Deleted products"], ["added", "Products added since"]];
const kb = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

export async function mount(el, params, ctx) {
  const admin = ctx.isAdmin();
  const S = { versions: [], vid: null, q: "", kind: "", offset: 0, items: [], counts: null, total: 0, picked: new Set() };

  const title = (v) => v.name || (v.kind === "restore" ? "Before a restore" : "Saved automatically");
  const tag = (v) => (v.kind === "named" ? `<span class="pill s-ok">named</span>` : v.kind === "restore" ? `<span class="pill s-warn">undo point</span>` : "");

  async function loadList(keep) {
    S.versions = (await api("/api/versions")).items;
    if (!keep || !S.versions.some((v) => v.id === S.vid)) S.vid = S.versions[0]?.id ?? null;
    drawList();
    await loadChanges(true);
  }

  async function loadChanges(reset) {
    if (reset) { S.offset = 0; S.items = []; S.picked.clear(); }
    if (S.vid == null) { S.counts = null; return drawDetail(); }
    try {
      const r = await api(`/api/versions/${S.vid}/changes?` + qs({ q: S.q, kind: S.kind, offset: S.offset }));
      S.counts = r.counts; S.total = r.total; S.items = S.items.concat(r.items); S.offset = S.items.length;
    } catch (e) { S.counts = null; S.items = []; toast(e.message, "bad"); }
    drawDetail();
  }

  function drawList() {
    $("#vlist").innerHTML = S.versions.length
      ? S.versions.map((v) => `<button class="vitem ${v.id === S.vid ? "on" : ""}" data-v="${v.id}">
          <b>${esc(title(v))}</b> ${tag(v)}<br><span class="muted">${esc(v.at)}${v.by ? " · " + esc(v.by) : ""}</span><br>
          <span class="muted">${fmt(v.rows)} products · ${v.edits_since ? fmt(v.edits_since) + " edit" + (v.edits_since === 1 ? "" : "s") + " since" : "no edits since"} · ${kb(v.size)}</span></button>`).join("")
      : `<div class="empty">No versions yet. The first one is saved automatically after the next edit, or save one now.</div>`;
  }

  function drawDetail() {
    const v = S.versions.find((x) => x.id === S.vid);
    const box = $("#vdetail");
    if (!v) { box.innerHTML = `<div class="empty">Pick a version on the left to see what changed since then.</div>`; return; }
    const c = S.counts;
    box.innerHTML = `<div class="toolbar" style="gap:8px;flex-wrap:wrap">
        <div class="grow"><h3 style="margin:0">${esc(title(v))}</h3><span class="muted">${esc(v.at)}${v.by ? " · saved by " + esc(v.by) : ""}</span></div>
        <button class="btn small" data-act="name">${v.kind === "named" ? "Rename" : "Name this version"}</button>
        ${admin ? `<button class="btn small" data-act="delete">Delete</button>` : ""}
      </div>
      ${c ? `<p style="margin:10px 0">Compared with the catalog now: <b>${fmt(c.changed)}</b> edited product${c.changed === 1 ? "" : "s"} (${fmt(c.cells)} cells),
        <b>${fmt(c.deleted)}</b> deleted, <b>${fmt(c.added)}</b> added since. Restoring puts back what people typed; prices and availability read by the scraper stay as they are.</p>
        <div class="toolbar"><input class="grow" id="vq" type="search" placeholder="Search SKU or name" value="${esc(S.q)}">
          <select id="vkind">${KINDS.map(([k, l]) => `<option value="${k}" ${S.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="btnrow" style="margin:8px 0;gap:8px">
          <button class="btn small" data-act="all">Tick all shown</button><button class="btn small" data-act="none">Clear ticks</button>
          <button class="btn small primary" data-act="restore" ${S.picked.size ? "" : "disabled"}>Restore ticked (${fmt(S.picked.size)})</button>
          ${admin ? `<button class="btn small" data-act="everything" ${c.changed + c.deleted + c.added ? "" : "disabled"}>Restore everything…</button>` : ""}</div>
        ${S.items.length ? `<div class="tablewrap"><table><thead><tr><th></th><th>SKU</th><th>Product</th><th>What differs from then</th></tr></thead><tbody>${S.items.map(row).join("")}</tbody></table></div>
          ${S.items.length < S.total ? `<div style="padding:10px"><button class="btn small" data-act="more">Show more (${fmt(S.total - S.items.length)} left)</button></div>` : ""}`
          : `<div class="empty">${c.changed + c.deleted + c.added ? "Nothing matches." : "The catalog has no differences from this version."}</div>`}`
        : `<div class="empty">That version couldn't be read.</div>`}`;
  }

  function row(i) {
    const what = i.kind === "deleted" ? `<span class="pill s-crit">deleted</span> comes back with its typed values`
      : i.kind === "added" ? `<span class="pill s-warn">added since</span> restoring it removes the product (it can be restored from Activity)`
      : i.cells.map((x) => `<div><span class="muted">${esc(x.label)}:</span> <s class="muted">${esc(String(x.now) || "—")}</s> → <b>${esc(String(x.then) || "—")}</b></div>`).join("")
        + (i.cells_total > i.cells.length ? `<div class="muted">and ${fmt(i.cells_total - i.cells.length)} more</div>` : "");
    return `<tr><td><input type="checkbox" data-pick="${esc(i.sku)}" ${S.picked.has(i.sku) ? "checked" : ""} aria-label="Restore ${esc(i.sku)}"></td>
      <td class="mono">${i.kind === "deleted" ? esc(i.sku) : `<a href="#catalog?q=${encodeURIComponent(i.sku)}">${esc(i.sku)}</a>`}</td><td>${esc(i.name)}</td><td>${what}</td></tr>`;
  }

  async function doRestore(body, what) {
    const v = S.versions.find((x) => x.id === S.vid);
    try {
      const r = await api(`/api/versions/${S.vid}/restore`, body);
      const bits = [r.products && `${fmt(r.products)} product${r.products === 1 ? "" : "s"} put back (${fmt(r.cells)} cells)`, r.brought_back && `${fmt(r.brought_back)} brought back`, r.removed && `${fmt(r.removed)} removed`].filter(Boolean);
      toast(bits.length ? `Restored ${what}: ${bits.join(", ")}.${r.protected ? ` ${fmt(r.protected)} cell${r.protected === 1 ? "" : "s"} skipped (protected column).` : ""} An undo point was saved.` : "Nothing needed restoring.");
    } catch (e) { return toast(e.message, "bad"); }
    ctx.refreshCounts?.();
    await loadList(true);
  }

  el.innerHTML = `<style>
      .vwrap{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}.vside{flex:0 0 310px;max-width:100%}.vmain{flex:1 1 520px;min-width:0}
      #vlist{display:flex;flex-direction:column;gap:6px;max-height:70vh;overflow:auto}
      .vitem{text-align:left;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);cursor:pointer;font:inherit}
      .vitem.on{border-color:var(--brand);box-shadow:0 0 0 1px var(--brand) inset}
    </style>
    <div class="section-head"><div><h2>Version history</h2><p>The catalog is saved automatically after edits, and before every import. Pick a version, see what changed since, then put back the products you need. Restoring saves an undo point first.</p></div>
      <button class="btn primary" id="saveNow">Save a named version</button></div>
    <div class="vwrap"><div class="vside panel"><div id="vlist"></div><p class="muted" style="margin:8px 4px 2px">Automatic versions are kept for 2 days, then one per day for 30 days. Named versions are kept until an admin deletes them.</p></div>
      <div class="vmain panel" id="vdetail"></div></div>`;

  el.onclick = async (e) => {
    const vb = e.target.closest("[data-v]");
    if (vb) { S.vid = +vb.dataset.v; S.q = ""; S.kind = ""; drawList(); return loadChanges(true); }
    if (e.target.id === "saveNow") {
      const r = await dialog({ title: "Save a named version", body: `<label class="field"><span>Name</span><input name="n" maxlength="120" placeholder="e.g. Before the price review" required></label><p class="muted">Saves the catalog exactly as it is now. Named versions are kept until an admin deletes them.</p>`, actions: [["Cancel", "close"], ["Save", "ok", "primary"]] });
      if (r.value !== "ok") return;
      try { const j = await api("/api/versions", { name: new FormData(r.el.querySelector("form")).get("n") }); S.vid = j.id; toast("Version saved."); await loadList(true); } catch (err) { toast(err.message, "bad"); }
      return;
    }
    const pick = e.target.closest("[data-pick]");
    if (pick) { pick.checked ? S.picked.add(pick.dataset.pick) : S.picked.delete(pick.dataset.pick); const b = $('[data-act="restore"]', el); b.disabled = !S.picked.size; b.textContent = `Restore ticked (${fmt(S.picked.size)})`; return; }
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    const v = S.versions.find((x) => x.id === S.vid);
    if (act === "all") { S.items.forEach((i) => S.picked.add(i.sku)); drawDetail(); }
    else if (act === "none") { S.picked.clear(); drawDetail(); }
    else if (act === "more") await loadChanges(false);
    else if (act === "restore") {
      if (await confirmBox(`Restore ${fmt(S.picked.size)} product${S.picked.size === 1 ? "" : "s"}?`, `They go back to how they were in “${title(v)}” (${v.at}). The catalog is saved first, so this can be undone from the list.`, "Restore")) await doRestore({ skus: [...S.picked] }, "the ticked products");
    } else if (act === "everything") {
      const c = S.counts;
      const r = await dialog({ title: "Restore everything?", body: `<p>Every product goes back to how it was in “${esc(title(v))}” (${esc(v.at)}): ${fmt(c.changed)} edited, ${fmt(c.deleted)} deleted${c.deleted === 1 ? "" : " ones"} brought back.</p>
        <label class="check" style="display:flex;gap:8px"><input type="checkbox" name="rm"> Also remove the ${fmt(c.added)} product${c.added === 1 ? "" : "s"} added since</label>
        <p class="muted">Everything done since will be lost for those products. An undo point is saved first.</p>`, actions: [["Cancel", "close"], ["Restore everything", "ok", "primary"]] });
      if (r.value === "ok") await doRestore({ everything: true, remove_added: !!r.el.querySelector('input[name="rm"]').checked }, "everything");
    } else if (act === "name") {
      const r = await dialog({ title: v.kind === "named" ? "Rename this version" : "Name this version", body: `<label class="field"><span>Name</span><input name="n" maxlength="120" value="${esc(v.name)}" placeholder="Leave empty to un-name it"></label><p class="muted">A named version is kept until an admin deletes it.</p>`, actions: [["Cancel", "close"], ["Save", "ok", "primary"]] });
      if (r.value !== "ok") return;
      try { await api(`/api/versions/${v.id}/name`, { name: new FormData(r.el.querySelector("form")).get("n") }); await loadList(true); } catch (err) { toast(err.message, "bad"); }
    } else if (act === "delete") {
      if (!(await confirmBox("Delete this version?", `“${title(v)}” (${v.at}) is removed for good.`, "Delete"))) return;
      try { await api(`/api/versions/${v.id}/delete`, {}); await loadList(false); } catch (err) { toast(err.message, "bad"); }
    }
  };
  const search = debounce(async (val) => {
    S.q = val;
    await loadChanges(true);
    const box = $("#vq");                                   // the table is redrawn: keep typing where it was
    if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
  });
  el.oninput = (e) => { if (e.target.id === "vq") search(e.target.value); };
  el.onchange = (e) => { if (e.target.id === "vkind") { S.kind = e.target.value; loadChanges(true); } };
  await loadList(false);
}
