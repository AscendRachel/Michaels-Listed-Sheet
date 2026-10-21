// Image viewer: the product's Michaels images and the main Amazon image, side by side for comparison.
import { esc } from "./util.js";

// Amazon addresses are stored without a size; ask for the size needed (small for the grid, large to compare).
export const amazonSized = (base, n) => (base ? base.replace(/\.(jpg|jpeg|png)$/i, `._AC_SL${n}_.$1`) : "");

let closeCurrent = null;

// product: { sku, name, michaels_sku_number, images: [url...] (Michaels), amazon_image: url (Amazon main image) }
export function openCarousel(product, start = 0) {
  closeCurrent?.();
  const panes = [];
  if ((product.images || []).length) panes.push({ key: "mz", title: "Michaels", imgs: product.images, full: (u) => u, thumb: (u) => u, link: (u) => u });
  if (product.amazon_image) panes.push({ key: "amz", title: "Amazon · main image", imgs: [product.amazon_image], full: (u) => amazonSized(u, 1000), thumb: (u) => amazonSized(u, 160), link: (u) => u });
  if (!panes.length) return;
  const previouslyFocused = document.activeElement;

  const lb = document.createElement("div");
  lb.className = "lb";
  lb.setAttribute("role", "dialog");
  lb.setAttribute("aria-modal", "true");
  lb.setAttribute("aria-label", `Images for ${product.sku}`);
  lb.innerHTML = `
    <div class="lb-top">
      <div class="lb-title"><b>${esc(product.name || product.sku)}</b>
        <span>${esc(product.sku)}${product.michaels_sku_number ? ` · Michaels #${esc(product.michaels_sku_number)}` : ""}${panes.length === 2 ? " · compare the two marketplaces" : ""}</span></div>
      <button class="btn small" id="lbClose" aria-label="Close">Close ✕</button>
    </div>
    <div class="lb-panes ${panes.length === 2 ? "two" : ""}">${panes.map((p) => `
      <section class="lb-pane" data-pane="${p.key}" aria-label="${esc(p.title)}">
        <div class="lb-phead"><b>${esc(p.title)}</b><span class="lb-pcount"></span><a class="btn small lb-popen" target="_blank" rel="noopener noreferrer">Open original ↗</a></div>
        <div class="lb-stage">
          <button class="lb-nav prev" data-nav="-1" aria-label="Previous image" ${p.imgs.length < 2 ? 'style="visibility:hidden"' : ""}>‹</button>
          <div class="lb-frame"><img alt="" referrerpolicy="no-referrer"><div class="lb-err hidden"></div></div>
          <button class="lb-nav next" data-nav="1" aria-label="Next image" ${p.imgs.length < 2 ? 'style="visibility:hidden"' : ""}>›</button>
        </div>
        ${p.imgs.length > 1 ? `<div class="lb-thumbs">${p.imgs.map((u, n) => `<button data-n="${n}" aria-label="Image ${n + 1}"><img src="${esc(p.thumb(u))}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>`).join("")}</div>` : ""}
      </section>`).join("")}
    </div>`;
  document.body.append(lb);

  // Each pane keeps its own position. Some Michaels links are dead on the marketplace's side: skip those when the viewer
  // opens, mark their thumbnails, and say so plainly if someone lands on one.
  panes.forEach((p) => {
    p.el = lb.querySelector(`[data-pane="${p.key}"]`);
    p.img = p.el.querySelector(".lb-frame img");
    p.err = p.el.querySelector(".lb-err");
    p.i = p.key === "mz" ? Math.min(Math.max(0, start), p.imgs.length - 1) : 0;
    p.failed = new Set();
    p.auto = true;
    p.skipped = false;
    p.show = (n) => {
      p.i = (n + p.imgs.length) % p.imgs.length;
      p.err.classList.add("hidden");
      p.img.classList.remove("hidden");
      p.img.src = p.full(p.imgs[p.i]);
      p.el.querySelector(".lb-pcount").textContent = p.imgs.length > 1
        ? `${p.i + 1} / ${p.imgs.length}${p.skipped && p.i > 0 ? " · earlier link(s) broken at the source" : ""}` : "";
      p.el.querySelector(".lb-popen").href = p.link(p.imgs[p.i]);
      p.el.querySelectorAll(".lb-thumbs button").forEach((b, k) => b.classList.toggle("on", k === p.i));
      p.el.querySelector(".lb-thumbs button.on")?.scrollIntoView({ block: "nearest", inline: "center" });
      if (p.imgs.length > 1) for (const k of [p.i + 1, p.i - 1]) new Image().src = p.full(p.imgs[(k + p.imgs.length) % p.imgs.length]);
    };
    p.markBad = (n) => { p.failed.add(n); p.el.querySelector(`.lb-thumbs button[data-n="${n}"]`)?.classList.add("bad"); };
    p.img.addEventListener("load", () => { p.auto = false; });
    p.img.addEventListener("error", () => {
      p.markBad(p.i);
      if (p.auto) {
        const next = p.imgs.findIndex((_, k) => k > p.i && !p.failed.has(k));
        if (next >= 0) { p.skipped = true; return p.show(next); }
      }
      p.img.classList.add("hidden");
      p.err.classList.remove("hidden");
      const link = `<br><a href="${esc(p.link(p.imgs[p.i]))}" target="_blank" rel="noopener noreferrer">Open the link directly ↗</a>`;
      p.err.innerHTML = p.key === "amz"
        ? `The Amazon image couldn't be loaded.<br><span class="lb-sub">Check this PC's internet connection.</span>${link}`
        : p.failed.size >= p.imgs.length
          ? `None of this product's Michaels images could be loaded.<br><span class="lb-sub">The links may be dead, or Michaels may only serve them to US addresses (turn on the VPN).</span>${link}`
          : `Image ${p.i + 1} isn't available from Michaels (the link is broken or blocked).<br><span class="lb-sub">Use ‹ › or the thumbnails to see the others.</span>${link}`;
      p.auto = false;
    });
    p.el.querySelectorAll(".lb-thumbs img").forEach((t, n) => t.addEventListener("error", () => p.markBad(n)));
  });

  const mz = panes.find((p) => p.key === "mz");
  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    lb.remove();
    closeCurrent = null;
    previouslyFocused?.focus?.();
  };
  const onKey = (e) => {
    const stop = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === "Escape") { stop(); close(); }
    else if (mz && (e.key === "ArrowRight" || e.key === "ArrowDown")) { stop(); mz.show(mz.i + 1); }
    else if (mz && (e.key === "ArrowLeft" || e.key === "ArrowUp")) { stop(); mz.show(mz.i - 1); }
    else if (mz && e.key === "Home") { stop(); mz.show(0); }
    else if (mz && e.key === "End") { stop(); mz.show(mz.imgs.length - 1); }
    else if (e.key === "Tab") e.stopPropagation();           // keep focus inside the viewer's own controls
  };
  document.addEventListener("keydown", onKey, true);
  lb.addEventListener("click", (e) => {
    if (e.target === lb || e.target.classList.contains("lb-stage") || e.target.classList.contains("lb-frame") || e.target.classList.contains("lb-panes")) return close();
    if (e.target.closest("#lbClose")) return close();
    const pane = panes.find((p) => p.el.contains(e.target));
    const nav = e.target.closest("[data-nav]");
    if (pane && nav) return pane.show(pane.i + Number(nav.dataset.nav));
    const t = e.target.closest("[data-n]");
    if (pane && t) pane.show(+t.dataset.n);
  });
  let x0 = null;                                                 // swipe on touch screens moves the Michaels images
  lb.addEventListener("touchstart", (e) => (x0 = e.touches[0].clientX), { passive: true });
  lb.addEventListener("touchend", (e) => {
    if (x0 == null || !mz) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) mz.show(mz.i + (dx < 0 ? 1 : -1));
    x0 = null;
  });
  closeCurrent = close;
  panes.forEach((p) => p.show(p.i));
  lb.querySelector("#lbClose").focus();
}
