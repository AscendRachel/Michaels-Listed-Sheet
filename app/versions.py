"""Version history: saved copies of the catalog that can be compared with today and restored, like Google Sheets.

Why: every edit is saved the moment it is made, so when someone forgets to start a work list there is nothing to go back
to. A version is a snapshot of every product row:
  * the app saves one by itself when something was edited (at most every 30 minutes), and before an import;
  * anyone can name the current catalog ("Before the price review") and a named version is never deleted by the app;
  * restoring first saves the catalog as it is now ("Before restoring ..."), so a restore can itself be undone.
What a restore puts back is what people type: the sheet's editable columns, manual overrides of calculated columns and
custom columns. It leaves what the scraper and the Michaels import fill in (prices, availability, titles) as they are.
Snapshots are compressed files in the data folder (versions/), listed in the `versions` table.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from datetime import datetime, timedelta

from . import catalog, db, protect, rules, sheet
from .db import conn, log_activity, now_str, tx

VERSIONS_DIR = db.DB_PATH.parent / "versions"

MIN_GAP_MINUTES = 30            # an automatic version is saved at most this often
KEEP_ALL_HOURS = 48             # automatic versions: all of them for this long, then one per day ...
KEEP_DAYS = 30                  # ... for this many days
MAX_NAMED = 200
PAGE = 200

# What a restore puts back (typed by people). Scraper and Michaels columns are left alone.
HUMAN = ("name", "category", "variation", "upc", "source_price", "weight", "inventory", "listed", "listing_status",
         "manual_status", "remarks", "link", "source", "title_ok")
BOOLS = {f for f in HUMAN if catalog.EDITABLE[f] is bool}
SHEET_KEY = {"title_ok": "title_check"}                  # the column's name in the sheet (used by column protection)
_cache: dict = {}


def _path(vid: int):
    return VERSIONS_DIR / f"v{int(vid)}.json.gz"


# ------------------------------------------------------------------ saving
def take(kind: str = "auto", name: str = "", by: str = "") -> dict:
    """Save the catalog as it is now. kind: auto | named | restore."""
    c = conn()
    cur = c.execute("SELECT * FROM products")
    cols = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    act = c.execute("SELECT COALESCE(MAX(id),0) FROM activity").fetchone()[0]
    data = gzip.compress(json.dumps({"cols": cols, "rows": rows}, separators=(",", ":"), default=str).encode(), 6)
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with tx() as t:
        vid = t.execute("INSERT INTO versions(at, kind, name, by, rows, activity_id, size) VALUES(?,?,?,?,?,?,?)",
                        (now_str(), kind, (name or "")[:120], (by or "")[:60], len(rows), act, len(data))).lastrowid
    try:
        tmp = _path(vid).with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(_path(vid))
    except OSError:
        with tx() as t:
            t.execute("DELETE FROM versions WHERE id=?", (vid,))
        raise
    return {"id": vid, "rows": len(rows)}


def _last(c):
    return c.execute("SELECT * FROM versions ORDER BY id DESC LIMIT 1").fetchone()


def take_if_edited(name: str = "", by: str = "", min_gap: int = 0) -> int | None:
    """Save a version only when something was edited since the last one (and at least min_gap minutes have passed)."""
    c = conn()
    last = _last(c)
    if not c.execute("SELECT 1 FROM products LIMIT 1").fetchone():
        return None
    if last:
        if c.execute("SELECT COALESCE(MAX(id),0) FROM activity").fetchone()[0] <= last["activity_id"]:
            return None
        if min_gap and datetime.now() - datetime.strptime(last["at"], "%Y-%m-%d %H:%M:%S") < timedelta(minutes=min_gap):
            return None
    return take("auto", name, by)["id"]


def prune() -> int:
    """Automatic versions older than KEEP_ALL_HOURS keep one per day; older than KEEP_DAYS go. Named versions stay."""
    c = conn()
    now = datetime.now()
    soft, hard = (now - timedelta(hours=KEEP_ALL_HOURS)).strftime("%Y-%m-%d %H:%M:%S"), (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    drop, newest_of_day = [], {}
    for r in c.execute("SELECT id, at FROM versions WHERE kind<>'named' ORDER BY id"):
        if r["at"] < hard:
            drop.append(r["id"])
        elif r["at"] < soft:
            day = r["at"][:10]
            if day in newest_of_day:
                drop.append(newest_of_day[day])
            newest_of_day[day] = r["id"]
    _remove(drop)
    return len(drop)


def _remove(ids) -> None:
    if not ids:
        return
    with tx() as c:
        c.executemany("DELETE FROM versions WHERE id=?", [(i,) for i in ids])
    for i in ids:
        try:
            _path(i).unlink()
        except OSError:
            pass
        if _cache.get("id") == i:
            _cache.clear()


def loop() -> None:
    time.sleep(60)                                       # let the server finish starting first
    while True:
        try:
            take_if_edited(min_gap=MIN_GAP_MINUTES)
            prune()
        except Exception as e:                           # never let one failure stop the history
            print(f"Version history failed: {e}", flush=True)
        time.sleep(600)


def start_background() -> None:
    threading.Thread(target=loop, daemon=True).start()


# ------------------------------------------------------------------ reading
def _meta(vid: int):
    r = conn().execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
    if not r:
        raise ValueError("That version wasn't found.")
    return r


def label_of(r) -> str:
    return r["name"] or {"auto": "Saved automatically", "restore": "Before a restore"}.get(r["kind"], "Version") + " · " + r["at"]


def _load(vid: int) -> dict:
    """{sku: product row as a dict} as it was in that version."""
    if _cache.get("id") == vid:
        return _cache["rows"]
    _meta(vid)
    try:
        d = json.loads(gzip.decompress(_path(vid).read_bytes()))
    except (OSError, ValueError):
        raise ValueError("The saved copy for that version is missing or damaged.")
    cols = d["cols"]
    rows = {r[0]: dict(zip(cols, r)) for r in d["rows"]}      # sku is the first column
    _cache.clear()
    _cache.update(id=vid, rows=rows)
    return rows


def listing() -> dict:
    c = conn()
    out = []
    for r in c.execute("SELECT * FROM versions ORDER BY id DESC LIMIT 400"):
        out.append({"id": r["id"], "at": r["at"], "kind": r["kind"], "name": r["name"], "by": r["by"], "rows": r["rows"],
                    "size": r["size"], "edits_since": c.execute("SELECT COUNT(*) FROM activity WHERE id>? AND sku<>''", (r["activity_id"],)).fetchone()[0]})
    return {"items": out, "keep_days": KEEP_DAYS, "keep_hours": KEEP_ALL_HOURS}


def _same(f: str, a, b) -> bool:
    if f in BOOLS:
        return bool(a) == bool(b)
    from .work import _norm
    return _norm(a) == _norm(b)


def _labels() -> dict:
    lab = {**catalog.FIELD_LABELS, **sheet.LABELS}
    lab.update({c["key"]: c["label"] for c in sheet.list_custom()})
    return lab


def _diff(then: dict, now: dict, custom: set[str], lab: dict) -> list[dict]:
    """The cells that differ between a saved row and the live row. `then` is what a restore puts back."""
    out = []
    for f in HUMAN:
        if not _same(f, then.get(f), now.get(f)):
            out.append({"f": f, "src": "col", "label": lab.get(f, f), "then": then.get(f), "now": now.get(f)})
    for src, allowed in (("ovr", sheet.OVERRIDABLE), ("extra", custom)):
        a, b = db.load_ovr(then.get(src)), db.load_ovr(now.get(src))
        for k in sorted((set(a) | set(b)) & set(allowed)):
            if not _same(k, a.get(k), b.get(k)):
                out.append({"f": k, "src": src, "label": lab.get(k, k), "then": a.get(k), "now": b.get(k)})
    return out


def _current() -> dict:
    return {r["sku"]: dict(r) for r in conn().execute("SELECT * FROM products")}


def _compare(vid: int) -> tuple[list[dict], list[dict], list[dict]]:
    """(changed, deleted_since, added_since) between that version and the catalog now."""
    then, now = _load(vid), _current()
    custom = {c["key"] for c in sheet.list_custom()}
    lab = _labels()
    changed, deleted, added = [], [], []
    for sku, t in then.items():
        n = now.get(sku)
        if n is None:
            deleted.append({"sku": sku, "name": t.get("name") or "", "kind": "deleted", "cells": []})
            continue
        cells = _diff(t, n, custom, lab)
        if cells:
            changed.append({"sku": sku, "name": n.get("name") or "", "kind": "changed", "cells": cells})
    for sku, n in now.items():
        if sku not in then:
            added.append({"sku": sku, "name": n.get("name") or "", "kind": "added", "cells": []})
    return changed, deleted, added


def _show(v):
    return "" if v is None else (v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))


def changes(vid: int, q: str = "", kind: str = "", offset: int = 0) -> dict:
    """What differs between that version and the catalog now: counts, plus one page of products."""
    m = _meta(vid)
    changed, deleted, added = _compare(vid)
    items = {"": changed + deleted + added, "changed": changed, "deleted": deleted, "added": added}.get(kind or "", changed + deleted + added)
    q = (q or "").strip().lower()
    if q:
        items = [i for i in items if q in i["sku"].lower() or q in i["name"].lower()]
    page = items[offset: offset + PAGE]
    for i in page:
        i["cells_total"] = len(i["cells"])
        i["cells"] = [{**x, "then": _show(x["then"]), "now": _show(x["now"])} for x in i["cells"][:12]]
    return {"version": {"id": m["id"], "at": m["at"], "name": m["name"], "kind": m["kind"]},
            "counts": {"changed": len(changed), "cells": sum(len(i["cells"]) for i in changed), "deleted": len(deleted), "added": len(added)},
            "total": len(items), "offset": offset, "items": page}


# ------------------------------------------------------------------ restoring
def restore(user: dict, vid: int, skus: list | None = None, everything: bool = False, remove_added: bool = False) -> dict:
    """Put the typed values (and deleted rows) of the chosen products back as they were in that version.
    skus=None with everything=True restores every difference. remove_added also removes products created since."""
    m = _meta(vid)
    changed, deleted, added = _compare(vid)
    if everything:
        pick_changed, pick_deleted = changed, deleted
        pick_added = [a["sku"] for a in added] if remove_added else []
    else:
        want = {str(s) for s in (skus or [])}
        if not want:
            raise ValueError("Choose at least one product to restore.")
        pick_changed = [i for i in changed if i["sku"] in want]
        pick_deleted = [i for i in deleted if i["sku"] in want]
        pick_added = [a["sku"] for a in added if a["sku"] in want]
    if not (pick_changed or pick_deleted or pick_added):
        return {"ok": True, "products": 0, "cells": 0, "brought_back": 0, "removed": 0, "protected": 0, "safety_id": None}
    safety = take("restore", f"Before restoring “{m['name'] or m['at']}”", user["name"])["id"]
    then = _load(vid)
    locks = protect.all_locks()
    cells = prot = back = 0
    with tx() as c:
        st = db.get_settings(c)
        pr, tf, tt = db.get_pricing(c), float(st["timeframe_days"]), float(st["title_threshold"])
        have = {r["name"] for r in c.execute("PRAGMA table_info(products)")}
        for item in pick_changed:
            sku = item["sku"]
            p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if not p:
                continue
            ovr, extra = db.load_ovr(p["ovr"]), db.load_ovr(p["extra"])
            sets, args, raw, shown = [], [], [], []
            for x in item["cells"]:
                if protect.why_blocked(user, SHEET_KEY.get(x["f"], x["f"]), locks):
                    prot += 1
                    continue
                f, v = x["f"], x["then"]
                if x["src"] == "col":
                    sets.append(f"{f}=?")
                    args.append(v)
                    if f == "link" and rules.asin_from_link(v or ""):
                        sets.append("asin=?")
                        args.append(rules.asin_from_link(v))
                else:
                    bag = ovr if x["src"] == "ovr" else extra
                    if v in (None, ""):
                        bag.pop(f, None)
                    else:
                        bag[f] = v
                raw.append({"f": "title_check" if f == "title_ok" else f, "o": x["now"], "n": v})
                shown.append(f"{x['label']}: {_show(x['now']) or '—'} → {_show(v) or '—'}")
            if not raw:
                continue
            sets += ["ovr=?", "extra=?"]
            args += [json.dumps(ovr) if ovr else None, json.dumps(extra) if extra else None]
            c.execute(f"UPDATE products SET {', '.join(sets)}, updated_at=?, updated_by=? WHERE sku=?", args + [now_str(), user["name"], sku])
            catalog._refresh_derived(c, sku, pr, tf)
            log_activity(c, user["name"], "restored a version", sku, "; ".join(shown), rules.issue_of(p, tt), db.issue_now(c, sku, tt), raw)
            cells += len(raw)
        for item in pick_deleted:
            sku = item["sku"]
            if c.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                continue
            row = {k: v for k, v in then[sku].items() if k in have}
            c.execute(f"INSERT INTO products({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
            c.execute("DELETE FROM deleted_products WHERE id=(SELECT MAX(id) FROM deleted_products WHERE sku=?)", (sku,))
            log_activity(c, user["name"], "restored", sku, f"from the version “{m['name'] or m['at']}”")
            back += 1
    removed = catalog.delete_products(pick_added, user["name"])["deleted"] if pick_added else 0
    with tx() as c:
        log_activity(c, user["name"], "restored a version", "",
                     f"“{m['name'] or m['at']}”: {len(pick_changed):,} products ({cells:,} cells), {back:,} brought back, {removed:,} removed")
    return {"ok": True, "products": len(pick_changed), "cells": cells, "brought_back": back, "removed": removed,
            "protected": prot, "safety_id": safety}


def rename(vid: int, name: str) -> dict:
    """Name a version (it is then kept for good); an empty name turns it back into an ordinary one."""
    m = _meta(vid)
    name = " ".join((name or "").split())[:120]
    with tx() as c:
        if name and m["kind"] != "named" and c.execute("SELECT COUNT(*) FROM versions WHERE kind='named'").fetchone()[0] >= MAX_NAMED:
            raise ValueError(f"You can keep up to {MAX_NAMED} named versions. Delete some you no longer need.")
        c.execute("UPDATE versions SET name=?, kind=? WHERE id=?", (name, "named" if name else ("auto" if m["kind"] == "named" else m["kind"]), vid))
    return {"ok": True}


def save_named(name: str, user: str) -> dict:
    name = " ".join((name or "").split())[:120]
    if not name:
        raise ValueError("Give this version a name.")
    if conn().execute("SELECT COUNT(*) FROM versions WHERE kind='named'").fetchone()[0] >= MAX_NAMED:
        raise ValueError(f"You can keep up to {MAX_NAMED} named versions. Delete some you no longer need.")
    r = take("named", name, user)
    with tx() as c:
        log_activity(c, user, "saved a version", "", f"“{name}”: {r['rows']:,} products")
    return {"ok": True, **r}


def delete(vid: int, user: str) -> dict:
    m = _meta(vid)
    _remove([vid])
    with tx() as c:
        log_activity(c, user, "deleted a version", "", label_of(m))
    return {"ok": True}
