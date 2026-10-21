"""Logins, sessions and the scraper key.

Password hashing is PBKDF2-SHA256 with the same parameters as the earlier version,
so existing user accounts keep working after the upgrade.
"""

from __future__ import annotations

import hashlib
import os
import hmac
import re
import secrets
import time

from fastapi import Depends, HTTPException, Request

from . import db

COOKIE = os.environ.get("GT_COOKIE", "gt_session")      # give every app on one server its own name, so their logins never mix
SESSION_DAYS = 30
FAILED: dict[str, list[float]] = {}     # ip -> failed sign-in timestamps


def hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()


def user_count() -> int:
    return db.conn().execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0]


def add_user(username: str, name: str, password: str, role: str = "member") -> str:
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._\-]{3,30}", username):
        raise ValueError("Username: 3-30 letters, numbers, dots, dashes or underscores.")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in ("admin", "member"):
        raise ValueError("Role must be admin or member.")
    salt = secrets.token_hex(16)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("That username is taken.")
        c.execute("INSERT INTO users(username, name, pw_hash, salt, role, created_at) VALUES(?,?,?,?,?,?)",
                  (username, (name or username).strip()[:60], hash_pw(password, salt), salt, role, db.now_str()))
    return username


def set_password(username: str, password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    salt = secrets.token_hex(16)
    with db.tx() as c:
        n = c.execute("UPDATE users SET pw_hash=?, salt=? WHERE username=?",
                      (hash_pw(password, salt), salt, username)).rowcount
        c.execute("DELETE FROM sessions WHERE username=?", (username,))
    if not n:
        raise ValueError("No such user.")


def check_login(username: str, password: str):
    u = db.conn().execute("SELECT * FROM users WHERE username=? AND disabled=0",
                          ((username or "").strip().lower(),)).fetchone()
    if not u:
        hash_pw(password or "x", "00" * 16)       # same amount of work whether or not the user exists
        return None
    return u if hmac.compare_digest(u["pw_hash"], hash_pw(password or "", u["salt"])) else None


def new_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO sessions(token, username, expires) VALUES(?,?,?)",
                  (token, username, time.time() + SESSION_DAYS * 86400))
    return token


def session_user(token: str | None):
    if not token:
        return None
    r = db.conn().execute("""SELECT u.username, u.name, u.role FROM sessions s JOIN users u ON u.username=s.username
        WHERE s.token=? AND s.expires>? AND u.disabled=0""", (token, time.time())).fetchone()
    return dict(r) if r else None


def _key_prefix() -> str:
    """The store's short mark in lower case (CTR -> ctr_), so a key shows which store it belongs to."""
    from . import brand
    mark = re.sub(r"[^a-z0-9]", "", brand.get()["mark"].lower())
    return (mark or "key") + "_"


def agent_key() -> str:
    c = db.conn()
    row = c.execute("SELECT value FROM settings WHERE key='agent_key'").fetchone()
    if row:
        return row["value"]
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('agent_key', ?)",
                  (_key_prefix() + secrets.token_urlsafe(24),))
    return db.conn().execute("SELECT value FROM settings WHERE key='agent_key'").fetchone()["value"]


def rotate_agent_key() -> str:
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key='agent_key'")
    return agent_key()


# ------------------------------------------------------------------ request helpers
def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def is_local_direct(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and not request.headers.get("x-forwarded-for")


def cookie_kwargs(request: Request, max_age: int) -> dict:
    https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
    return dict(key=COOKIE, max_age=max_age, path="/", httponly=True, samesite="lax", secure=https)


def current_user(request: Request) -> dict:
    u = session_user(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Please sign in.")
    return u


def admin_user(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Only admins can do this.")
    return u


def agent_ok(request: Request) -> None:
    if not hmac.compare_digest(request.headers.get("x-agent-key") or "", agent_key()):
        raise HTTPException(403, "Wrong scraper key. Copy it from Scraper > Settings in the app.")
