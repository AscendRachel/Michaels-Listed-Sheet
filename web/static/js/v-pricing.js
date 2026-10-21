// Pricing rules: the 3PL fee by weight, the buffer and the markup, with a live "what would change" preview.
import { $, api, confirmBox, debounce, esc, fmt, money, priceFor, toast } from "./util.js";

export async function mount(el, params, ctx) {
  const admin = ctx.isAdmin();
  const clone = (o) => JSON.parse(JSON.stringify(o));
  let draft = clone(ctx.meta.pricing);
  let impact = await api("/api/pricing/impact");
  const same = () => JSON.stringify({ m: draft.markup, b: draft.fee_buffer, t: draft.tables.current }) ===
                     JSON.stringify({ m: ctx.meta.pricing.markup, b: ctx.meta.pricing.fee_buffer, t: ctx.meta.pricing.tables.current });
  const dirty = () => !same();
  const tiers = () => draft.tables.current;

  const table = () => `<table style="min-width:0"><thead><tr><th>Weight up to (oz)</th><th class="num">3PL fee</th><th class="num">+ buffer = charged</th>${admin ? "<th></th>" : ""}</tr></thead><tbody>
    ${tiers().map((t, i) => `<tr><td>${admin ? `<input type="number" step="any" min="0" data-i="${i}" data-k="max" value="${t.max}" style="width:100px" aria-label="Weight limit, tier ${i + 1}">` : t.max}</td>
      <td class="num">${admin ? `<input type="number" step="0.01" min="0" data-i="${i}" data-k="fee" value="${t.fee}" style="width:100px" aria-label="Fee, tier ${i + 1}">` : money(t.fee)}</td>
      <td class="num">${money(Number(t.fee) + Number(draft.fee_buffer || 0))}</td>${admin ? `<td><button class="btn small danger" data-del="${i}" aria-label="Remove tier ${i + 1}">×</button></td>` : ""}</tr>`).join("")}</tbody></table>
    ${admin ? `<button class="btn small" id="addTier" style="margin-top:8px">+ Add weight tier</button>` : ""}`;

  const impactText = () => dirty()
    ? `With these changes: <b>${fmt(impact.up)}</b> prices go up, <b>${fmt(impact.down)}</b> go down, ${fmt(impact.same)} stay the same${impact.none ? `, ${fmt(impact.none)} can't be priced` : ""}${impact.avg ? ` (average ${impact.avg > 0 ? "+" : ""}${money(impact.avg)} on changed ones)` : ""}.`
    : `These are the rules in use now, applied to ${fmt(impact.products)} products${impact.none ? ` (${fmt(impact.none)} can't be priced: no weight, no source price, or heavier than the last tier)` : ""}. Edit anything to see how many prices would change.`;

  const calc = () => {
    const w = $("#cw")?.value, s = $("#cs")?.value;
    const p = priceFor(w, s, draft);
    $("#cout") && ($("#cout").innerHTML = w === "" || s === "" ? `<span class="muted">Enter a weight and a source price.</span>`
      : p.fee == null ? `<span class="muted">That weight is heavier than the last tier, so it can't be priced.</span>`
      : `3PL fee <b>${money(p.fee)}</b> → cost ${money(p.cost)} → × ${draft.markup} = ${money(p.selling)} → automation price <b>${money(p.auto)}</b>`);
  };

  const draw = () => {
    el.innerHTML = `<div class="section-head"><div><h2>Pricing rules</h2><p>Automation price = (source price + 3PL fee + buffer) × markup, rounded down to whole dollars, plus $0.99. The 3PL fee comes from the weight table below: the product's weight is rounded to the nearest ounce and the first tier it fits in is used. Saving recalculates every product.</p></div>
      ${admin ? `<div class="btnrow"><button class="btn" id="reset" ${dirty() ? "" : "disabled"}>Discard changes</button><button class="btn primary" id="save" ${dirty() ? "" : "disabled"}>Save pricing rules</button></div>` : ""}</div>
      ${admin ? "" : `<div class="banner">Only admins can change pricing rules.</div>`}
      <div class="two"><div>
        <div class="card" style="margin-bottom:16px"><div class="formgrid">
          <label class="field"><span>Markup (1.53 = 53%)</span><input type="number" step="0.01" id="markup" value="${draft.markup}" ${admin ? "" : "disabled"}></label>
          <label class="field"><span>Fee buffer ($ added to every fee)</span><input type="number" step="0.01" id="buffer" value="${draft.fee_buffer}" ${admin ? "" : "disabled"}></label></div>
          <div id="impact" class="banner ${dirty() ? "" : "good"}" style="margin:12px 0 0">${impactText()}</div></div>
        <div class="card"><h3>3PL fee by weight</h3>
          <p class="hint" style="margin:6px 0 10px">Each row means “up to this weight, this fee”. When the 3PL changes its rates, edit the fees, change a weight limit, or add and remove tiers here. A product heavier than the last tier can't be priced (it shows in the <i>Weight outside fee table</i> queue).</p>
          <div class="tablewrap" style="max-height:480px">${table()}</div></div></div>
      <div class="card"><h3>Try a price</h3><p class="hint" style="margin:6px 0 10px">Uses the rules as edited on this page, saved or not.</p>
        <div class="formgrid"><label class="field"><span>Weight (oz)</span><input type="number" step="any" min="0" id="cw"></label>
          <label class="field"><span>Source price ($)</span><input type="number" step="0.01" min="0" id="cs"></label></div>
        <div id="cout" style="margin-top:12px"></div></div></div>`;
    calc();
  };

  const preview = debounce(async () => {
    try {
      impact = await api("/api/pricing/preview", { pricing: draft });
      $("#impact") && ($("#impact").innerHTML = impactText());
    } catch (e) {
      $("#impact") && ($("#impact").textContent = e.message);
    }
  }, 400);
  const changed = () => {
    $("#save") && ($("#save").disabled = $("#reset").disabled = !dirty());
    $("#impact") && ($("#impact").className = "banner" + (dirty() ? "" : " good"));
    preview();
    calc();
  };

  el.oninput = (e) => {
    const t = e.target;
    if (t.id === "markup") draft.markup = Number(t.value);
    else if (t.id === "buffer") draft.fee_buffer = Number(t.value);
    else if (t.dataset.k) {
      tiers()[+t.dataset.i][t.dataset.k] = t.value === "" ? "" : Number(t.value);
      // the "charged" column follows as you type
      const row = t.closest("tr");
      if (row) row.children[2].textContent = money(Number(tiers()[+t.dataset.i].fee) + Number(draft.fee_buffer || 0));
    } else if (t.id === "cw" || t.id === "cs") return calc();
    else return;
    if (t.id === "buffer") el.querySelectorAll("tbody tr").forEach((tr, i) => (tr.children[2].textContent = money(Number(tiers()[i].fee) + Number(draft.fee_buffer || 0))));   // every "charged" cell depends on the buffer
    changed();
  };
  el.onclick = async (e) => {
    const t = e.target;
    if (t.closest("#reset")) { draft = clone(ctx.meta.pricing); impact = await api("/api/pricing/impact"); draw(); }
    const del = t.closest("[data-del]");
    if (del) { tiers().splice(+del.dataset.del, 1); draw(); changed(); }
    if (t.closest("#addTier")) {
      const last = tiers()[tiers().length - 1] || { max: 0, fee: 0 };
      tiers().push({ max: Number(last.max) + 16, fee: last.fee });
      draw();
      changed();
      el.querySelector(`input[data-i="${tiers().length - 1}"][data-k="max"]`)?.focus();
    }
    if (t.closest("#save")) {
      const sorted = [...tiers()].sort((a, b) => a.max - b.max);
      if (!(await confirmBox("Save pricing rules?", `${impactText().replace(/<[^>]+>/g, "")} Every product's price is recalculated right away.`, "Save"))) return;
      try {
        draft.tables.current = sorted;
        await api("/api/pricing", { pricing: draft });
        await Promise.all([ctx.refreshMeta(), ctx.refreshCounts()]);
        draft = clone(ctx.meta.pricing);
        impact = await api("/api/pricing/impact");
        draw();
        toast("Pricing rules saved.");
      } catch (err) { toast(err.message, "bad"); }
    }
  };
  draw();
}

