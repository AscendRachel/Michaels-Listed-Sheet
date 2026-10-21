// "Today": every work queue as a card. Click one to open it in the Catalog.
import { QUEUE_UI, STATUS, ago, esc, fmt } from "./util.js";
import { go } from "./main.js";

const card = (key, label, ui, n) =>
  `<button class="qcard ${ui.cls} ${n ? "" : "zero"}" data-queue="${esc(key)}"><span class="qn">${fmt(n)}</span>
   <span class="ql">${esc(label)}</span><span class="qd">${esc(ui.desc)}</span></button>`;

export async function mount(el, params, ctx) {
  await ctx.refreshCounts();
  const { counts: c, total, last_check } = ctx.counts;
  if (!total) {
    el.innerHTML = `<div class="card"><h3>No products yet</h3>
      <p class="muted">${ctx.isAdmin() ? `Go to <a href="#scraper">Scraper</a> and import the Listed Sheet (download it from Google Sheets as CSV).` : "Ask an admin to import the Listed Sheet."}</p></div>`;
    return;
  }
  const problem = Object.keys(STATUS).filter((k) => !["Normal", "Not checked"].includes(k));
  const needs = problem.reduce((a, k) => a + (c[k] || 0), 0) + (c["listing-changed"] || 0) + (c["title-mismatch"] || 0);
  const groups = [
    ["Amazon source · latest scrape", [...problem.map((k) => card(k, k, STATUS[k], c[k] || 0)), card("Normal", "Normal", STATUS.Normal, c.Normal || 0), c["Not checked"] ? card("Not checked", "Not checked", STATUS["Not checked"], c["Not checked"]) : ""]],
  ];
  const labels = ctx.meta.queues;
  for (const g of ["Listing check", "Michaels sync", "Data quality"]) {
    groups.push([g, Object.entries(labels).filter(([, v]) => v.group === g).map(([k, v]) => card(k, v.label, QUEUE_UI[k] || { cls: "neutral", desc: "" }, c[k] || 0))]);
  }
  el.innerHTML = `<div class="section-head"><div><div class="eyebrow">Today</div><h2>${fmt(needs)} issues need attention</h2>
    <p>${fmt(total)} products · latest Amazon check ${esc(ago(last_check))}. Statuses follow column W of the Listed Sheet and update as scrapers report in. Pick a queue to work through it.</p></div>
    <div class="btnrow"><a class="btn" href="#scraper">Scraper</a><a class="btn primary" href="#catalog">Open catalog</a></div></div>
    <div class="qgroups">${groups.map(([t, h]) => `<section class="qgroup"><h3>${esc(t)}</h3><div class="qgrid">${[].concat(h).join("")}</div></section>`).join("")}</div>`;
  el.onclick = (e) => {
    const b = e.target.closest("[data-queue]");
    if (b) go("catalog", { queue: b.dataset.queue });
  };
}
