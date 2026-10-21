"""The name, logo and colour people see (browser tab title, header, sign-in page, tab icon).

Every store/app has its own database, so the branding is simply a setting inside that database: an admin edits it on the
Team tab and it never leaks into another store. A fresh app starts from environment variables (GT_APP_NAME,
GT_APP_MARK, GT_BRAND_COLOR) so IT can name each copy at deployment time; what an admin saves later wins over those.
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse

from . import db
from .db import conn, log_activity, now_str, tx

DEFAULT_NAME = "Greentek Catalog"
DEFAULT_MARK = "GT"
DEFAULT_COLOR = "#1f7a4d"
MAX_LOGO = 400 * 1024
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
KEYS = ("brand_name", "brand_mark", "brand_color")


def _defaults() -> dict:
    return {"name": (os.environ.get("GT_APP_NAME") or DEFAULT_NAME).strip()[:60],
            "mark": (os.environ.get("GT_APP_MARK") or DEFAULT_MARK).strip()[:3],
            "color": os.environ.get("GT_BRAND_COLOR", "").strip() if HEX.match(os.environ.get("GT_BRAND_COLOR", "").strip()) else ""}


def get(c=None) -> dict:
    c = c or conn()
    saved = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings WHERE key IN (?,?,?)", KEYS)}
    d = _defaults()
    name = (saved.get("brand_name") or "").strip() or d["name"]
    mark = (saved.get("brand_mark") or "").strip() or ("".join(w[0] for w in name.split()[:2]).upper() if saved.get("brand_name") else d["mark"])
    color = saved.get("brand_color") if HEX.match(saved.get("brand_color") or "") else d["color"]
    logo = c.execute("SELECT at FROM brand_logo WHERE id=1").fetchone()
    return {"name": name, "mark": mark[:3], "color": color, "logo": f"/brand/logo?v={urllib.parse.quote(logo['at'])}" if logo else ""}


def save(name: str, mark: str, color: str, user: str) -> dict:
    name, mark, color = (name or "").strip()[:60], (mark or "").strip()[:3], (color or "").strip()
    if color and not HEX.match(color):
        raise ValueError("The colour must look like #1f7a4d.")
    with tx() as c:
        for k, v in (("brand_name", name), ("brand_mark", mark), ("brand_color", color)):
            db.set_setting(c, k, v)
        log_activity(c, user, "changed the app name and logo", "", name or "(default name)")
    return get()


def sniff(data: bytes) -> str:
    """The image type from the file's own first bytes (never from the file name): png / jpeg / webp / gif only."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def set_logo(data: bytes, user: str) -> dict:
    if not data:
        raise ValueError("Choose an image file first.")
    if len(data) > MAX_LOGO:
        raise ValueError("The logo is too big. Use an image under 400 KB.")
    ctype = sniff(data)
    if not ctype:
        raise ValueError("The logo must be a PNG, JPG, WEBP or GIF image.")
    with tx() as c:
        c.execute("INSERT INTO brand_logo(id, ctype, data, at) VALUES(1,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET ctype=excluded.ctype, data=excluded.data, at=excluded.at", (ctype, data, now_str()))
        log_activity(c, user, "changed the app name and logo", "", "new logo")
    return get()


def remove_logo(user: str) -> dict:
    with tx() as c:
        c.execute("DELETE FROM brand_logo WHERE id=1")
        log_activity(c, user, "changed the app name and logo", "", "logo removed")
    return get()


def logo_file() -> tuple[bytes, str] | None:
    r = conn().execute("SELECT data, ctype FROM brand_logo WHERE id=1").fetchone()
    return (bytes(r["data"]), r["ctype"]) if r else None


# ------------------------------------------------------------------ the pages
def _ink(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#08130d" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def style_block(b: dict) -> str:
    """Overrides the accent colour in both the light and the dark theme (only when a colour was chosen)."""
    c = b["color"]
    if not c:
        return ""
    ink = _ink(c)
    light = f"--brand:{c};--brand-ink:{ink};--brand-soft:color-mix(in srgb,{c} 14%,#ffffff);"
    dark = f"--brand:color-mix(in srgb,{c} 78%,#ffffff);--brand-ink:#08130d;--brand-soft:color-mix(in srgb,{c} 26%,#111b15);"
    return (f"<style>:root{{{light}}}@media (prefers-color-scheme:dark){{:root:not([data-theme=\"light\"]){{{dark}}}}}"
            f":root[data-theme=\"dark\"]{{{dark}}}</style>")


def icon_href(b: dict) -> str:
    if b["logo"]:
        return b["logo"]
    fill = b["color"] or DEFAULT_COLOR
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='{fill}'/>"
           f"<text x='16' y='21' font-size='14' font-family='sans-serif' font-weight='700' text-anchor='middle' fill='white'>"
           f"{html.escape(b['mark'])}</text></svg>")
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe="/:='")


def logo_html(b: dict) -> str:
    if b["logo"]:
        return f'<div class="logo logo-img"><img src="{html.escape(b["logo"])}" alt=""></div>'
    return f'<div class="logo">{html.escape(b["mark"])}</div>'


def render(page: str, b: dict | None = None) -> str:
    """Fill the {{…}} placeholders of a page with this store's name, logo and colour."""
    b = b or get()
    return (page.replace("{{APP_NAME}}", html.escape(b["name"])).replace("{{LOGO_HTML}}", logo_html(b))
            .replace("{{ICON_HREF}}", html.escape(icon_href(b), quote=True)).replace("{{BRAND_STYLE}}", style_block(b)))
