// Team: logins (admins only).
import { $, api, confirmBox, dialog, esc, toast } from "./util.js";

export async function mount(el, params, ctx) {
  const draw = async () => {
    const rows = (await api("/api/team")).items;
    const me = ctx.meta.me.username;
    const b = ctx.meta.brand || { name: "", mark: "", color: "", logo: "" };
    el.innerHTML = `<div class="card" style="margin-bottom:16px"><h3>Store name &amp; logo</h3>
        <p class="muted">What everyone sees in the browser tab, the header and the sign-in page. Each store has its own settings, so one store's name never shows on another.</p>
        <div class="formgrid" style="grid-template-columns:1fr 110px 120px">
          <label class="field"><span>App name</span><input id="bName" maxlength="60" value="${esc(b.name)}"></label>
          <label class="field"><span>Short mark (1 to 3 letters)</span><input id="bMark" maxlength="3" value="${esc(b.mark)}"></label>
          <label class="field"><span>Accent colour</span><input id="bColor" type="color" value="${esc(b.color || "#1f7a4d")}"></label></div>
        <div class="btnrow" style="margin-top:12px;gap:12px;align-items:center">
          ${b.logo ? `<span class="logo logo-img"><img src="${esc(b.logo)}" alt="Current logo"></span>` : `<span class="logo">${esc(b.mark)}</span>`}
          <label class="btn small">${b.logo ? "Change logo…" : "Upload a logo…"} <input type="file" id="bLogo" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>
          ${b.logo ? `<button class="btn small" id="bLogoRemove">Remove logo</button>` : `<span class="muted">Optional. PNG, JPG, WEBP or GIF under 400 KB. Without one, the short mark is used.</span>`}
          <span class="grow"></span><button class="btn primary" id="bSave">Save</button></div></div>
      <div class="section-head"><div><h2>Team</h2><p>Everyone who can sign in. Admins can also change pricing rules, import sheets, change scraper settings and manage the team.</p></div>
      <div class="btnrow"><button class="btn primary" id="add">Add team member</button></div></div>
      <div class="panel"><div class="tablewrap"><table style="min-width:560px"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th></th></tr></thead><tbody>
      ${rows.map((u) => `<tr><td>${esc(u.name)}</td><td class="mono">${esc(u.username)}</td><td>${u.role === "admin" ? '<span class="pill s-ok">Admin</span>' : "Member"}</td>
        <td class="num"><button class="btn small" data-reset="${esc(u.username)}">Reset password</button> ${u.username === me ? "" : `<button class="btn small danger" data-remove="${esc(u.username)}">Remove</button>`}</td></tr>`).join("")}</tbody></table></div></div>`;
  };
  el.onclick = async (e) => {
    const t = e.target;
    try {
      if (t.closest("#bSave")) {                          // name, mark, colour (and the logo, if one was picked), then reload so the header and tab update
        const f = $("#bLogo").files[0];
        if (f) await api("/api/brand/logo", await f.arrayBuffer(), { headers: { "X-Filename": encodeURIComponent(f.name) } });
        await api("/api/brand", { name: $("#bName").value, mark: $("#bMark").value, color: $("#bColor").value === "#1f7a4d" && !ctx.meta.brand.color ? "" : $("#bColor").value });
        toast("Saved. Reloading…");
        setTimeout(() => location.reload(), 700);
        return;
      }
      if (t.closest("#bLogoRemove")) {
        await api("/api/brand/logo/remove", {});
        toast("Logo removed. Reloading…");
        setTimeout(() => location.reload(), 700);
        return;
      }
      if (t.closest("#add")) {
        const r = await dialog({
          title: "Add team member",
          body: `<label class="field"><span>Name</span><input name="name" required></label><label class="field"><span>Username</span><input name="username" required autocapitalize="off"></label>
                 <label class="field"><span>Temporary password (8+ characters)</span><input name="password" minlength="8" required></label>
                 <label class="field"><span>Role</span><select name="role"><option value="member">Member</option><option value="admin">Admin</option></select></label>`,
          actions: [["Cancel", "close"], ["Add", "ok", "primary"]],
        });
        if (r.value === "ok") {
          await api("/api/team/add", Object.fromEntries(new FormData(r.el.querySelector("form"))));
          toast("Team member added. Share the password privately.");
          draw();
        }
      }
      const rs = t.closest("[data-reset]");
      if (rs) {
        const r = await dialog({ title: "Reset password for " + rs.dataset.reset, body: `<label class="field"><span>New password (8+ characters)</span><input name="password" minlength="8" required></label>`, actions: [["Cancel", "close"], ["Reset", "ok", "primary"]] });
        if (r.value === "ok") {
          await api("/api/team/reset", { username: rs.dataset.reset, password: new FormData(r.el.querySelector("form")).get("password") });
          toast("Password reset.");
        }
      }
      const rm = t.closest("[data-remove]");
      if (rm && (await confirmBox("Remove " + rm.dataset.remove + "?", "They won't be able to sign in. Their past changes stay in Activity.", "Remove"))) {
        await api("/api/team/remove", { username: rm.dataset.remove });
        draw();
      }
    } catch (err) { toast(err.message, "bad"); }
  };
  await draw();
}
