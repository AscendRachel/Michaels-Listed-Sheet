// Scraper: run progress, scraper PCs, import/export, settings, key and backup.
import { $, api, ago, confirmBox, download, esc, fmt, toast } from "./util.js";

export async function mount(el, params, ctx) {
  const admin = ctx.isAdmin();
  let timer;
  let editing = false;     // don't redraw while someone is typing in settings

  const draw = async () => {
    if (!el.isConnected || !$("#scraperRoot")) return clearInterval(timer);
    if (editing) return;
    const d = await api("/api/scraper");
    $("#live").innerHTML = live(d);
    if (!$("#settings").dataset.ready) { $("#settings").innerHTML = settings(d); $("#settings").dataset.ready = "1"; }
    $("#importInfo").innerHTML = importInfo(d);
  };

  const live = (d) => {
    const r = d.run;
    const done = r ? r.done + r.failed : 0;
    const pctDone = r && r.total ? Math.round((done / r.total) * 100) : 0;
    const head = !r ? `<p class="muted">No run yet. Start the scraper on a scraper PC and it creates one.</p>` : `
      <div class="btnrow" style="justify-content:space-between"><h3>Run #${r.id} · ${esc(r.state)}</h3>${admin && r.state === "open" ? `<button class="btn small danger" id="cancelRun">Cancel run</button>` : ""}</div>
      <div class="progress" style="margin:10px 0" role="progressbar" aria-valuenow="${pctDone}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pctDone}%"></i></div>
      <div class="stats"><div class="stat"><div class="v">${fmt(done)} / ${fmt(r.total)}</div><div class="l">checked (${pctDone}%)</div></div>
      <div class="stat"><div class="v">${fmt(r.pending)}</div><div class="l">waiting</div></div><div class="stat"><div class="v">${fmt(r.claimed)}</div><div class="l">in progress</div></div>
      <div class="stat"><div class="v">${fmt(r.failed)}</div><div class="l">failed</div></div><div class="stat"><div class="v">${fmt(r.rate_per_hour)}/h</div><div class="l">current speed</div></div>
      <div class="stat"><div class="v">${r.eta_hours != null ? r.eta_hours + " h" : "—"}</div><div class="l">time left</div></div></div>
      <p class="muted">Started ${esc(ago(r.started_at))} by ${esc(r.started_by || "?")}${r.finished_at ? ` · finished ${esc(ago(r.finished_at))}` : ""}</p>`;
    const agents = d.agents.length ? `<div class="tablewrap" style="margin-top:12px"><table style="min-width:640px"><thead><tr><th>Scraper PC</th><th>State</th><th class="num">Windows</th><th class="num">Done this run</th><th class="num">In hand</th><th class="num">CAPTCHAs</th><th>Seen</th></tr></thead><tbody>
      ${d.agents.map((a) => `<tr><td>${esc(a.name)}</td><td><span class="dotstate ${esc(a.state)}"></span>${esc(a.state)}${a.message ? `<div class="muted">${esc(a.message)}</div>` : ""}</td><td class="num">${a.workers ?? "—"}</td><td class="num">${fmt(a.done_this_run)}</td><td class="num">${fmt(a.claimed_now)}</td><td class="num">${fmt(a.captchas)}</td><td>${a.online ? "now" : a.last_seen_ago + " s ago"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted" style="margin-top:12px">No scraper PCs have connected yet.</p>`;
    return head + agents;
  };

  const importInfo = (d) => {
    const i = d.last_import;
    return i ? `Last import <b>${esc(ago(i.at))}</b> from ${esc(i.filename)}: ${fmt(i.products)} products (${fmt(i.added)} new, ${fmt(i.updated)} updated, ${fmt(i.removed)} removed${i.no_source_link ? `, ${fmt(i.no_source_link)} without a link` : ""}).` : "No sheet imported yet.";
  };

  const settings = (d) => {
    const s = d.settings;
    const f = (k, label, hint = "") => `<label class="field"><span>${label}</span><input name="${k}" value="${esc(s[k])}" ${admin ? "" : "disabled"}>${hint ? `<div class="hint">${hint}</div>` : ""}</label>`;
    return `<form id="setForm"><div class="formgrid">${f("zip_code", "Delivery ZIP code", "Amazon delivery dates depend on this.")}${f("timeframe_days", "Allowed delivery days", "Longer than this becomes “TimeFrame”.")}
      ${f("delay_min_seconds", "Min pause between products (s)")}${f("delay_max_seconds", "Max pause (s)")}${f("history_days", "Keep check history (days)")}${f("title_threshold", "Title match alert below (%)")}</div>
      <label class="field" style="margin-top:12px"><span>Words that mean the same (for the Amazon title check)</span>
        <textarea name="title_synonyms" rows="4" ${admin ? "" : "disabled"} placeholder="one group per line, e.g.&#10;sticker, decal&#10;tumbler, travel cup">${esc(s.title_synonyms || "")}</textarea>
        <div class="hint">Amazon often rewords titles. Words on the same line count as one word when comparing a title with the catalog. Plurals, joined words (pillowcase / pillow case), sizes (cm / inch) and typos are already handled. Saving re-checks every title.</div></label>
      ${admin ? `<div class="btnrow" style="margin-top:12px"><button class="btn primary">Save settings</button></div>` : ""}</form>`;
  };

  el.innerHTML = `<div id="scraperRoot"><div class="section-head"><div><h2>Scraper</h2><p>Each scraper PC has its own Start and Stop. PCs never wait for each other; if one stops, its products return to the pool.</p></div>
    <div class="btnrow"><button class="btn" id="exportOpt">Export results (CSV)</button></div></div>
    <div class="panel" id="live"></div>
    <div class="two"><div class="card"><h3>Settings</h3><div id="settings" style="margin-top:10px"></div></div>
    <div style="display:grid;gap:16px"><div class="card"><h3>Products</h3><p class="muted" id="importInfo" style="margin:8px 0"></p>
      ${admin ? `<p class="hint">Download the Listed Sheet from Google Sheets as CSV (File → Download → .csv), then choose it here. Newer scrape results already in the app are kept.</p><input type="file" id="sheet" accept=".csv,.xlsx,.xlsm">` : `<p class="hint">Only admins can import.</p>`}</div>
    ${admin ? `<div class="card"><h3>Connect a scraper PC</h3><p class="hint" style="margin:8px 0">Enter this server address and key in start_scraper.bat on the scraper PC.</p>
      <dl class="kv" style="grid-template-columns:90px 1fr"><dt>Address</dt><dd class="mono">${esc(location.origin)}</dd><dt>Key</dt><dd class="mono" id="key">••••••••</dd></dl>
      <div class="btnrow" style="margin-top:10px"><button class="btn small" id="showKey">Show / copy</button><button class="btn small danger" id="rotate">New key…</button></div></div>
      <div class="card"><h3>Backup</h3><p class="hint" style="margin:8px 0">The server saves a copy every day and keeps 7. Download one now to keep elsewhere.</p><button class="btn small" id="backup">Download backup now</button></div>` : ""}</div></div></div>`;

  el.onfocusin = (e) => { if (e.target.closest("#setForm")) editing = true; };
  el.onfocusout = (e) => { if (e.target.closest("#setForm")) editing = false; };
  el.onclick = async (e) => {
    const t = e.target;
    try {
      if (t.closest("#exportOpt")) download("/api/export/optimizer.csv");
      if (t.closest("#backup")) download("/api/backup");
      if (t.closest("#cancelRun") && (await confirmBox("Cancel this run?", "Scraper PCs stop receiving work. Results already saved are kept.", "Cancel run"))) {
        await api("/api/scraper/cancel", {});
        toast("Run cancelled.");
        draw();
      }
      if (t.closest("#showKey")) {
        const k = (await api("/api/scraper/key")).key;
        $("#key").textContent = k;
        try { await navigator.clipboard.writeText(k); toast("Key copied."); } catch {}
      }
      if (t.closest("#rotate") && (await confirmBox("Make a new scraper key?", `Every scraper PC must be given the new key before it can work again. The new key starts with “${(ctx.meta.brand?.mark || "key").toLowerCase().replace(/[^a-z0-9]/g, "") || "key"}_”, the store's short mark, so you can tell which store a key belongs to.`, "Make new key"))) {
        $("#key").textContent = (await api("/api/scraper/key/rotate", {})).key;
        toast("New key created.");
      }
    } catch (err) { toast(err.message, "bad"); }
  };
  el.onsubmit = async (e) => {
    if (e.target.id !== "setForm") return;
    e.preventDefault();
    try {
      await api("/api/scraper/settings", Object.fromEntries(new FormData(e.target)));
      await ctx.refreshMeta();
      toast("Settings saved.");
    } catch (err) { toast(err.message, "bad"); }
  };
  el.onchange = async (e) => {
    if (e.target.id !== "sheet" || !e.target.files[0]) return;
    const file = e.target.files[0];
    try {
      toast("Importing…");
      const r = await api("/api/import", await file.arrayBuffer(), { headers: { "X-Filename": encodeURIComponent(file.name) } });
      toast(`Imported ${fmt(r.products)} products (${fmt(r.added)} new).`);
      ctx.refreshCounts();
      e.target.value = "";
      $("#settings").dataset.ready = "";
      draw();
    } catch (err) { toast(err.message, "bad"); }
  };
  await draw();
  timer = setInterval(() => draw().catch(() => {}), 5000);
}
