"""Greentek Catalog - web app and API.

Run with:  python run.py     (or: uvicorn app.main:app)
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, brand, catalog, db, imports, protect, scraper, sheet, sold, work
from .auth import admin_user, current_user
from .db import ROOT, conn, now_str, tx

WEB = ROOT / "web"
NO_STORE = {"Cache-Control": "no-store"}

# Windows lets installed programs override file types in its registry (an .svg can come back as
# "document/ConceptDrawDIAGRAM", a .js as text/plain), and browsers refuse scripts and images served
# with a wrong type. Pin the ones this app serves so it behaves the same on every PC.
import mimetypes
for _ext, _type in {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
                    ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon"}.items():
    mimetypes.add_type(_type, _ext)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    db.rebuild_title_model()                         # word weights for the Amazon title check
    with tx() as c:
        if db.get_settings(c).get("title_algo") != db.TITLE_ALGO:     # matching improved: re-judge every stored title once
            db.recompute_titles(c)
            db.set_setting(c, "title_algo", db.TITLE_ALGO)
        if db.get_settings(c).get("status_algo") != db.STATUS_ALGO:   # new statuses (OOS Variation / Different Item): re-derive once
            db.recompute_statuses(c)
            db.set_setting(c, "status_algo", db.STATUS_ALGO)
    auth.agent_key()
    scraper.prune_history()
    import os
    if not os.environ.get("GT_NO_AUTO_BACKUP"):
        scraper.start_background()
    yield


app = FastAPI(title="Greentek Catalog", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------ errors in one shape: {"error": "..."}
@app.exception_handler(ValueError)
async def _value_error(_, e: ValueError):
    return JSONResponse({"error": str(e)}, 400, headers=NO_STORE)


@app.exception_handler(HTTPException)
async def _http_error(_, e: HTTPException):
    return JSONResponse({"error": e.detail}, e.status_code, headers=NO_STORE)


@app.exception_handler(RequestValidationError)
async def _bad_body(_, e: RequestValidationError):
    return JSONResponse({"error": "That request wasn't understood."}, 422, headers=NO_STORE)


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/static/"):
        # Re-check the page's scripts and styles on every load (a quick "not modified" answer when nothing changed),
        # so an update never leaves a browser running old code against a new server.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


async def jbody(request: Request) -> dict:
    try:
        b = await request.json()
    except ValueError:
        return {}
    return b if isinstance(b, dict) else {}


async def rawbody(request: Request) -> bytes:
    return await request.body()


# ------------------------------------------------------------------ pages
def _page(name: str) -> HTMLResponse:
    """An HTML page with this store's name, logo and colour filled in (each store has its own database, so its own look)."""
    return HTMLResponse(brand.render((WEB / name).read_text(encoding="utf-8")), headers=NO_STORE)


@app.get("/brand/logo", include_in_schema=False)
def brand_logo():
    f = brand.logo_file()
    if not f:
        raise HTTPException(404, "No logo.")
    return Response(f[0], media_type=f[1], headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
                                                    "Content-Security-Policy": "default-src 'none'"})


@app.get("/login", include_in_schema=False)
def login_page():
    return _page("login.html")


@app.get("/", include_in_schema=False)
def home(request: Request):
    return _page("index.html") if auth.session_user(request.cookies.get(auth.COOKIE)) else RedirectResponse("/login")


app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


# ------------------------------------------------------------------ session
@app.get("/api/ping")
def ping():
    return {"ok": True, "server_time": now_str()}


@app.get("/api/setup-needed")
def setup_needed(request: Request):
    return {"setup": auth.user_count() == 0, "allowed": auth.is_local_direct(request)}


@app.post("/api/login")
def login(request: Request, b: dict = Depends(jbody)):
    ip, now = auth.client_ip(request), time.time()
    recent = [t for t in auth.FAILED.get(ip, []) if now - t < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many failed sign-ins. Wait 10 minutes and try again.")
    u = auth.check_login(b.get("username"), b.get("password"))
    if not u:
        auth.FAILED[ip] = recent + [now]
        raise HTTPException(401, "Wrong username or password.")
    auth.FAILED.pop(ip, None)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(value=auth.new_session(u["username"]), **auth.cookie_kwargs(request, auth.SESSION_DAYS * 86400))
    return resp


@app.post("/api/setup")
def setup(request: Request, b: dict = Depends(jbody)):
    if auth.user_count() > 0:
        raise HTTPException(403, "Setup is already done. Sign in instead.")
    if not auth.is_local_direct(request):
        raise HTTPException(403, "Create the first admin on the server itself.")
    username = auth.add_user(b.get("username"), b.get("name"), b.get("password"), "admin")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(value=auth.new_session(username), **auth.cookie_kwargs(request, auth.SESSION_DAYS * 86400))
    return resp


@app.post("/api/logout")
def logout(request: Request):
    tok = request.cookies.get(auth.COOKIE)
    if tok:
        with tx() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (tok,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/me")
def me(u=Depends(current_user)):
    return u


@app.post("/api/me/password")
def change_password(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    if not auth.check_login(u["username"], b.get("current")):
        raise ValueError("Your current password is wrong.")
    auth.set_password(u["username"], b.get("new"))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(value=auth.new_session(u["username"]), **auth.cookie_kwargs(request, auth.SESSION_DAYS * 86400))
    return resp


# ------------------------------------------------------------------ catalog
@app.get("/api/meta")
def meta(u=Depends(current_user)):
    return {**catalog.meta(), "me": u, "locks": protect.all_locks(), "brand": brand.get()}


@app.post("/api/brand")
def set_brand(u=Depends(admin_user), b: dict = Depends(jbody)):
    """The store's name, short logo text and colour (admins only)."""
    return {"brand": brand.save(b.get("name"), b.get("mark"), b.get("color"), u["name"])}


@app.post("/api/brand/logo")
def set_brand_logo(u=Depends(admin_user), raw: bytes = Depends(rawbody)):
    return {"brand": brand.set_logo(raw, u["name"])}


@app.post("/api/brand/logo/remove")
def remove_brand_logo(u=Depends(admin_user)):
    return {"brand": brand.remove_logo(u["name"])}


# ------------------------------------------------------------------ work lists (a saved, frozen list of rows being resolved)
@app.get("/api/work")
def work_mine(u=Depends(current_user)):
    return {"items": work.mine(u)}


@app.post("/api/work")
def work_start(u=Depends(current_user), b: dict = Depends(jbody)):
    return work.start(u, b.get("name"), b.get("params") or {}, b.get("skus") or [])


@app.get("/api/work/{list_id}")
def work_load(list_id: int, u=Depends(current_user)):
    return work.load(u, list_id)


@app.post("/api/work/{list_id}/finish")
def work_finish(list_id: int, u=Depends(current_user), b: dict = Depends(jbody)):
    return work.finish(u, list_id, bool(b.get("force")))


@app.post("/api/work/{list_id}/discard")
def work_discard(list_id: int, u=Depends(current_user)):
    return work.discard(u, list_id)


@app.post("/api/locks")
def set_lock(u=Depends(admin_user), b: dict = Depends(jbody)):
    """Protect a column (or remove its protection): admins only."""
    return {"locks": protect.set_lock(b.get("col"), b.get("mode"), b.get("users") or [], b.get("note"), u["name"])}


@app.get("/api/queues")
def queues(u=Depends(current_user)):
    return catalog.queue_counts()


@app.get("/api/products")
def products(request: Request, u=Depends(current_user)):
    return catalog.list_products(dict(request.query_params))


@app.get("/api/products/skus")
def product_skus(request: Request, u=Depends(current_user)):
    """Every SKU matching the current filters (for 'select all N matching')."""
    skus = catalog.matching_skus(dict(request.query_params))
    return {"skus": skus}


@app.get("/api/products/dupes")
def dupes(request: Request, u=Depends(current_user)):
    q = request.query_params
    return {"dupes": catalog.find_dupes(q.get("sku", ""), q.get("asin", ""), q.get("upc", ""), q.get("link", ""), q.get("ignore", ""))}


@app.get("/api/products/export.csv")
def export_view(request: Request, u=Depends(current_user)):
    return _csv(catalog.export_view(dict(request.query_params)), f"catalog-view-{datetime.now():%Y%m%d-%H%M}.csv")


@app.post("/api/products/bulk")
def bulk(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    protect.require(u, list((b.get("patch") or {}).keys()) + (["title_check"] if b.get("action") == "accept_title" else []))
    return catalog.bulk_update(b.get("skus") or [], b.get("patch") or {}, b.get("action") or "", u["name"])


@app.post("/api/products/create")
def create(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    protect.require(u, [k for k, v in (b.get("product") or {}).items() if protect.present(v)])
    return catalog.create_product(b.get("product") or {}, u["name"])


@app.post("/api/products/preview-batch")
def preview_batch(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    protect.require(u, {k for r in (b.get("rows") or []) for k, v in r.items() if protect.present(v)} - {"loose"})
    return catalog.preview_batch(b.get("rows") or [], bool(b.get("loose")))


@app.post("/api/products/delete")
def delete_products(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    return catalog.delete_products(b.get("skus") or [], u["name"])


@app.get("/api/products/deleted")
def deleted_products(u=Depends(current_user)):
    return catalog.list_deleted()


@app.post("/api/products/restore")
def restore_products(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    return catalog.restore_products(b.get("skus") or [], u["name"])


@app.post("/api/products/create-batch")
def create_batch(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    protect.require(u, {k for r in (b.get("rows") or []) for k, v in r.items() if protect.present(v)})
    return catalog.create_batch(b.get("rows") or [], u["name"], bool(b.get("loose")))


@app.get("/api/sheet/skus")
def sheet_skus(request: Request, u=Depends(current_user)):
    return sheet.sku_list(dict(request.query_params))


# The sheet sends its filters in the request body (POST): a long list of ticked values would not fit in a web address.
@app.post("/api/sheet/skus")
def sheet_skus_post(b: dict = Depends(jbody), u=Depends(current_user)):
    return sheet.sku_list(b)


@app.post("/api/sheet/values")
def sheet_values_post(b: dict = Depends(jbody), u=Depends(current_user)):
    return catalog.column_values(str(b.get("col", "")), b)


@app.post("/api/products/export.csv")
def export_view_post(b: dict = Depends(jbody), u=Depends(current_user)):
    return _csv(catalog.export_view(b), f"catalog-view-{datetime.now():%Y%m%d-%H%M}.csv")


@app.get("/api/sheet/history")
def sheet_history(request: Request, u=Depends(current_user)):
    q = request.query_params
    return sheet.cell_history(q.get("sku", ""), q.get("field", ""))


@app.get("/api/sheet/values")
def sheet_values(request: Request, u=Depends(current_user)):
    q = dict(request.query_params)
    return catalog.column_values(q.get("col", ""), q)


@app.post("/api/sheet/rows")
def sheet_rows(b: dict = Depends(jbody), u=Depends(current_user)):
    return {"rows": sheet.rows([str(s) for s in (b.get("skus") or [])])}


@app.post("/api/sheet/cells")
def sheet_cells(b: dict = Depends(jbody), u=Depends(current_user)):
    edits, blocked = protect.filter_edits(u, b.get("edits") or [])          # protected columns: refuse those cells, save the rest
    res = sheet.apply_cells(edits, u["name"], b.get("filter"), b.get("action") or "edited in the sheet")
    if blocked:
        res["errors"] = (blocked + res["errors"])[:50]
        res["error_count"] += len(blocked)
    return res


@app.get("/api/sheet/columns")
def sheet_columns(u=Depends(current_user)):
    return {"columns": sheet.list_custom()}


@app.post("/api/sheet/columns/add")
def sheet_add_column(b: dict = Depends(jbody), u=Depends(admin_user)):
    return sheet.add_column(b.get("label"), b.get("type") or "text", b.get("after"), u["name"])


@app.post("/api/sheet/columns/delete")
def sheet_delete_column(b: dict = Depends(jbody), u=Depends(admin_user)):
    return sheet.delete_column(str(b.get("key") or ""), u["name"])


@app.post("/api/sheet/use-amazon-price")
def sheet_use_amazon(b: dict = Depends(jbody), u=Depends(current_user)):
    protect.require(u, ["source_price"])
    return sheet.use_amazon_price([str(s) for s in (b.get("skus") or [])], u["name"], b.get("filter"))


@app.get("/api/products/{sku:path}")
def product(sku: str, u=Depends(current_user)):
    return catalog.product_detail(unquote(sku))


@app.post("/api/products/{sku:path}")
def edit_product(sku: str, request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    protect.require(u, list((b.get("patch") or {}).keys()))
    return catalog.update_product(unquote(sku), b.get("patch") or {}, u["name"], b.get("action") or "edited")


@app.get("/api/pricing/impact")
def impact(u=Depends(current_user)):
    return catalog.pricing_impact()


@app.post("/api/pricing/preview")
def pricing_preview(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    return catalog.pricing_impact(b.get("pricing") or {})


@app.post("/api/pricing")
def save_pricing(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    return catalog.save_pricing(b.get("pricing") or {}, u["name"])


@app.get("/api/productivity")
def productivity(request: Request, u=Depends(current_user)):
    return catalog.productivity(dict(request.query_params))


@app.get("/api/activity")
def activity(request: Request, u=Depends(current_user)):
    return catalog.activity_feed(dict(request.query_params))


@app.get("/api/views")
def views(u=Depends(current_user)):
    return {"items": catalog.saved_views(u["username"])}


@app.post("/api/views")
def add_view(request: Request, u=Depends(current_user), b: dict = Depends(jbody)):
    return catalog.save_view(u["username"], b.get("name"), b.get("params") or {}, bool(b.get("shared")))


@app.post("/api/views/{view_id}/delete")
def delete_view(view_id: int, u=Depends(current_user)):
    catalog.delete_view(u["username"], view_id, u["role"] == "admin")
    return {"ok": True}


# ------------------------------------------------------------------ exports
def _csv(data: bytes, filename: str) -> Response:
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', **NO_STORE})


@app.get("/api/export/optimizer.csv")
def export_optimizer(u=Depends(current_user)):
    return _csv(catalog.export_optimizer(), f"source-optimizer-{datetime.now():%Y%m%d-%H%M}.csv")


@app.get("/api/export/michaels-fixes.csv")
def export_fixes(u=Depends(current_user)):
    return _csv(catalog.export_michaels_fixes(), f"michaels-price-fixes-{datetime.now():%Y%m%d-%H%M}.csv")


# ------------------------------------------------------------------ imports (admin)
@app.post("/api/import")
def import_sheet(request: Request, u=Depends(admin_user), raw: bytes = Depends(rawbody)):
    return imports.import_products(unquote(request.headers.get("x-filename") or "upload.csv"), raw, u["name"])


@app.post("/api/michaels/preview")
def michaels_preview(request: Request, u=Depends(admin_user), raw: bytes = Depends(rawbody)):
    mapping = None
    if request.headers.get("x-mapping"):
        import json
        mapping = json.loads(unquote(request.headers["x-mapping"]))
    return imports.mz_preview(unquote(request.headers.get("x-filename") or "upload.csv"), raw, mapping)


@app.post("/api/michaels/apply")
def michaels_apply(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    return imports.mz_apply(b.get("token"), bool(b.get("clear_missing", True)), u["name"])


@app.get("/api/sold")
def sold_info(u=Depends(admin_user)):
    return {"config": sold.get_config(), "status": sold.get_status()}


@app.post("/api/sold/config")
def sold_config(u=Depends(admin_user), b: dict = Depends(jbody)):
    return {"config": sold.save_config(b, u["name"])}


@app.post("/api/sold/sync")
def sold_sync(u=Depends(admin_user), b: dict = Depends(jbody)):
    return {"status": sold.sync(u["name"], b.get("years")), "config": sold.get_config()}


@app.post("/api/sold/upload")
def sold_upload(request: Request, u=Depends(admin_user), raw: bytes = Depends(rawbody)):
    year = request.headers.get("x-year", "")
    if year not in sold.YEARS:
        raise ValueError("Say which year this file is for.")
    return {"status": sold.sync(u["name"], [year], {year: (unquote(request.headers.get("x-filename") or "upload.csv"), raw)})}


@app.get("/api/michaels/history")
def michaels_history(u=Depends(current_user)):
    return imports.mz_history()


# ------------------------------------------------------------------ scraper
@app.post("/api/agent/{action}")
def agent(action: str, request: Request, b: dict = Depends(jbody)):
    auth.agent_ok(request)
    fn = scraper.AGENT_ROUTES.get(action)
    if not fn:
        raise HTTPException(404, "not found")
    return fn(b)


@app.get("/api/scraper")
def scraper_dashboard(u=Depends(current_user)):
    return scraper.dashboard()


@app.post("/api/scraper/settings")
def scraper_settings(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    return scraper.save_settings(b, u["name"])


@app.post("/api/scraper/cancel")
def scraper_cancel(u=Depends(admin_user)):
    scraper.cancel_run(u["name"])
    return {"ok": True}


@app.get("/api/scraper/key")
def scraper_key(u=Depends(admin_user)):
    return {"key": auth.agent_key()}


@app.post("/api/scraper/key/rotate")
def scraper_key_rotate(u=Depends(admin_user)):
    key = auth.rotate_agent_key()
    with tx() as c:
        db.log_activity(c, u["name"], "changed the scraper key", "", "Scraper PCs need the new key")
    return {"key": key}


@app.get("/api/backup")
def backup(u=Depends(admin_user)):
    return Response(scraper.backup_download(), media_type="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="greentek-backup-{datetime.now():%Y-%m-%d-%H%M}.db.gz"'})


# ------------------------------------------------------------------ team (admin)
@app.get("/api/team")
def team(u=Depends(admin_user)):
    rows = conn().execute("SELECT username, name, role, created_at FROM users WHERE disabled=0 ORDER BY role, username")
    return {"items": [dict(r) for r in rows]}


@app.post("/api/team/add")
def team_add(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    username = auth.add_user(b.get("username"), b.get("name"), b.get("password"), b.get("role", "member"))
    with tx() as c:
        db.log_activity(c, u["name"], "added team member", "", username)
    return {"ok": True, "username": username}


@app.post("/api/team/reset")
def team_reset(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    auth.set_password(b.get("username", ""), b.get("password"))
    with tx() as c:
        db.log_activity(c, u["name"], "reset the password of", "", b.get("username", ""))
    return {"ok": True}


@app.post("/api/team/remove")
def team_remove(request: Request, u=Depends(admin_user), b: dict = Depends(jbody)):
    if b.get("username") == u["username"]:
        raise ValueError("You can't remove yourself.")
    with tx() as c:
        c.execute("UPDATE users SET disabled=1 WHERE username=?", (b.get("username"),))
        c.execute("DELETE FROM sessions WHERE username=?", (b.get("username"),))
        db.log_activity(c, u["name"], "removed team member", "", b.get("username", ""))
    return {"ok": True}
