"""Column protection, like "Protect range" in Google Sheets.

An admin can protect any column and choose who may edit it:
  nobody  - only imports and the scraper write it (nobody can type in it)
  admins  - only admins can edit it
  people  - admins plus the team members the admin picked
Imports, the scraper and the other automatic jobs are never blocked: protection is about people typing.
The three Michaels columns start out protected with "nobody" (the Michaels download listing owns them); an admin can
change that like any other column.
"""

from __future__ import annotations

import json

from . import db
from .db import conn, log_activity, now_str, tx

MODES = ("nobody", "admins", "people")
DEFAULTS = {
    "michaels_sku_number": "From the Michaels download listing. Only a new import changes it.",
    "michaels_price": "From the Michaels download listing. Only a new import changes it.",
    "michaels_qty": "From the Michaels download listing. Only a new import changes it.",
}


def seed(c=None) -> None:
    """Put the default protections in once. After that an admin's choices (including removing them) are kept."""
    with tx() as t:
        if (db.get_settings(t).get("locks_seeded") or "") == "1":
            return
        for col, note in DEFAULTS.items():
            t.execute("INSERT OR IGNORE INTO column_locks(col, mode, users, note, by, at) VALUES(?,?,?,?,?,?)",
                      (col, "nobody", "[]", note, "system", now_str()))
        db.set_setting(t, "locks_seeded", "1")


def all_locks(c=None) -> dict:
    c = c or conn()
    out = {}
    for r in c.execute("SELECT col, mode, users, note, by, at FROM column_locks"):
        try:
            users = json.loads(r["users"] or "[]")
        except ValueError:
            users = []
        out[r["col"]] = {"mode": r["mode"], "users": users, "note": r["note"] or "", "by": r["by"], "at": r["at"]}
    return out


def known_columns(c=None) -> set[str]:
    from . import catalog, sheet
    c = c or conn()
    keys = set(catalog.EDITABLE) | set(sheet.OVERRIDABLE) | {"sku", "title_check"}
    keys.discard("title_ok")
    keys |= {r["key"] for r in c.execute("SELECT key FROM custom_columns")}
    return keys


def set_lock(col: str, mode: str, users: list, note: str, admin_name: str) -> dict:
    col = str(col or "")
    if col not in known_columns():
        raise ValueError("That column can't be protected.")
    with tx() as c:
        if mode in ("", "none", None):
            c.execute("DELETE FROM column_locks WHERE col=?", (col,))
            log_activity(c, admin_name, "removed the protection of a column", "", col)
        else:
            if mode not in MODES:
                raise ValueError("Choose who may edit this column.")
            valid = {r["username"] for r in c.execute("SELECT username FROM users WHERE COALESCE(disabled,0)=0")}
            picked = sorted({str(u) for u in (users or []) if str(u) in valid}) if mode == "people" else []
            c.execute("INSERT INTO column_locks(col, mode, users, note, by, at) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(col) DO UPDATE SET mode=excluded.mode, users=excluded.users, note=excluded.note, by=excluded.by, at=excluded.at",
                      (col, mode, json.dumps(picked), str(note or "").strip()[:200], admin_name, now_str()))
            log_activity(c, admin_name, "protected a column", "", f"{col}: {mode}" + (f" ({', '.join(picked)})" if picked else ""))
    return all_locks()


# ------------------------------------------------------------------ who may edit
def why_blocked(actor: dict, col: str, locks: dict | None = None) -> str:
    """'' when this person may edit the column, otherwise the reason (shown to them)."""
    lock = (locks if locks is not None else all_locks()).get(col)
    if not lock:
        return ""
    admin = actor.get("role") == "admin"
    if lock["mode"] == "admins":
        return "" if admin else "This column is protected: only admins can edit it."
    if lock["mode"] == "people":
        return "" if admin or actor.get("username") in lock["users"] else "This column is protected: only admins and the people an admin chose can edit it."
    note = f" {lock['note']}" if lock.get("note") else ""
    return "This column is protected: nobody can type in it." + note


def filter_edits(actor: dict, edits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split cell edits into the ones this person may make and error rows for the ones they may not."""
    locks = all_locks()
    if not locks:
        return edits, []
    keep, errors, memo = [], [], {}
    for e in edits:
        f = e.get("field")
        if f not in memo:
            memo[f] = why_blocked(actor, f, locks)
        if memo[f]:
            errors.append({"sku": str(e.get("sku") or ""), "field": f, "error": memo[f]})
        else:
            keep.append(e)
    return keep, errors


def require(actor: dict, fields) -> None:
    """Raise if any of these columns is protected against this person (used where a whole action is refused)."""
    locks = all_locks()
    for f in fields:
        msg = why_blocked(actor, f, locks)
        if msg:
            raise ValueError(f"{msg} ({f})")


def present(v) -> bool:
    return v not in (None, "", False, 0, "0")
