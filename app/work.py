"""Work lists: a saved, frozen list of rows someone is working through (for example every High Price row).

Why: while an issue is being resolved row by row, a row that stops matching the filter would normally drop out of the
view, and after a power cut or a reload nobody knows which rows were already done. A work list is saved on the server:
  * the rows are frozen when the list is started, so they stay (marked) after they are fixed, even after a reload;
  * "edited" = rows this person changed since the list started (read from the activity log, nothing extra to record);
  * "resolved" = frozen rows that no longer match the filter the list was started from;
  * the list only closes when the person presses Finish; if rows still have the issue they must choose
    to keep working or to finish anyway (those rows are recorded as skipped).
"""

from __future__ import annotations

import json

from . import catalog, db, protect, sheet
from .db import conn, log_activity, now_str, tx

MAX_ROWS = 60000


def _own(c, list_id: int, user: dict):
    r = c.execute("SELECT * FROM worklists WHERE id=? AND username=?", (list_id, user["username"])).fetchone()
    if not r:
        raise ValueError("That work list wasn't found.")
    return r


def _progress(c, r) -> dict:
    skus = json.loads(r["skus"])
    params = json.loads(r["params"] or "{}")
    keep = set(sheet._still_matching(c, skus, params)) if params else set(skus)       # rows that still have the issue
    mine = {x[0] for x in c.execute("SELECT DISTINCT sku FROM activity WHERE user=? AND at>=? AND sku<>''", (r["user_name"], r["created_at"]))}
    edited = mine & set(skus)
    open_ = [s for s in skus if s in keep]
    resolved = [s for s in skus if s not in keep]
    return {"total": len(skus), "edited": len(edited), "edited_skus": sorted(edited), "open": len(open_), "resolved": len(resolved),
            "resolved_skus": resolved, "open_skus": open_}


def start(user: dict, name: str, params: dict, skus: list) -> dict:
    skus = [str(s) for s in dict.fromkeys(skus)][:MAX_ROWS]
    if not skus:
        raise ValueError("There are no rows in this view to work through.")
    name = (str(name or "").strip() or "Work list")[:80]
    keep = {k: v for k, v in (params or {}).items() if (k in ("q", "queue", "sort", "color") or str(k).startswith("cf_")) and v}
    with tx() as c:
        cur = c.execute("INSERT INTO worklists(username, user_name, name, params, skus, created_at, state) VALUES(?,?,?,?,?,?, 'open')",
                        (user["username"], user["name"], name, json.dumps(keep), json.dumps(skus), now_str()))
        log_activity(c, user["name"], "started a work list", "", f"{name}: {len(skus):,} rows")
        return {"ok": True, "id": cur.lastrowid, "name": name, "total": len(skus)}


def mine(user: dict) -> list[dict]:
    """My unfinished lists with their progress (shown after a reload or a power cut so they can be resumed)."""
    c = conn()
    out = []
    for r in c.execute("SELECT * FROM worklists WHERE username=? AND state='open' ORDER BY id DESC LIMIT 20", (user["username"],)):
        p = _progress(c, r)
        out.append({"id": r["id"], "name": r["name"], "created_at": r["created_at"], "total": p["total"], "edited": p["edited"],
                    "resolved": p["resolved"], "open": p["open"]})
    return out


def load(user: dict, list_id: int) -> dict:
    c = conn()
    r = _own(c, list_id, user)
    if r["state"] != "open":
        raise ValueError("That work list is already finished.")
    p = _progress(c, r)
    return {"id": r["id"], "name": r["name"], "params": json.loads(r["params"] or "{}"), "created_at": r["created_at"],
            "skus": json.loads(r["skus"]), "resolved": p["resolved_skus"], "edited": p["edited_skus"],
            "total": p["total"], "open": p["open"]}


def finish(user: dict, list_id: int, force: bool = False) -> dict:
    """Confirm the list is done. If rows still have the issue, ask first (force = finish anyway; they are recorded as skipped)."""
    with tx() as c:
        r = _own(c, list_id, user)
        if r["state"] != "open":
            raise ValueError("That work list is already finished.")
        p = _progress(c, r)
        if p["open"] and not force:
            return {"ok": False, "unresolved": p["open"], "total": p["total"], "open_skus": p["open_skus"][:5000]}
        summary = {"total": p["total"], "edited": p["edited"], "resolved": p["resolved"], "skipped": p["open"], "forced": bool(p["open"])}
        c.execute("UPDATE worklists SET state='finished', finished_at=?, summary=? WHERE id=?", (now_str(), json.dumps(summary), list_id))
        log_activity(c, user["name"], "finished a work list", "",
                     f"{r['name']}: {p['total']:,} rows, {p['resolved']:,} resolved, {p['edited']:,} edited"
                     + (f", {p['open']:,} still open (finished anyway)" if p["open"] else ", all resolved"))
        return {"ok": True, **summary}


# ------------------------------------------------------------------ undoing a person's changes
# Every edit is saved the moment it is made (other people use the same catalog), so "discard" has to undo them one by
# one. The activity log keeps the old and new value of every cell change, so the undo puts back the value the cell had
# when the list was started, but only where the cell still holds what THIS person set: a cell someone else (or the
# scraper) has changed since is left alone and reported.
NOT_UNDONE = {"sku", "title_check"}


def _norm(v):
    if v is None or v == "":
        return ""
    if isinstance(v, (bool, int, float)):
        return round(float(v), 6)
    s = str(v).strip()
    try:
        return round(float(s.replace("$", "").replace(",", "")), 6)
    except ValueError:
        return s


def _plan(c, r) -> tuple[list, list, int]:
    """(cells to put back as [(sku, field, value_at_start)], cells that changed again since, edits with no record to undo)."""
    skus = set(json.loads(r["skus"]))
    events: dict = {}
    unrecorded = 0
    for a in c.execute("""SELECT sku, changes, action FROM activity WHERE user=? AND at>=? AND sku<>''
                          AND action NOT IN ('added','deleted','restored') ORDER BY id ASC""", (r["user_name"], r["created_at"])):
        if a["sku"] not in skus:
            continue
        if not a["changes"]:
            unrecorded += 1
            continue
        for ch in json.loads(a["changes"]):
            if ch.get("f") not in NOT_UNDONE:
                events.setdefault((a["sku"], ch["f"]), []).append((ch.get("o"), ch.get("n")))
    current: dict = {}
    names = sorted({s for s, _ in events})
    for i in range(0, len(names), 300):
        for row in sheet.rows(names[i:i + 300]):
            current[row["sku"]] = row
    todo, conflicts = [], []
    for (sku, f), seq in events.items():
        row = current.get(sku)
        if row is None:                                              # deleted since: nothing to put back
            continue
        has_value = f not in sheet.OVERRIDABLE or f in row.get("_ovr", [])      # a typed override only counts if it is still typed
        cur = row.get(f) if has_value else None
        first_old, last_new = seq[0][0], seq[-1][1]
        if _norm(cur) == _norm(first_old):
            continue                                                 # already as it was at the start
        if _norm(cur) == _norm(last_new):
            todo.append((sku, f, first_old))
        else:
            conflicts.append((sku, f))
    return todo, conflicts, unrecorded


def revert_preview(user: dict, list_id: int) -> dict:
    c = conn()
    todo, conflicts, unrecorded = _plan(c, _own(c, list_id, user))
    return {"cells": len(todo), "products": len({t[0] for t in todo}), "conflicts": len(conflicts), "unrecorded": unrecorded}


def discard(user: dict, list_id: int, revert: bool = False) -> dict:
    """Close a work list without confirming it. With revert=True the changes this person made to its rows are undone first."""
    out = {"ok": True, "undone": 0, "products": 0, "left_alone": 0, "protected": 0}
    if revert:
        c = conn()
        r = _own(c, list_id, user)
        todo, conflicts, _ = _plan(c, r)
        edits = [{"sku": s, "field": f, "value": "" if _norm(v) == "" else ("TRUE" if _norm(v) == 1.0 else "FALSE") if catalog.EDITABLE.get(f) is bool
                  else ("" if v is None else v)} for s, f, v in todo]
        keep, blocked = protect.filter_edits(user, edits)
        res = sheet.apply_cells(keep, user["name"], None, action="undid a work list") if keep else {"changed": 0, "error_count": 0}
        out.update({"undone": len(keep) - res["error_count"], "products": res["changed"],
                    "left_alone": len(conflicts) + res["error_count"], "protected": len(blocked)})
    with tx() as c:
        r = _own(c, list_id, user)
        c.execute("UPDATE worklists SET state='discarded', finished_at=?, summary=? WHERE id=?", (now_str(), json.dumps(out), list_id))
        log_activity(c, user["name"], "discarded a work list", "",
                     r["name"] + (f": undid {out['undone']:,} changes in {out['products']:,} products, left {out['left_alone']:,} alone" if revert else ""))
    return out
