"""Spreadsheet-style access to the catalog: a frozen list of SKUs (the "view"), rows on demand,
cell edits in batches with undo information, custom columns and one-click bulk formulas.

The view is a snapshot: after you edit rows they stay in the view even if they no longer match the
filter (like a Google Sheets filter view). `refresh` simply asks for the SKU list again.

Every column can be edited:
  * stored columns (name, link, price, availability ...) are simply saved;
  * calculated columns (fee, selling price, status ...) take a manual override, like typing over a formula
    in Sheets. A blank cell removes the override and the calculation takes over again;
  * custom columns added by an admin keep their values per product;
  * SKU renames the product everywhere (history, activity, scraper tasks).
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from . import catalog, db, rules
from .db import conn, log_activity, now_str, tx
from .rules import to_float

MAX_ROWS_PER_REQUEST = 400

DIRECT = set(catalog.EDITABLE) - {"title_ok"}                      # saved as typed
OVR_NUM = {"fee", "cost", "selling", "auto_price", "sold_total"}
OVR_PCT = {"markup_pct"}
OVERRIDABLE = OVR_NUM | OVR_PCT | {"status"}            # calculated columns that accept an override
SPECIAL = {"sku", "title_check"}
LABELS = {"fee": "3PL fee", "cost": "Source + 3PL fee", "selling": "Selling price", "markup_pct": "Markup %",
          "auto_price": "Automation price", "status": "Source status", "sold_total": "Total sold",
          "sku": "SKU", "title_check": "Title check"}
COLUMN_TYPES = ("text", "number", "checkbox", "date")
SKU_RE = re.compile(r"[A-Za-z0-9._\-]+")


# ------------------------------------------------------------------ the view
def sku_list(q: dict) -> dict:
    """Every SKU matching the filters, in sort order: the frozen view."""
    where, binds = catalog._where(q)
    key = q.get("sort", "")
    expr = catalog.sort_expr(key) if key else None
    order = (f"({expr}) IS NULL, {expr} {'DESC' if key.startswith('-') else 'ASC'}, row_order" if expr else "row_order")
    skus = [r[0] for r in conn().execute(f"SELECT sku FROM products WHERE {where} ORDER BY {order}", binds)]
    return {"skus": skus, "total": len(skus), "taken_at": now_str()}


def title_summary(p, threshold: float) -> str:
    """One line for the TITLE CHECK column: what the title check concluded and why."""
    if not p["title"]:
        return ""
    s, accepted = p["title_score"], (p["title_ok"] or "") == p["title"]
    bad = bool(p["title_conflict"]) or (s is not None and s < threshold)
    if accepted and bad:
        return "Accepted by the team"
    if p["title_conflict"]:
        return "Listing changed: " + (p["title_note"] or "")
    if s is not None and s < threshold:
        return f"Low match {s}%" + (": " + p["title_note"] if p["title_note"] else "")
    return f"OK {s}%" if s is not None else ""


def _row(p, pr: dict, tt: float = 50.0) -> dict:
    d = catalog._decorate(p)
    d["title_check"] = title_summary(p, tt)
    ovr, extra = db.load_ovr(d.pop("ovr", None)), db.load_ovr(d.pop("extra", None))
    cost = (p["source_price"] + p["fee"]) if p["source_price"] is not None and p["fee"] is not None else None
    selling = cost * pr["markup"] if cost is not None else None
    d["cost"] = round(cost, 2) if cost is not None else None
    d["selling"] = round(selling, 2) if selling is not None else None
    d["markup_pct"] = round((selling - cost) / cost, 4) if cost else None
    d["sold_total"] = (p["sold_2025"] or 0) + (p["sold_2026"] or 0)
    for k, v in ovr.items():                 # manual overrides win over the calculation
        d[k] = v
    d["_ovr"] = sorted(ovr)
    for k, v in extra.items():               # custom columns
        d[k] = v
    return d


def rows(skus: list[str]) -> list[dict]:
    skus = skus[:MAX_ROWS_PER_REQUEST]
    if not skus:
        return []
    c = conn()
    pr = db.get_pricing(c)
    marks = ",".join("?" * len(skus))
    tt = float(db.get_settings(c).get("title_threshold") or 50)
    got = {r["sku"]: r for r in c.execute(f"SELECT {catalog.LIST_COLS} FROM products WHERE sku IN ({marks})", skus)}
    return [_row(got[s], pr, tt) for s in skus if s in got]


def _still_matching(c, skus: list[str], q: dict) -> set[str]:
    where, binds = catalog._where(q)
    out: set[str] = set()
    for i in range(0, len(skus), 500):
        chunk = skus[i:i + 500]
        named = {f"s{j}": s for j, s in enumerate(chunk)}      # named binds only: they can't mix with positional ones
        sql = f"SELECT sku FROM products WHERE ({where}) AND sku IN ({','.join(':s' + str(j) for j in range(len(chunk)))})"
        out.update(r[0] for r in c.execute(sql, {**binds, **named}))
    return out


# ------------------------------------------------------------------ custom columns
def list_custom() -> list[dict]:
    return [dict(r) for r in conn().execute("SELECT key, label, type, after FROM custom_columns ORDER BY created_at, rowid")]


def add_column(label: str, ctype: str, after: str | None, user: str) -> dict:
    label = re.sub(r"\s+", " ", (label or "").strip())[:40]
    if not label:
        raise ValueError("Give the column a name.")
    if ctype not in COLUMN_TYPES:
        raise ValueError("Column type must be text, number, checkbox or date.")
    base = "x_" + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30] or "col")
    with tx() as c:
        if c.execute("SELECT 1 FROM custom_columns WHERE LOWER(label)=LOWER(?)", (label,)).fetchone():
            raise ValueError("A custom column with that name already exists.")
        key, n = base, 2
        while c.execute("SELECT 1 FROM custom_columns WHERE key=?", (key,)).fetchone():
            key, n = f"{base}_{n}", n + 1
        c.execute("INSERT INTO custom_columns(key, label, type, after, created_by, created_at) VALUES(?,?,?,?,?,?)",
                  (key, label, ctype, (after or "")[:60] or None, user, now_str()))
        log_activity(c, user, "added a column", "", f"{label} ({ctype})")
    return {"ok": True, "key": key, "columns": list_custom()}


def delete_column(key: str, user: str) -> dict:
    with tx() as c:
        col = c.execute("SELECT * FROM custom_columns WHERE key=?", (key,)).fetchone()
        if not col:
            raise ValueError("That column doesn't exist, or it's one of the sheet's built-in columns "
                             "(built-in columns can be hidden, not deleted).")
        c.execute("UPDATE custom_columns SET after=? WHERE after=?", (col["after"], key))       # keep the others in place
        c.execute("DELETE FROM custom_columns WHERE key=?", (key,))
        n = c.execute("""UPDATE products SET extra=json_remove(extra, '$.' || ?) WHERE extra IS NOT NULL
            AND json_extract(extra, '$.' || ?) IS NOT NULL""", (key, key)).rowcount
        c.execute("UPDATE products SET extra=NULL WHERE extra='{}'")
        log_activity(c, user, "deleted a column", "", f"{col['label']} ({n:,} values removed)")
    return {"ok": True, "columns": list_custom(), "values_removed": n}


# ------------------------------------------------------------------ parsing typed values
def _num(v, what: str):
    if v is None or str(v).strip() == "":
        return None
    n = to_float(str(v).replace("%", ""))
    if n is None:
        raise ValueError(f"{what}: enter a number.")
    return n


def _pct(v):
    if v is None or str(v).strip() == "":
        return None
    n = _num(v, "Markup %")
    return n / 100 if "%" in str(v) or abs(n) > 5 else n


def _date(v):
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("Dates look like 2026-03-31 or 3/31/2026.")


def _custom_value(ctype: str, v, label: str):
    """The value to store for a custom column cell, or None to remove it."""
    if v is None or str(v).strip() == "":
        return None
    if ctype == "number":
        return _num(v, label)
    if ctype == "checkbox":
        return 1 if v is True or str(v).strip().upper() in ("1", "TRUE", "YES", "Y", "X", "CHECKED") else 0
    if ctype == "date":
        return _date(v)
    return str(v).strip()[:500]


# ------------------------------------------------------------------ saving cells
def apply_cells(edits: list[dict], user: str, view_filter: dict | None = None, action: str = "edited in the sheet") -> dict:
    """edits: [{sku, field, value}]. Bad cells are reported and skipped; good ones are saved together.
    Returns what changed, the previous values (for undo), refreshed rows, which rows left the filter
    and any SKU renames."""
    by_sku: dict[str, dict] = {}
    for e in edits:
        by_sku.setdefault(str(e.get("sku") or ""), {})[e.get("field")] = e.get("value")
    errors: list[dict] = []
    before: list[dict] = []
    changed_skus: list[str] = []
    renamed: dict[str, str] = {}
    with tx() as c:
        st = db.get_settings(c)
        pr, tf, tt = db.get_pricing(c), float(st["timeframe_days"]), float(st["title_threshold"])
        custom = {r["key"]: r for r in c.execute("SELECT key, label, type FROM custom_columns")}
        for sku, patch in by_sku.items():
            p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if not p:
                errors.append({"sku": sku, "field": "", "error": "Product not found."})
                continue
            direct: dict = {}
            ovr, extra = db.load_ovr(p["ovr"]), db.load_ovr(p["extra"])
            ovr0, extra0 = dict(ovr), dict(extra)
            new_sku = None
            notes: list[str] = []
            raw: list = []                       # old/new value per cell, for Show edit history
            bad = False
            for field, value in patch.items():
                try:
                    if field == "title_check":
                        v = str(value or "").strip().lower()
                        if v in ("ok", "fine", "accept", "accepted", "same", "yes", "true"):
                            direct["title_ok"] = p["title"] or ""         # this Amazon title is the same item
                        elif v == "":
                            direct["title_ok"] = ""                       # take the approval back: review it again
                        else:
                            raise ValueError("Type OK to accept this Amazon title as the same item, or clear the cell to review it again.")
                    elif field == "sku":
                        new_sku = str(value or "").strip()
                    elif field in DIRECT:
                        direct[field] = value
                    elif field in OVERRIDABLE:
                        if value is None or str(value).strip() == "":
                            ovr.pop(field, None)
                        elif field == "status":
                            s = str(value).strip()
                            match = next((x for x in rules.STATUSES if x.lower() == s.lower()), None)
                            if not match:
                                raise ValueError("Status must be one of: " + ", ".join(rules.STATUSES) + ".")
                            ovr[field] = match
                        elif field in OVR_PCT:
                            ovr[field] = _pct(value)
                        else:
                            ovr[field] = _num(value, LABELS[field])
                    elif field in custom:
                        v = _custom_value(custom[field]["type"], value, custom[field]["label"])
                        if v and custom[field]["type"] == "text":
                            v = catalog.dropdown_value(c, field, v, extra.get(field), custom[field]["label"])
                        if v is None:
                            extra.pop(field, None)
                        else:
                            extra[field] = v
                    else:
                        raise ValueError("This column can't be edited here.")
                except ValueError as ex:
                    errors.append({"sku": sku, "field": field, "error": str(ex)})
                    bad = True
            if bad:
                # a row with a bad cell is skipped as a whole so a half-saved paste can't happen
                continue
            try:
                changes, sets, args = catalog._apply_patch(c, p, direct, raw)
            except ValueError as ex:
                errors.append({"sku": sku, "field": ",".join(direct), "error": str(ex)})
                continue
            if new_sku is not None and new_sku != sku:
                if not SKU_RE.fullmatch(new_sku):
                    errors.append({"sku": sku, "field": "sku", "error": "Use only letters, numbers, dots, dashes and underscores."})
                    continue
                if c.execute("SELECT 1 FROM products WHERE LOWER(sku)=LOWER(?) AND sku<>?", (new_sku, sku)).fetchone():
                    errors.append({"sku": sku, "field": "sku", "error": f"{new_sku} already exists."})
                    continue
            else:
                new_sku = None
            ovr_changed, extra_changed = ovr != ovr0, extra != extra0
            if not (sets or ovr_changed or extra_changed or new_sku):
                continue
            if ovr_changed:
                for k in set(ovr) | set(ovr0):
                    if ovr.get(k) != ovr0.get(k):
                        raw.append({"f": k, "o": ovr0.get(k), "n": ovr.get(k)})
                        before.append({"sku": sku, "field": k, "value": ovr0.get(k)})
                        notes.append(f"{LABELS.get(k, k)}: {'typed ' + str(ovr[k]) if k in ovr else 'back to the formula'}")
            if extra_changed:
                for k in set(extra) | set(extra0):
                    if extra.get(k) != extra0.get(k):
                        raw.append({"f": k, "o": extra0.get(k), "n": extra.get(k)})
                        before.append({"sku": sku, "field": k, "value": extra0.get(k)})
                        notes.append(f"{custom[k]['label'] if k in custom else k}: {extra0.get(k, '—')} → {extra.get(k, '—')}")
            for f in direct:
                if f in catalog.EDITABLE and (p[f] if p[f] is not None else "") != catalog._clean(f, direct[f]):
                    before.append({"sku": sku, "field": f, "value": p[f]})
            sets = list(sets)
            if ovr_changed:
                sets.append("ovr=?")
                args.append(json.dumps(ovr) if ovr else None)
            if extra_changed:
                sets.append("extra=?")
                args.append(json.dumps(extra) if extra else None)
            if sets:
                c.execute(f"UPDATE products SET {', '.join(sets)}, updated_at=?, updated_by=? WHERE sku=?",
                          args + [now_str(), user, sku])
            catalog._refresh_derived(c, sku, pr, tf)
            final = sku
            if new_sku:
                for table in ("products", "history", "tasks", "activity"):
                    c.execute(f"UPDATE {table} SET sku=? WHERE sku=?", (new_sku, sku))
                for b in before:
                    if b["sku"] == sku:
                        b["sku"] = new_sku
                before.append({"sku": new_sku, "field": "sku", "value": sku})
                renamed[sku] = new_sku
                final = new_sku
                notes.append(f"SKU: {sku} → {new_sku}")
                raw.append({"f": "sku", "o": sku, "n": new_sku})
            log_activity(c, user, action, final, "; ".join(changes + notes), rules.issue_of(p, tt), db.issue_now(c, final, tt), raw)
            changed_skus.append(final)
    fresh = rows(changed_skus[:MAX_ROWS_PER_REQUEST])
    left = []
    if view_filter is not None and changed_skus:
        keep = _still_matching(conn(), changed_skus, view_filter)
        left = [s for s in changed_skus if s not in keep]
    return {"ok": True, "changed": len(changed_skus), "before": before, "rows": fresh, "left_view": left,
            "renamed": renamed, "errors": errors[:50], "error_count": len(errors), "updated_skus": changed_skus}


def use_amazon_price(skus: list[str], user: str, view_filter: dict | None = None) -> dict:
    """Copy each product's latest Amazon price (SCRAP PRICE) into SOURCE PRICE."""
    c = conn()
    edits, skipped = [], 0
    for i in range(0, len(skus), 500):
        chunk = skus[i:i + 500]
        for r in c.execute(f"SELECT sku, price_num FROM products WHERE sku IN ({','.join('?' * len(chunk))})", chunk):
            if r["price_num"] and r["price_num"] > 0:
                edits.append({"sku": r["sku"], "field": "source_price", "value": r["price_num"]})
            else:
                skipped += 1
    res = apply_cells(edits, user, view_filter, action="used the Amazon price as source price")
    done = set(res["updated_skus"])
    res["after"] = [e for e in edits if e["sku"] in done]          # for redo
    res["skipped_no_price"] = skipped
    return res




# ------------------------------------------------------------------ edit history of one cell
SCRAPER_FIELDS = {"price": "price", "availability": "availability", "delivery": "delivery", "status": "status", "title": "title"}


def _show(field: str, v) -> str:
    if v is None or v == "":
        return ""
    if catalog.EDITABLE.get(field) is bool or field in ("listed", "inventory"):
        return "TRUE" if v in (1, True, "1", "TRUE", "true") else "FALSE"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def cell_history(sku: str, field: str) -> dict:
    """Who changed one cell and when, newest first: people's edits (from the activity log), plus what the scraper
    changed for the columns it fills. Edits made before changes were recorded per cell are read from their text."""
    c = conn()
    p = c.execute("SELECT sku, name FROM products WHERE sku=?", (sku,)).fetchone()
    if not p:
        raise ValueError("Product not found.")
    custom = {r["key"]: r["label"] for r in c.execute("SELECT key, label FROM custom_columns")}
    key = "title_ok" if field == "title_check" else field
    labels = {x.lower() for x in (catalog.FIELD_LABELS.get(key), LABELS.get(field), custom.get(field)) if x}
    events: list[dict] = []
    for a in c.execute("SELECT at, user, action, detail, changes FROM activity WHERE sku=? ORDER BY id DESC LIMIT 800", (sku,)):
        done = False
        if a["changes"]:
            try:
                for ch in json.loads(a["changes"]):
                    if ch.get("f") == field:
                        events.append({"at": a["at"], "who": a["user"], "action": a["action"], "old": _show(field, ch.get("o")),
                                       "new": _show(field, ch.get("n")), "kind": "edit"})
                done = True
            except ValueError:
                pass
        if not done and a["detail"] and labels:                      # older entries: "Label: old → new; Label2: …"
            for seg in str(a["detail"]).split("; "):
                name, _, rest = seg.partition(": ")
                if name.lower() in labels and rest:
                    old, arrow, new = rest.partition(" → ")
                    events.append({"at": a["at"], "who": a["user"], "action": a["action"], "old": "" if not arrow else old.replace("—", ""),
                                   "new": (new if arrow else rest).replace("—", ""), "kind": "edit"})
    col = SCRAPER_FIELDS.get(field)
    if col:
        prev = None
        for h in c.execute(f"SELECT at, agent, {col} AS v FROM history WHERE sku=? ORDER BY id ASC", (sku,)):
            v = (h["v"] or "").strip()
            if field == "title" and not v:
                continue
            if prev is not None and v != prev:
                events.append({"at": h["at"], "who": f"Scraper ({h['agent'] or 'a scraper PC'})", "action": "Amazon check",
                               "old": prev, "new": v, "kind": "scraper"})
            prev = v
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"sku": sku, "name": p["name"], "field": field, "events": events[:200]}
