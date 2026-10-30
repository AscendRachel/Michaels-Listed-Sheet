// App shell: navigation, shared state and the view router.
import { $, api, dialog, esc, fmt, setServerOffset, store, toast } from "./util.js";

export const ctx = {
  meta: null,
  counts: null,
  isAdmin: () => ctx.meta?.me?.role === "admin",
  async refreshCounts() {
    ctx.counts = await api("/api/queues");
    renderNav();
  },
  async refreshMeta() {
    ctx.meta = await api("/api/meta");
    setServerOffset(ctx.meta.utc_offset_minutes);
  },
};

const VIEWS = {
  today: { label: "Today", mod: "./v-today.js" },
  catalog: { label: "Catalog", mod: "./v-catalog.js" },
  add: { label: "Add products", mod: "./v-add.js" },
  michaels: { label: "Michaels", mod: "./v-michaels.js" },
  pricing: { label: "Pricing", mod: "./v-pricing.js" },
  sold: { label: "Sold counts", mod: "./v-sold.js", admin: true },
  scraper: { label: "Scraper", mod: "./v-scraper.js" },
  productivity: { label: "Productivity", mod: "./v-productivity.js" },
  activity: { label: "Activity", mod: "./v-activity.js" },
  history: { label: "Version history", mod: "./v-history.js" },
  team: { label: "Team", mod: "./v-team.js", admin: true },
};

export function route() {
  const [name, ...rest] = (location.hash || "#today").slice(1).split("?");
  return { name: VIEWS[name] ? name : "today", params: new URLSearchParams(rest.join("?")) };
}
export function go(name, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
  location.hash = `#${name}${q ? "?" + q : ""}`;
}

function renderNav() {
  const cur = route().name;
  const c = ctx.counts;
  const attention = c ? ["Out of Stock", "OOS Different Item", "OOS Variation", "High Price"].reduce((a, k) => a + (c.counts[k] || 0), 0) : 0;
  $("#tabs").innerHTML = Object.entries(VIEWS)
    .filter(([, v]) => !v.admin || ctx.isAdmin())
    .map(([k, v]) => {
      const badge = k === "catalog" && c ? `<span class="count">${fmt(c.total)}</span>` : k === "today" && attention ? `<span class="dot">${fmt(attention)}</span>` : "";
      return `<a class="tab" href="#${k}" ${cur === k ? 'aria-current="page"' : ""}>${v.label}${badge}</a>`;
    })
    .join("");
  const me = ctx.meta?.me;
  $("#me").innerHTML = me
    ? `<span><b>${esc(me.name)}</b> · ${me.role === "admin" ? "Admin" : "Member"}</span>
       <button class="btn small" id="themeBtn" title="Switch light/dark">◐</button>
       <button class="btn small" id="pwBtn">Password</button><button class="btn small" id="outBtn">Sign out</button>`
    : "";
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("#themeBtn, #pwBtn, #outBtn");
  if (!t) return;
  if (t.id === "themeBtn") {
    const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    store.set("gt_theme", document.documentElement.dataset.theme);
  } else if (t.id === "outBtn") {
    await api("/api/logout", {});
    location.href = "/login";
  } else {
    const r = await dialog({
      title: "Change your password",
      body: `<label class="field"><span>Current password</span><input type="password" name="cur" autocomplete="current-password" required></label>
             <label class="field"><span>New password (8+ characters)</span><input type="password" name="new" autocomplete="new-password" minlength="8" required></label>`,
      actions: [["Cancel", "close"], ["Change password", "ok", "primary"]],
    });
    if (r.value !== "ok") return;
    try {
      const f = new FormData(r.el.querySelector("form"));
      await api("/api/me/password", { current: f.get("cur"), new: f.get("new") });
      toast("Password changed.");
    } catch (err) {
      toast(err.message, "bad");
    }
  }
});

let token = 0;
async function show() {
  const my = ++token;
  const { name, params } = route();
  renderNav();
  const main = $("#main");
  main.innerHTML = `<div class="empty">Loading…</div>`;
  for (const h of ["onclick", "onchange", "oninput", "onsubmit", "onfocusin", "onfocusout"]) main[h] = null;
  document.querySelectorAll(".drawer, .scrim").forEach((n) => n.remove());
  try {
    const mod = await import(VIEWS[name].mod);
    if (my !== token) return;
    await mod.mount(main, params, ctx);
  } catch (err) {
    if (my !== token) return;
    main.innerHTML = `<div class="banner bad">Something went wrong: ${esc(err.message)}. If the server window was closed, start it again and refresh.</div>`;
  }
}

window.addEventListener("hashchange", show);
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.querySelector("dialog[open]")) {
    const s = $("#q");
    if (s) {
      e.preventDefault();
      s.focus();
    }
  }
});

(async () => {
  try {
    await Promise.all([ctx.refreshMeta(), ctx.refreshCounts()]);
  } catch (err) {
    $("#main").innerHTML = `<div class="banner bad">Can't reach the server (${esc(err.message)}).</div>`;
    return;
  }
  show();
})();
