// Productivity: who resolved how many issues on a day, and how the last two weeks looked.
import { $, api, esc, qs } from "./util.js";

const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

export async function mount(el) {
  const f = { day: today(), days: 14 };
  const load = async () => {
    const r = await api("/api/productivity?" + qs(f));
    const max = Math.max(1, ...r.series.flatMap((s) => s.counts));
    $("#cards").innerHTML = r.people.length
      ? r.people.map((p) => `<div class="card"><h3>${esc(p.user)}</h3>
          <div style="font-size:30px;font-weight:700">${p.resolved}</div><div class="muted">issues resolved</div>
          <div class="hint">${p.touched} product${p.touched === 1 ? "" : "s"} touched · active ${esc(p.first)}–${esc(p.last)}</div>
          <div style="margin-top:6px">${Object.entries(p.kinds).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="pill">${esc(k)} · ${n}</span>`).join(" ") || `<span class="muted">No issues cleared yet.</span>`}</div></div>`).join("")
      : `<p class="empty">Nobody edited anything on ${esc(r.day)}.</p>`;
    $("#total").textContent = `${r.total_resolved} issue${r.total_resolved === 1 ? "" : "s"} resolved on ${r.day}`;
    $("#trend").innerHTML = r.series.length
      ? `<table><thead><tr><th>Who</th>${r.dates.map((d) => `<th style="text-align:center" title="${d}">${d.slice(8)}</th>`).join("")}<th>Total</th></tr></thead><tbody>${r.series.map((s) =>
          `<tr><td>${esc(s.user)}</td>${s.counts.map((n, i) => `<td style="text-align:center;${n ? `background:color-mix(in srgb,var(--brand) ${Math.round(15 + 70 * n / max)}%,transparent);font-weight:600` : "color:var(--muted)"}" data-day="${r.dates[i]}">${n || "·"}</td>`).join("")}<td><b>${s.counts.reduce((a, b) => a + b, 0)}</b></td></tr>`).join("")}</tbody></table>`
      : `<p class="empty">No edits in this period.</p>`;
  };
  el.innerHTML = `<div class="section-head"><div><h2>Productivity</h2><p>An issue counts as resolved when someone edits a product that had one (a problem status, Listing changed or Title mismatch) and it has none afterwards. A product counts once per person per day.</p></div></div>
    <div class="panel"><div class="toolbar"><label class="field" style="margin:0"><span>Day</span><input type="date" id="day" value="${f.day}"></label>
      <button class="btn small" id="td">Today</button><span class="grow"></span><b id="total"></b></div>
      <div id="cards" class="cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;padding:12px"></div></div>
    <div class="panel" style="margin-top:14px"><div class="toolbar"><b>Issues resolved per day</b><span class="grow"></span>
      <select id="span"><option value="7">Last 7 days</option><option value="14" selected>Last 14 days</option><option value="30">Last 30 days</option></select></div>
      <div class="tablewrap" id="trend"></div></div>`;
  el.onchange = (e) => {
    if (e.target.id === "day" && e.target.value) f.day = e.target.value;
    else if (e.target.id === "span") f.days = e.target.value;
    else return;
    load();
  };
  el.onclick = (e) => {
    if (e.target.id === "td") { f.day = today(); $("#day").value = f.day; load(); }
    const c = e.target.closest("[data-day]");
    if (c) { f.day = c.dataset.day; $("#day").value = f.day; load(); }
  };
  await load();
}
