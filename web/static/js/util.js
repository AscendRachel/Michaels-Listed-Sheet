// Small helpers shared by every view.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmt = (n) => (n ?? 0).toLocaleString();
export const num = (v) => {
  if (v === null || v === undefined || v === "") return NaN;
  const n = Number(v);
  return Number.isFinite(n) ? n : NaN;
};
export const money = (v) => (Number.isNaN(num(v)) ? "—" : "$" + num(v).toFixed(2));
export const pct = (v) => (Number.isNaN(num(v)) ? "—" : (num(v) * 100).toFixed(0) + "%");
export const debounce = (fn, ms = 250) => {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
};

export function ago(ts) {
  if (!ts) return "—";
  const d = new Date(String(ts).replace(" ", "T"));
  if (Number.isNaN(d)) return ts;
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

// ---- storage that never throws (private windows, blocked storage)
export const store = {
  get(k, fallback = "") {
    try {
      return localStorage.getItem(k) ?? fallback;
    } catch {
      return fallback;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch {}
  },
};

// ---- API
export class ApiError extends Error {}
export async function api(path, body, opts = {}) {
  const init = { headers: {} };
  if (body !== undefined) {
    init.method = "POST";
    if (body instanceof ArrayBuffer || body instanceof Blob) {
      init.body = body;
      init.headers = { ...opts.headers };
    } else {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
  }
  const r = await fetch(path, init);
  if (r.status === 401) {
    location.href = "/login";
    throw new ApiError("Please sign in.");
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(j.error || `Error ${r.status}${r.status === 404 ? " (" + String(path).split("?")[0] + " was not found on the server. If you just updated, restart the server)" : ""}`);
  return j;
}
export const qs = (o) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v !== undefined && v !== null)).toString();

// ---- toast
let toastTimer;
export function toast(msg, kind = "") {
  const root = $("#toasts");
  root.innerHTML = `<div class="toast ${kind}" role="status">${esc(msg)}</div>`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (root.innerHTML = ""), kind === "bad" ? 6000 : 3000);
}

// ---- modal dialog: returns a promise resolved with the button value
export function dialog({ title, body, actions = [["Close", "close"]], wide = false }) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "modal" + (wide ? " wide" : "");
    dlg.innerHTML = `<form method="dialog"><h3>${esc(title)}</h3><div class="modal-body">${body}</div>
      <div class="btnrow end">${actions
        .map(([label, value, cls]) => `<button class="btn ${cls || ""}" value="${esc(value)}" ${value === "close" ? "formnovalidate" : ""}>${esc(label)}</button>`)
        .join("")}</div></form>`;
    dlg.addEventListener("close", () => {
      const result = { value: dlg.returnValue, el: dlg };
      resolve(result);
      setTimeout(() => dlg.remove(), 0);
    });
    document.body.append(dlg);
    dlg.showModal();
    dlg.querySelector("input,select,textarea")?.focus();
  });
}
export async function confirmBox(title, text, yes = "Yes") {
  const r = await dialog({ title, body: `<p>${esc(text)}</p>`, actions: [["Cancel", "close"], [yes, "yes", "primary"]] });
  return r.value === "yes";
}

export function download(href) {
  const a = document.createElement("a");
  a.href = href;
  a.click();
}

// ---- status vocabulary (colors come from CSS classes)
export const STATUS = {
  "Out of Stock": { cls: "crit", desc: "Inventory is on but Amazon shows currently unavailable." },
  "OOS Different Item": { cls: "crit", desc: "The Amazon listing changed to a different item (pack count or size no longer matches). Set it out of stock." },
  "OOS Variation": { cls: "crit", desc: "The variation Amazon shows isn't the one in the catalog's VARIATION column." },
  "High Price": { cls: "crit", desc: "Amazon price is now above your source price. Margin is shrinking." },
  "Low Inventory": { cls: "warn", desc: "Amazon shows “Only X left in stock”." },
  TimeFrame: { cls: "warn", desc: "Earliest delivery is further out than the allowed days." },
  "Low Price": { cls: "info", desc: "Amazon price dropped below your source price." },
  "Manual Check": { cls: "neutral", desc: "The scraper couldn't read availability, or the Amazon title doesn't look like the product." },
  "Check Inventory": { cls: "violet", desc: "Listed, inventory off, but Amazon looks available again." },
  Normal: { cls: "ok", desc: "No issues from the last check." },
  "Not checked": { cls: "neutral", desc: "No Amazon check yet." },
};
export const QUEUE_UI = {
  "listing-changed": { cls: "crit", desc: "The Amazon title now states a different pack count or size than the catalog. The listing really changed." },
  "title-mismatch": { cls: "warn", desc: "Too few of the product's key words are in Amazon's title (rewording is ignored). Worth a look." },
  "mz-missing": { cls: "warn", desc: "Listed in the catalog but missing from the latest Michaels download." },
  "mz-stock-off": { cls: "crit", desc: "Michaels quantity above 0 while your inventory flag is off." },
  "mz-zero-on": { cls: "warn", desc: "Michaels quantity is 0 while your inventory flag is on." },
  "mz-price": { cls: "info", desc: "Michaels price isn't the automation price." },
  "dq-weight": { cls: "neutral", desc: "No weight means no 3PL fee and no price." },
  "dq-fee": { cls: "neutral", desc: "Heavier than the last tier of the active fee table." },
  "dq-category": { cls: "neutral", desc: "Category is blank." },
  "dq-listing": { cls: "neutral", desc: "Listing status is blank." },
  "dq-link": { cls: "neutral", desc: "Can't be scraped until a source link is added." },
};
export const pill = (status) =>
  `<span class="pill s-${(STATUS[status] || STATUS["Not checked"]).cls}">${esc(status || "Not checked")}</span>`;

// ---- same pricing formula as the server (used for live previews while typing)
export function feeFor(weight, pr) {
  const w = num(weight);
  if (Number.isNaN(w)) return null;
  const r = Math.floor(w + 0.5);
  for (const t of pr.tables[pr.active_table] || []) if (r <= num(t.max)) return Math.round((num(t.fee) + num(pr.fee_buffer)) * 100) / 100;
  return null;
}
export function priceFor(weight, source, pr) {
  const fee = feeFor(weight, pr);
  const s = num(source);
  if (fee === null || Number.isNaN(s)) return { fee, cost: null, selling: null, auto: null, margin: null };
  const cost = s + fee;
  const selling = cost * num(pr.markup);
  const auto = Math.floor(selling) + 0.99;
  return { fee, cost, selling, auto, margin: (auto - cost) / auto };
}
