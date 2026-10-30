"""Product catalog: queues, search, editing, adding, pricing rules and the activity feed.

Queues are plain SQL conditions over stored columns (status, fee, auto_price, title_score ...),
so counting and listing stay fast with tens of thousands of products.
"""

from __future__ import annotations

import csv
import io
import json
import re

from . import db, rules, titles
from .db import AMAZON_BASE, conn, log_activity, now_str, tx
from .rules import to_float

LISTING_STATUSES = ["Live", "Deactivated", "OOS"]
MANUAL_STATUSES = ["In Stock", "HP", "LOW INVENTORY", "OOS", "CHECK INVENTORY", "OOS/Different Item", "Time Frame",
                   "Unavailable", "Watermark", "Archived", "Pending Review", "Prohibited", "No Listing"]

EDITABLE = {"name": str, "category": str, "variation": str, "upc": str, "source_price": float, "weight": float,
            "inventory": bool, "listed": bool, "listing_status": str, "manual_status": str, "remarks": str,
            "link": str, "source": str, "title_ok": str, "michaels_sku_number": str,
            # filled by the scraper or an import, but a person can correct them in the sheet
            "asin": str, "price": str, "availability": str, "delivery": str, "run_at": str, "title": str, "checked_by": str, "amazon_variation": str, "amazon_image": str,
            "michaels_price": float, "michaels_qty": float, "sold_2025": float, "sold_2026": float}
FIELD_LABELS = {"amazon_image": "Amazon image", "amazon_variation": "Amazon variation", "checked_by": "Scraped by", "michaels_sku_number": "Michaels SKU number", "asin": "ASIN", "price": "Amazon price",
                "availability": "Availability", "delivery": "Estimated delivery", "run_at": "Last check",
                "michaels_price": "Michaels price", "michaels_qty": "Michaels quantity",
                "sold_2025": "Sold 2025", "sold_2026": "Sold 2026", "name": "Name", "category": "Category", "variation": "Variation", "upc": "UPC",
                "source_price": "Source price", "weight": "Weight", "inventory": "Inventory", "listed": "Listed",
                "listing_status": "Listing status", "manual_status": "Status counter", "remarks": "Optimizer remarks",
                "link": "Source link", "source": "Source", "title_ok": "Title check"}
BULK_FIELDS = ("listing_status", "manual_status", "category", "inventory", "listed", "remarks")

LIST_COLS = ("sku, asin, link, name, category, variation, upc, source, source_price, weight, inventory, listed, "
             "listing_status, manual_status, remarks, price, availability, delivery, run_at, status, last_result, "
             "checked_by, michaels_price, michaels_qty, sold_2025, sold_2026, updated_at, updated_by, created_in_app, "
             "title, title_score, title_note, title_ok, michaels_sku_number, michaels_updated_at, fee, auto_price, "
             "price_num, prev_price_num, price_alert_ack, ovr, extra, images, title_conflict, amazon_variation, amazon_image")

# ------------------------------------------------------------------ queues
# key -> (label, group, SQL condition). Bind names: :tt title threshold, :mz Michaels imported.
SOURCE_QUEUES = [s for s in rules.STATUSES]
EXTRA_QUEUES = {
    # The Amazon title states a pack count or size that no longer agrees with the catalog: the listing really changed.
    "listing-changed": ("Listing changed", "Listing check",
                        "COALESCE(title,'')<>'' AND COALESCE(title_conflict,0)=1 AND COALESCE(title_ok,'')<>title"),
    # No conflicting facts, but too few of the product's key words are in the Amazon title: worth a look.
    "title-mismatch": ("Title mismatch", "Listing check",
                       "COALESCE(title,'')<>'' AND COALESCE(title_conflict,0)=0 AND title_score IS NOT NULL "
                       "AND title_score < :tt AND COALESCE(title_ok,'')<>title"),
    "mz-missing": ("Listed but not on Michaels", "Michaels sync", ":mz=1 AND listed=1 AND michaels_updated_at IS NULL"),
    "mz-stock-off": ("Michaels has stock, inventory off", "Michaels sync",
                     "COALESCE(michaels_qty,0)>0 AND COALESCE(inventory,0)=0"),
    "mz-zero-on": ("Michaels at 0, inventory on", "Michaels sync",
                   "michaels_qty IS NOT NULL AND michaels_qty=0 AND COALESCE(inventory,0)<>0"),
    "mz-price": ("Michaels price differs", "Michaels sync",
                 "COALESCE(michaels_price,0)>0 AND auto_price IS NOT NULL AND ABS(michaels_price-auto_price)>0.009"),
    "dq-weight": ("Missing weight", "Data quality", "COALESCE(weight,0)<=0"),
    "dq-fee": ("Weight outside fee table", "Data quality",
               "COALESCE(weight,0)>0 AND auto_price IS NULL AND source_price IS NOT NULL"),
    "dq-category": ("Missing category", "Data quality", "COALESCE(category,'')=''"),
    "dq-listing": ("Missing listing status", "Data quality", "COALESCE(listing_status,'')=''"),
    "dq-link": ("No Amazon link", "Data quality", "LOWER(COALESCE(link,'')) NOT LIKE '%amazon.%'"),
}


def _binds(c=None) -> dict:
    c = c or conn()
    s = db.get_settings(c)
    return {"tt": float(s.get("title_threshold") or 50),
            "mk": float(db.get_pricing(c)["markup"]),
            "mz": 1 if c.execute("SELECT 1 FROM michaels_imports LIMIT 1").fetchone() else 0}


def queue_counts() -> dict:
    c = conn()
    binds = _binds(c)
    parts = ", ".join(f'SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS "{k}"' for k, (_, _, cond) in EXTRA_QUEUES.items())
    row = c.execute(f"SELECT COUNT(*) AS total, MAX(run_at) AS last_check, {parts} FROM products WHERE active=1", binds).fetchone()
    counts = {k: (row[k] or 0) for k in EXTRA_QUEUES}
    for r in c.execute("SELECT COALESCE(status,'Not checked') s, COUNT(*) n FROM products WHERE active=1 GROUP BY s"):
        counts[r["s"]] = r["n"]
    return {"counts": counts, "total": row["total"], "last_check": row["last_check"]}


SORTS = {
    "sku": "sku COLLATE NOCASE", "name": "LOWER(name)", "status": "status", "source_price": "source_price",
    "auto_price": "auto_price", "michaels_price": "michaels_price", "michaels_sku_number": "michaels_sku_number", "run_at": "run_at", "title_score": "title_score",
    "sold": "(COALESCE(sold_2025,0)+COALESCE(sold_2026,0))", "weight": "weight", "updated_at": "updated_at",
    "price_change": "CASE WHEN COALESCE(prev_price_num,0)>0 THEN ABS(price_num-prev_price_num)/prev_price_num END",
    "cost": "(source_price+fee)", "selling": "((source_price+fee)*:mk)", "fee": "fee",
    "sold_total": "(COALESCE(sold_2025,0)+COALESCE(sold_2026,0))", "listed": "listed", "inventory": "inventory",
    "category": "LOWER(category)", "listing_status": "listing_status", "manual_status": "manual_status",
    "sold_2025": "sold_2025", "sold_2026": "sold_2026", "michaels_qty": "michaels_qty", "asin": "asin",
    "remarks": "LOWER(remarks)", "variation": "LOWER(variation)", "upc": "upc", "source": "source",
    "title": "LOWER(title)", "title_check": "title_score", "checked_by": "LOWER(checked_by)", "amazon_variation": "LOWER(amazon_variation)",
}

# Column filters (?cf_<key>=...), like the filter row of a Google Sheets filter view.
CF_TEXT = {"sku": "sku", "asin": "asin", "name": "name", "category": "category", "link": "link", "source": "source",
           "listing_status": "listing_status", "variation": "variation", "upc": "upc", "manual_status": "manual_status",
           "remarks": "remarks", "michaels_sku_number": "michaels_sku_number", "price": "price",
           "availability": "availability", "delivery": "delivery", "status": "COALESCE(status,'Not checked')",
           "title": "title", "checked_by": "checked_by", "amazon_variation": "amazon_variation"}
CF_BOOL = {"listed": "COALESCE(listed,0)", "inventory": "COALESCE(inventory,0)",
           "img": "CASE WHEN COALESCE(images,'') NOT IN ('', '[]') THEN 1 ELSE 0 END",
           "amz_img": "CASE WHEN COALESCE(amazon_image,'')<>'' THEN 1 ELSE 0 END"}
CF_NUM = {"weight": "weight", "source_price": "source_price", "fee": "fee", "auto_price": "auto_price",
          "michaels_price": "michaels_price", "michaels_qty": "michaels_qty",
          "sold_total": "(COALESCE(sold_2025,0)+COALESCE(sold_2026,0))", "cost": "(source_price+fee)"}
_NUM = r"\$?\s*(-?\d+(?:\.\d+)?)"
NUM_OP = re.compile(rf"^\s*(>=|<=|!=|>|<|=)?\s*{_NUM}\s*$")
TEXT_OP = re.compile(r"^(nc|sw|ew|eq):(.*)$", re.S)       # "Filter by condition" on text: does not contain / starts with / ends with / is exactly


def _text_condition(col: str, v: str, where: list, binds: dict, n: int) -> bool:
    m = TEXT_OP.match(v)
    if not m:
        return False
    op, val = m.groups()
    if op == "eq":
        where.append(f"LOWER(COALESCE({col},''))=LOWER(:cf{n})")
        binds[f"cf{n}"] = val
    else:
        where.append(f"COALESCE({col},'') {'NOT LIKE' if op == 'nc' else 'LIKE'} :cf{n}")
        binds[f"cf{n}"] = {"nc": f"%{val}%", "sw": f"{val}%", "ew": f"%{val}"}[op]
    return True
NUM_RANGE = re.compile(rf"^\s*{_NUM}\s*(?:\.\.|to|-)\s*{_NUM}\s*$", re.I)


def custom_types() -> dict[str, str]:
    """key -> type for the columns people added to the sheet."""
    return {r["key"]: r["type"] for r in conn().execute("SELECT key, type FROM custom_columns")}


def sort_expr(key: str) -> str | None:
    key = key.lstrip("-")
    if key.startswith("color:"):                         # "Sort by color": rows of that colour first (a leading - puts them last)
        cond = color_condition(key[6:])
        return f"CASE WHEN {cond} THEN 0 ELSE 1 END" if cond else None
    if key in custom_types():
        return f"json_extract(extra, '$.{key}')"
    return SORTS.get(key)


BLANK = "(blank)"


def _in_filter(expr: str, vals: list, boolean: bool, where: list, binds: dict, n: int, negate: bool = False) -> None:
    """Value-picker filter. `in:` = the cell equals one of the listed values; `notin:` = it equals none of them
    (the short way to say "everything except these"). Text ignores case; "(blank)" stands for an empty cell."""
    vals = [str(x) for x in vals][:5000]
    if boolean:
        ones = {1 for x in vals if x.upper() in ("TRUE", "1")} | {0 for x in vals if x.upper() in ("FALSE", "0")}
        listed = ",".join(str(i) for i in ones)
        where.append((f"COALESCE({expr},0) NOT IN ({listed})" if ones else "1") if negate else (f"COALESCE({expr},0) IN ({listed})" if ones else "0"))
        return
    names, parts = [], []
    for i, x in enumerate(vals):
        if x == BLANK:
            parts.append(f"COALESCE({expr},'')<>''" if negate else f"COALESCE({expr},'')=''")
        else:
            binds[f"cf{n}_{i}"] = x.lower()
            names.append(f":cf{n}_{i}")
    if names:
        parts.append(f"LOWER(COALESCE({expr},'')) {'NOT IN' if negate else 'IN'} ({','.join(names)})")
    if negate:
        where.append("(" + " AND ".join(parts) + ")" if parts else "1")
    else:
        where.append("(" + " OR ".join(parts) + ")" if parts else "0")


def _picked(v: str):
    """(mode, values) for a picker filter `in:[...]` / `notin:[...]`, else None."""
    for mode in ("notin", "in"):
        if v.startswith(mode + ":"):
            try:
                vals = json.loads(v[len(mode) + 1:])
                return mode, vals if isinstance(vals, list) else []
            except ValueError:
                return mode, []
    return None


def column_values(key: str, q: dict) -> dict:
    """The values in a column with how many products have each (for the picker), under every other filter."""
    q2 = {k: v for k, v in q.items() if k not in ("col", "cf_" + key)}
    cust = custom_types()
    boolean = key in CF_BOOL or cust.get(key) == "checkbox"
    if key in cust:
        if cust[key] == "number":
            raise ValueError("Number columns are filtered with a range such as >10 or 5-9.")
        expr = f"json_extract(extra, '$.{key}')"
    elif key in CF_TEXT:
        expr = CF_TEXT[key]
    elif key in CF_BOOL:
        expr = CF_BOOL[key]
    else:
        raise ValueError("This column has no value list.")
    where, binds = _where(q2)
    if boolean:
        rows = conn().execute(f"SELECT COALESCE({expr},0) v, COUNT(*) n FROM products WHERE {where} GROUP BY v", binds).fetchall()
        return {"values": [{"v": "TRUE" if r["v"] else "FALSE", "n": r["n"]} for r in sorted(rows, key=lambda r: -r["v"])], "truncated": False}
    rows = conn().execute(f"""SELECT MIN(x) v, COUNT(*) n FROM (SELECT COALESCE({expr},'') x FROM products WHERE {where})
        GROUP BY LOWER(x) LIMIT 501""", binds).fetchall()
    out = [{"v": BLANK if str(r["v"]) == "" else str(r["v"]), "n": r["n"]} for r in rows[:500]]
    order = {s: i for i, s in enumerate(rules.STATUSES)}
    out.sort(key=lambda x: (x["v"] == BLANK, order.get(x["v"], 99) if key == "status" else 0, x["v"].lower()))
    return {"values": out, "truncated": len(rows) > 500}


def _column_filters(q: dict, where: list, binds: dict) -> None:
    cust = custom_types() if any(k.startswith("cf_x_") for k in q) else {}
    for k, raw in q.items():
        if not k.startswith("cf_") or raw is None or str(raw).strip() == "":
            continue
        key, v, n = k[3:], str(raw).strip(), len(binds)
        picked = _picked(v)
        if picked is not None:
            mode, vals = picked
            neg = mode == "notin"
            if key.startswith("x_") and key in cust and cust[key] != "number":
                _in_filter(f"json_extract(extra, '$.{key}')", vals, cust[key] == "checkbox", where, binds, n, neg)
            elif key in CF_TEXT:
                _in_filter(CF_TEXT[key], vals, False, where, binds, n, neg)
            elif key in CF_BOOL:
                _in_filter(CF_BOOL[key], vals, True, where, binds, n, neg)
            continue
        if key.startswith("x_"):
            if key not in cust:
                continue
            col = f"json_extract(extra, '$.{key}')"
            if cust[key] == "checkbox":
                where.append(f"COALESCE({col},0)=:cf{n}")
                binds[f"cf{n}"] = 1 if v.upper() in ("TRUE", "1", "YES", "ON") else 0
            elif v == "(blank)":
                where.append(f"COALESCE({col},'')=''")
            elif v == "(not blank)":
                where.append(f"COALESCE({col},'')<>''")
            elif cust[key] != "number" and _text_condition(col, v, where, binds, n):
                pass
            elif cust[key] == "number":
                if (m := NUM_RANGE.match(v)):
                    where.append(f"{col} BETWEEN :cf{n}a AND :cf{n}b")
                    binds[f"cf{n}a"], binds[f"cf{n}b"] = float(m.group(1)), float(m.group(2))
                elif (m := NUM_OP.match(v)):
                    where.append(f"{col} {m.group(1) or '='} :cf{n}")
                    binds[f"cf{n}"] = float(m.group(2))
                else:
                    where.append("0")
            else:
                where.append(f"{col} LIKE :cf{n}")
                binds[f"cf{n}"] = f"%{v}%"
        elif key in CF_TEXT:
            col = CF_TEXT[key]
            if v == "(blank)":
                where.append(f"COALESCE({col},'')=''")
            elif v == "(not blank)":
                where.append(f"COALESCE({col},'')<>''")
            elif _text_condition(col, v, where, binds, n):
                pass
            else:
                where.append(f"{col} LIKE :cf{n}")
                binds[f"cf{n}"] = f"%{v}%"
        elif key in CF_BOOL:
            where.append(f"{CF_BOOL[key]}=:cf{n}")
            binds[f"cf{n}"] = 1 if v.upper() in ("TRUE", "1", "YES", "ON") else 0
        elif key in CF_NUM:
            col = CF_NUM[key]
            if v == "(blank)":
                where.append(f"{col} IS NULL")
            elif v == "(not blank)":
                where.append(f"{col} IS NOT NULL")
            elif (m := NUM_RANGE.match(v)):
                where.append(f"{col} BETWEEN :cf{n}a AND :cf{n}b")
                binds[f"cf{n}a"], binds[f"cf{n}b"] = float(m.group(1)), float(m.group(2))
            elif (m := NUM_OP.match(v)):
                where.append(f"{col} {m.group(1) or '='} :cf{n}")
                binds[f"cf{n}"] = float(m.group(2))
            else:
                where.append("0")      # not understood: show nothing rather than everything


# Colour filter (?color=oos,hp,cell_qty): the grid's colours as SQL, so every row of a colour can be found. These mirror
# ROW_RULES / cellFlag in web/static/js/v-catalog.js, which paint the same rules the Google Sheet's conditional formatting had.
_ON = "COALESCE(inventory,0)=1"
_ST = "COALESCE(status,'')"
ROW_COLORS = [                    # in the order the sheet tries them: the first one that fits colours the row
    ("oos", f"{_ST}='Out of Stock' AND {_ON}"), ("tf", f"{_ST}='TimeFrame' AND {_ON}"),
    ("chk", f"{_ST}='Check Inventory' AND NOT {_ON}"), ("hp", f"{_ST}='High Price' AND {_ON}"),
    ("li", f"{_ST}='Low Inventory' AND {_ON}"), ("lp", f"{_ST}='Low Price' AND {_ON}"), ("mc", f"{_ST}='Manual Check' AND {_ON}"),
    ("oosdi", f"{_ST}='OOS Different Item' AND {_ON}"), ("oosv", f"{_ST}='OOS Variation' AND {_ON}"),
    ("mzoff", f"COALESCE(michaels_qty,0)>0 AND NOT {_ON}"), ("mzzero", f"{_ON} AND COALESCE(michaels_qty,0)=0"),
]
CELL_COLORS = {
    "cell_qty": f"(COALESCE(michaels_qty,0)>0 AND NOT {_ON}) OR (michaels_qty IS NOT NULL AND michaels_qty=0 AND {_ON})",
    "cell_price": "michaels_price IS NOT NULL AND (auto_price IS NULL OR ABS(michaels_price-auto_price)>=0.005)",
    "box_sold": ("COALESCE(CASE WHEN COALESCE(ovr,'')<>'' THEN json_extract(ovr,'$.sold_total') END, "
                 "COALESCE(sold_2025,0)+COALESCE(sold_2026,0))>=1"),
    "text_prime": "COALESCE(delivery,'') LIKE '%prime%'",
}


def color_condition(k: str) -> str | None:
    """The SQL that is true for the rows (or cells) of one colour, None for a colour that doesn't exist."""
    if k == "none":
        return "NOT (" + " OR ".join(f"({s})" for _, s in ROW_COLORS) + ")"
    if k in CELL_COLORS:
        return f"({CELL_COLORS[k]})"
    names = [n for n, _ in ROW_COLORS]
    if k in names:
        i = names.index(k)
        return f"(({ROW_COLORS[i][1]})" + "".join(f" AND NOT ({s})" for _, s in ROW_COLORS[:i]) + ")"
    return None


def _color_filter(q: dict, where: list) -> None:
    keys = [k for k in str(q.get("color") or "").split(",") if k]
    parts = [c for c in (color_condition(k) for k in keys) if c]
    if keys:
        where.append("(" + " OR ".join(parts) + ")" if parts else "0")


def _where(q: dict) -> tuple[str, dict]:
    where, binds = ["active=1"], _binds()
    if q.get("q"):
        terms = [t for t in re.split(r"\s+", q["q"].strip()) if t][:6]
        for i, t in enumerate(terms):
            where.append(f"(sku LIKE :t{i} OR asin LIKE :t{i} OR name LIKE :t{i} OR upc LIKE :t{i} "
                         f"OR michaels_sku_number LIKE :t{i})")
            binds[f"t{i}"] = f"%{t}%"
    queue = q.get("queue", "")
    if queue in EXTRA_QUEUES:
        where.append(f"({EXTRA_QUEUES[queue][2]})")
    elif queue:
        where.append("COALESCE(status,'Not checked')=:queue")
        binds["queue"] = queue
    if q.get("listing"):
        if q["listing"] == "(blank)":
            where.append("COALESCE(listing_status,'')=''")
        else:
            where.append("listing_status=:listing")
            binds["listing"] = q["listing"]
    if q.get("category"):
        where.append("category=:category")
        binds["category"] = q["category"]
    if q.get("source"):
        where.append("source=:source")
        binds["source"] = q["source"]
    if q.get("inventory") in ("0", "1"):
        where.append("COALESCE(inventory,0)=:inv")
        binds["inv"] = int(q["inventory"])
    if q.get("added") == "1":
        where.append("created_in_app=1")
    _column_filters(q, where, binds)
    _color_filter(q, where)
    return " AND ".join(where), binds


def _decorate(r) -> dict:
    d = dict(r)
    d["images"] = db.load_list(d.get("images"))
    if d.get("price_num") and d.get("prev_price_num"):
        d["price_change_pct"] = round((d["price_num"] - d["prev_price_num"]) * 100.0 / d["prev_price_num"], 1)
    return d


def list_products(q: dict, page_size: int = 50) -> dict:
    where, binds = _where(q)
    c = conn()
    key = q.get("sort", "")
    desc = key.startswith("-")
    expr = sort_expr(key)
    order = f"({expr}) IS NULL, {expr} {'DESC' if desc else 'ASC'}, row_order" if expr else "row_order"
    total = c.execute(f"SELECT COUNT(*) FROM products WHERE {where}", binds).fetchone()[0]
    page = max(0, int(q.get("page", 0) or 0))
    size = max(1, min(int(q.get("size") or page_size), 500))
    rows = c.execute(f"SELECT {LIST_COLS} FROM products WHERE {where} ORDER BY {order} LIMIT :lim OFFSET :off",
                     {**binds, "lim": size, "off": page * size}).fetchall()
    return {"total": total, "page": page, "size": size, "items": [_decorate(r) for r in rows]}


def matching_skus(q: dict, limit: int = 20000) -> list[str]:
    where, binds = _where(q)
    return [r[0] for r in conn().execute(f"SELECT sku FROM products WHERE {where} ORDER BY row_order LIMIT {limit}", binds)]


def categories() -> list[str]:
    return [r[0] for r in conn().execute(
        "SELECT DISTINCT category FROM products WHERE active=1 AND COALESCE(category,'')<>'' ORDER BY category")]


# ------------------------------------------------------------------ fixed dropdown columns
# An admin can turn a text column into a dropdown: people then pick one of the listed values instead of typing.
# Stored as one setting, {column key: [values]}. Imports and the scraper are not checked against it.
NO_DROPDOWN = {"name", "link", "title", "title_ok"}


def get_dropdowns(c=None) -> dict[str, list[str]]:
    c = c or conn()
    row = c.execute("SELECT value FROM settings WHERE key='dropdowns'").fetchone()
    try:
        raw = json.loads(row["value"]) if row else {}
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: [str(x) for x in v] for k, v in raw.items() if isinstance(v, list)}


def _dropdown_label(c, col: str) -> str:
    """The column's label when it can be a dropdown (a stored text column or a custom text column), else ValueError."""
    if col in EDITABLE and EDITABLE[col] is str and col not in NO_DROPDOWN:
        return FIELD_LABELS.get(col, col)
    custom = c.execute("SELECT label, type FROM custom_columns WHERE key=?", (col,)).fetchone()
    if custom and custom["type"] == "text":
        return custom["label"]
    raise ValueError("This column can't be a dropdown. Only text columns can (not names, links or calculated columns).")


def _clean_values(values) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        s = re.sub(r"\s+", " ", str(v).strip())[:80]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:300]


def set_dropdown(col: str, values, enabled: bool, user: str) -> dict:
    values = _clean_values(values)
    with tx() as c:
        label = _dropdown_label(c, col)
        now = get_dropdowns(c)
        if enabled:
            if not values:
                raise ValueError("Add at least one value, or turn the dropdown off.")
            now[col] = values
        else:
            now.pop(col, None)
        db.set_setting(c, "dropdowns", json.dumps(now))
        shown = ", ".join(values[:8]) + ("…" if len(values) > 8 else "")
        log_activity(c, user, "changed a dropdown", "", f"{label}: " + (f"{len(values)} values ({shown})" if enabled else "back to free typing"))
    return {"dropdowns": get_dropdowns()}


def dropdown_value(c, field: str, v: str, current, label: str | None = None) -> str:
    """v is a value being typed into `field`. When the column is a dropdown, return the listed spelling of it or
    raise ValueError. A cell that already holds exactly v is left alone, so old values outside the list survive."""
    allowed = get_dropdowns(c).get(field)
    if allowed is None or not v or v == (current or ""):
        return v
    hit = next((x for x in allowed if x.lower() == v.lower()), None)
    if hit is None:
        raise ValueError(f"“{v}” isn't in the dropdown for {label or FIELD_LABELS.get(field, field)}. Choose one of: "
                         + ", ".join(allowed[:12]) + ("…" if len(allowed) > 12 else "") + ".")
    return hit


def distinct_values(col: str) -> dict:
    """The values a column holds now (numbers in order), to start a dropdown from."""
    c = conn()
    _dropdown_label(c, col)
    if col in EDITABLE:
        rows = c.execute(f"SELECT DISTINCT {col} AS v FROM products WHERE active=1 AND COALESCE({col}, '')<>''")
    else:
        rows = c.execute("SELECT DISTINCT json_extract(extra, '$.' || ?) AS v FROM products WHERE active=1 "
                         "AND extra IS NOT NULL AND COALESCE(json_extract(extra, '$.' || ?), '')<>''", (col, col))
    vals = _clean_values(str(r["v"]) for r in rows)

    def order(s):
        n = to_float(s)
        return (0, n, s.lower()) if n is not None else (1, 0, s.lower())
    vals.sort(key=order)
    return {"values": vals, "more": len(vals) >= 300}


def meta() -> dict:
    c = conn()
    dropdowns = get_dropdowns(c)
    return {"categories": categories(), "pricing": db.get_pricing(c), "settings": db.get_settings(c),
            "custom_columns": [dict(r) for r in c.execute("SELECT key, label, type, after FROM custom_columns ORDER BY created_at, rowid")],
            "dropdowns": dropdowns,
            "listing_statuses": LISTING_STATUSES, "manual_statuses": dropdowns.get("manual_status") or MANUAL_STATUSES,
            "queues": {k: {"label": v[0], "group": v[1]} for k, v in EXTRA_QUEUES.items()},
            "table_labels": rules.TABLE_LABELS}


def product_detail(sku: str) -> dict:
    c = conn()
    p = c.execute(f"SELECT {LIST_COLS} FROM products WHERE sku=?", (sku,)).fetchone()
    if not p:
        raise ValueError("Product not found.")
    pr = db.get_pricing(c)
    tf = float(db.get_settings(c)["timeframe_days"])
    d = _decorate(p)
    d["breakdown"] = rules.price_breakdown(p["weight"], p["source_price"], pr)
    d["why"] = rules.explain_status(p, tf)
    d["lead_days"] = rules.lead_days(p["delivery"], p["run_at"])
    hist = c.execute("SELECT * FROM history WHERE sku=? ORDER BY id DESC LIMIT 30", (sku,)).fetchall()
    act = c.execute("SELECT * FROM activity WHERE sku=? ORDER BY id DESC LIMIT 30", (sku,)).fetchall()
    return {"product": d, "history": [dict(h) for h in hist], "activity": [dict(a) for a in act]}


# ------------------------------------------------------------------ editing
def _clean(field, v):
    typ = EDITABLE[field]
    if typ is float:
        return to_float(v) if v not in (None, "") else None
    if typ is bool:
        return 1 if v is True or str(v).strip().upper() in ("1", "TRUE", "YES", "Y", "X", "CHECKED") else 0
    return str(v or "").strip()


def find_dupes(sku="", asin="", upc="", link="", ignore="") -> list[dict]:
    asin = (asin or rules.asin_from_link(link)).upper()
    out = []
    c = conn()
    for label, cond, val in [("SKU", "sku=? COLLATE NOCASE", (sku or "").strip()), ("ASIN", "asin=?", asin),
                             ("UPC", "upc=?", (upc or "").strip())]:
        if val:
            for r in c.execute(f"SELECT sku, name FROM products WHERE active=1 AND {cond} AND sku<>? LIMIT 3", (val, ignore)):
                out.append({"field": label, "sku": r["sku"], "name": r["name"]})
    return out


def _refresh_derived(c, sku: str, pr: dict, tf: float) -> None:
    """Re-derive fee, price, title score and status for one product after an edit."""
    p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    score, note, conflict = titles.check(p["name"], p["title"])
    ovr = db.load_ovr(p["ovr"])
    fee, auto = db.derive_prices(p["weight"], p["source_price"], ovr, pr)
    fresh = {**dict(p), "title_score": score, "title_conflict": conflict}        # the stored title fields are about to be replaced
    status = ovr["status"] if ovr.get("status") else rules.compute_status(
        fresh, tf, float(db.get_settings(c)["title_threshold"]))
    price_num = p["price_num"]
    if not (p["price"] or "").strip():
        price_num = None
    elif rules.scrap_price(p["price"]) is not None:
        price_num = rules.scrap_price(p["price"])
    c.execute("UPDATE products SET fee=?, auto_price=?, title_score=?, title_note=?, title_conflict=?, status=?, price_num=? WHERE sku=?",
              (fee, auto, score, note, conflict, status, price_num, sku))


def _apply_patch(c, p, patch: dict, raw: list | None = None) -> tuple[list[str], list[str], list]:
    """raw, when given, is filled with {"f","o","n"} for every changed field (read later by Show edit history)."""
    changes, sets, args = [], [], []
    for k, v in patch.items():
        if k not in EDITABLE:
            continue
        v = _clean(k, v)
        if k == "listing_status" and v and v not in LISTING_STATUSES:
            raise ValueError("Listing status must be Live, Deactivated or OOS.")
        if k == "name" and not v:
            raise ValueError("Product name can't be empty.")
        if EDITABLE[k] is str and k not in NO_DROPDOWN:
            v = dropdown_value(c, k, v, p[k])
        old = p[k]
        if (old if old is not None else "") == (v if v is not None else ""):
            continue
        show = lambda x: ("on" if x else "off") if EDITABLE[k] is bool else (x if x not in (None, "") else "—")
        changes.append(f"{FIELD_LABELS.get(k, k)}: {show(old)} → {show(v)}")
        if raw is not None:
            raw.append({"f": "title_check" if k == "title_ok" else k, "o": old, "n": v})
        sets.append(f"{k}=?")
        args.append(v)
        if k == "link":
            asin = rules.asin_from_link(v)
            if asin:
                sets.append("asin=?")
                args.append(asin)
    return changes, sets, args


def update_product(sku: str, patch: dict, user: str, action: str = "edited") -> dict:
    with tx() as c:
        p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not p:
            raise ValueError("Product not found.")
        raw: list = []
        changes, sets, args = _apply_patch(c, p, patch or {}, raw)
        if sets:
            st = db.get_settings(c)
            tt = float(st["title_threshold"])
            c.execute(f"UPDATE products SET {', '.join(sets)}, updated_at=?, updated_by=? WHERE sku=?",
                      args + [now_str(), user, sku])
            _refresh_derived(c, sku, db.get_pricing(c), float(st["timeframe_days"]))
            log_activity(c, user, action, sku, "; ".join(changes), rules.issue_of(p, tt), db.issue_now(c, sku, tt), raw)
    return {"ok": True, "changed": len(changes)}


def bulk_update(skus: list[str], patch: dict, action: str, user: str) -> dict:
    """Apply one change to many products. `action` can also be 'accept_title'."""
    skus = list(dict.fromkeys(skus))[:20000]
    if not skus:
        raise ValueError("Nothing is selected.")
    patch = {k: v for k, v in (patch or {}).items() if k in BULK_FIELDS}
    if not patch and action != "accept_title":
        raise ValueError("Pick something to change.")
    changed = 0
    with tx() as c:
        st = db.get_settings(c)
        pr, tf, tt = db.get_pricing(c), float(st["timeframe_days"]), float(st["title_threshold"])
        for sku in skus:
            p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if not p:
                continue
            if action == "accept_title":
                if p["title"]:
                    c.execute("UPDATE products SET title_ok=?, updated_at=?, updated_by=? WHERE sku=?",
                              (p["title"], now_str(), user, sku))
                    _refresh_derived(c, sku, pr, tf)                  # an accepted title lifts OOS Different Item / Manual Check
                    log_activity(c, user, "accepted the Amazon title", sku, "", rules.issue_of(p, tt), db.issue_now(c, sku, tt),
                                 [{"f": "title_check", "o": p["title_ok"] or "", "n": "OK (accepted)"}])
                    changed += 1
                continue
            raw: list = []
            changes, sets, args = _apply_patch(c, p, patch, raw)
            if sets:
                c.execute(f"UPDATE products SET {', '.join(sets)}, updated_at=?, updated_by=? WHERE sku=?",
                          args + [now_str(), user, sku])
                _refresh_derived(c, sku, pr, tf)
                log_activity(c, user, "bulk edited", sku, "; ".join(changes), rules.issue_of(p, tt), db.issue_now(c, sku, tt), raw)
                changed += 1
    return {"ok": True, "changed": changed, "selected": len(skus)}


# ------------------------------------------------------------------ adding
def validate_new(f: dict, pr: dict, taken: set[str] | None = None, loose: bool = False) -> dict:
    """Field errors for a product about to be created (empty dict when fine).
    loose = a row typed into the sheet: nothing is required (a missing SKU gets a temporary one); only a SKU that
    is given must be valid and unused. Missing pieces show up in the Data quality queues instead."""
    errors = {}
    sku = str(f.get("sku") or "").strip()
    if loose:
        if sku and not re.fullmatch(r"[A-Za-z0-9._\-]+", sku):
            errors["sku"] = "Use only letters, numbers, dots, dashes and underscores."
        elif sku and taken is not None and sku.lower() in taken:
            errors["sku"] = "SKU is repeated in this batch."
        return errors
    if not sku:
        errors["sku"] = "SKU is required."
    elif not re.fullmatch(r"[A-Za-z0-9._\-]+", sku):
        errors["sku"] = "Use only letters, numbers, dots, dashes and underscores."
    elif taken is not None and sku.lower() in taken:
        errors["sku"] = "SKU is repeated in this batch."
    if not str(f.get("name") or "").strip():
        errors["name"] = "Product name is required."
    if not str(f.get("link") or "").strip():
        errors["link"] = "Source link is required."
    if not (to_float(f.get("source_price")) or 0) > 0:
        errors["source_price"] = "Enter the source price in dollars."
    w = to_float(f.get("weight"))
    if not (w or 0) > 0:
        errors["weight"] = "Enter the weight in ounces."
    elif rules.fee_for(w, pr) is None:
        errors["weight"] = "Heavier than the last tier in the active fee table."
    if not str(f.get("category") or "").strip():
        errors["category"] = "Pick or type a category."
    return errors


def _insert_product(c, f: dict, user: str, pr: dict) -> str:
    sku = str(f["sku"]).strip()
    link = str(f.get("link") or "").strip()
    asin = rules.asin_from_link(link) or str(f.get("asin") or "").strip().upper()
    if rules.asin_from_link(link):
        link = f"{AMAZON_BASE}/dp/{asin}"
    w, sp = to_float(f.get("weight")), to_float(f.get("source_price"))
    # The Add products page doesn't send these and gets the usual defaults (on, on, Amazon, Live);
    # new rows typed into the sheet send exactly what the person left or filled in, blanks included.
    def flag(key):
        if key not in f:
            return 1
        v = f[key]
        return 1 if (str(v).strip().upper() in ("1", "TRUE", "YES", "Y", "X") if isinstance(v, str) else v) else 0
    source = str(f["source"]).strip() if "source" in f else "Amazon"
    listing = str(f["listing_status"]).strip() if "listing_status" in f else "Live"
    order = (c.execute("SELECT MAX(row_order) FROM products").fetchone()[0] or 1) + 1
    c.execute("""INSERT INTO products(sku, asin, link, name, category, variation, upc, source, source_price, weight,
        inventory, listed, listing_status, manual_status, remarks, row_order, active, status, created_in_app,
        created_at, updated_at, updated_by, sold_2025, sold_2026, fee, auto_price)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'Not checked',1,?,?,?,0,0,?,?)""",
              (sku, asin, link, str(f.get("name") or "").strip(), str(f.get("category") or "").strip(), str(f.get("variation") or "").strip(),
               str(f.get("upc") or "").strip(), source, sp, w, flag("inventory"), flag("listed"),
               listing, "", "", order, now_str(), now_str(), user,
               rules.fee_for(w, pr), rules.auto_price(w, sp, pr)))
    a = rules.auto_price(w, sp, pr)
    log_activity(c, user, "added", sku, f"Automation price ${a:.2f}" if a else "")
    return sku


def create_product(f: dict, user: str) -> dict:
    pr = db.get_pricing()
    errors = validate_new(f, pr)
    if errors:
        return {"ok": False, "errors": errors}
    dupes = find_dupes(f.get("sku", ""), f.get("asin", ""), f.get("upc", ""), f.get("link", ""))
    if dupes:
        return {"ok": False, "dupes": dupes}
    with tx() as c:
        if c.execute("SELECT 1 FROM products WHERE sku=?", (str(f["sku"]).strip(),)).fetchone():
            return {"ok": False, "errors": {"sku": "This SKU already exists (possibly removed from the sheet)."}}
        sku = _insert_product(c, f, user, pr)
    return {"ok": True, "sku": sku}


def preview_batch(rows: list[dict], loose: bool = False) -> dict:
    """Validate pasted rows without saving: field errors, duplicates and the price each would get."""
    pr = db.get_pricing()
    seen_sku, seen_asin, out = set(), set(), []
    for f in rows:
        errors = validate_new(f, pr, seen_sku, loose)
        sku = str(f.get("sku") or "").strip()
        asin = (rules.asin_from_link(f.get("link")) or str(f.get("asin") or "").strip()).upper()
        dupes = [] if errors.get("sku") else find_dupes(sku, asin, f.get("upc", ""), f.get("link", ""))
        if asin and asin in seen_asin:
            dupes.append({"field": "ASIN", "sku": "(this batch)", "name": "Same ASIN appears twice in the paste"})
        if sku:
            seen_sku.add(sku.lower())
        if asin:
            seen_asin.add(asin)
        w, sp = to_float(f.get("weight")), to_float(f.get("source_price"))
        out.append({"errors": errors, "dupes": dupes, "auto_price": rules.auto_price(w, sp, pr),
                    "fee": rules.fee_for(w, pr), "ok": not errors and not dupes})
    return {"rows": out, "ok_count": sum(1 for r in out if r["ok"])}


def _next_temp_sku(c) -> str:
    """NEW-000001, NEW-000002 ...: the temporary SKU of a row that was added without one (rename it any time)."""
    n = c.execute("SELECT MAX(CAST(SUBSTR(sku, 5) AS INTEGER)) FROM products WHERE sku GLOB 'NEW-[0-9]*'").fetchone()[0] or 0
    return f"NEW-{n + 1:06d}"


def create_batch(rows: list[dict], user: str, loose: bool = False) -> dict:
    pr = db.get_pricing()
    prev = preview_batch(rows, loose)
    created, made, skipped = [], [], 0
    with tx() as c:
        for i, (f, r) in enumerate(zip(rows, prev["rows"])):
            if not r["ok"]:
                skipped += 1
                continue
            sku = str(f.get("sku") or "").strip()
            if loose and not sku:
                sku = _next_temp_sku(c)                       # the next free temporary SKU (the one just made is already saved)
            if c.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                skipped += 1
                continue
            created.append(_insert_product(c, {**f, "sku": sku}, user, pr))
            made.append({"i": i, "sku": sku})
    return {"ok": True, "created": len(created), "skipped": skipped, "skus": created, "rows": made}


# ------------------------------------------------------------------ deleting
def delete_products(skus: list[str], user: str) -> dict:
    """Remove products from the catalog. Each one is archived first (deleted_products) so it can be restored."""
    skus = list(dict.fromkeys(str(s) for s in skus))[:20000]
    if not skus:
        raise ValueError("Nothing is selected.")
    gone = []
    with tx() as c:
        for sku in skus:
            p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
            if not p:
                continue
            c.execute("INSERT INTO deleted_products(sku, at, by, data) VALUES(?,?,?,?)", (sku, now_str(), user, json.dumps(dict(p))))
            c.execute("DELETE FROM products WHERE sku=?", (sku,))
            c.execute("DELETE FROM tasks WHERE sku=? AND state IN ('pending','claimed')", (sku,))   # the scraper stops asking for it
            log_activity(c, user, "deleted", sku, (p["name"] or "")[:120])
            gone.append(sku)
    return {"ok": True, "deleted": len(gone), "skus": gone}


def list_deleted(limit: int = 200) -> dict:
    """The most recently deleted products that can still be restored (newest first)."""
    c = conn()
    out, seen = [], set()
    for r in c.execute("SELECT sku, at, by, data FROM deleted_products ORDER BY id DESC LIMIT ?", (limit * 3,)):
        if r["sku"] in seen or c.execute("SELECT 1 FROM products WHERE sku=?", (r["sku"],)).fetchone():
            continue
        seen.add(r["sku"])
        try:
            name = json.loads(r["data"]).get("name") or ""
        except ValueError:
            name = ""
        out.append({"sku": r["sku"], "name": name, "at": r["at"], "by": r["by"]})
        if len(out) >= limit:
            break
    return {"items": out}


def restore_products(skus: list[str], user: str) -> dict:
    """Bring back deleted products (undo). A SKU that has been used again since is skipped."""
    back, skipped = [], 0
    with tx() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(products)")}
        for sku in dict.fromkeys(str(s) for s in skus):
            arch = c.execute("SELECT id, data FROM deleted_products WHERE sku=? ORDER BY id DESC LIMIT 1", (sku,)).fetchone()
            if not arch or c.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
                skipped += 1
                continue
            row = {k: v for k, v in json.loads(arch["data"]).items() if k in cols}
            c.execute(f"INSERT INTO products({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
            c.execute("DELETE FROM deleted_products WHERE id=?", (arch["id"],))
            log_activity(c, user, "restored", sku, "")
            back.append(sku)
    return {"ok": True, "restored": len(back), "skipped": skipped, "skus": back}


# ------------------------------------------------------------------ pricing rules
def _clean_tiers(rows) -> list[dict]:
    """The fee table as typed on the Pricing tab: each tier is 'up to N oz costs $F'."""
    tiers = []
    for r in rows or []:
        try:
            mx, fee = float(r["max"]), float(r["fee"])
        except (TypeError, ValueError, KeyError):
            raise ValueError("Every weight limit and every fee must be a number.")
        if mx <= 0:
            raise ValueError("A weight limit must be above 0 oz.")
        if fee < 0:
            raise ValueError("A fee can't be negative.")
        tiers.append({"max": mx, "fee": fee})
    if not tiers:
        raise ValueError("The fee table needs at least one weight tier.")
    tiers.sort(key=lambda t: t["max"])
    for a, b in zip(tiers, tiers[1:]):
        if a["max"] == b["max"]:
            raise ValueError(f"Two tiers have the same weight limit ({a['max']:g} oz).")
    return tiers


def save_pricing(p: dict, user: str) -> dict:
    cur = db.get_pricing()
    try:
        markup, buffer = float(p.get("markup", cur["markup"])), float(p.get("fee_buffer", cur["fee_buffer"]))
    except (TypeError, ValueError):
        raise ValueError("The markup and the fee buffer must be numbers.")
    if not 1 <= markup <= 5:
        raise ValueError("Markup should be between 1 and 5 (1.53 = 53%).")
    if buffer < 0:
        raise ValueError("The fee buffer can't be negative.")
    rows = (p.get("tables") or {}).get("current")
    tiers = _clean_tiers(rows) if rows is not None else cur["tables"]["current"]
    new = {"markup": markup, "fee_buffer": buffer, "active_table": "current", "tables": {"current": tiers}}
    with tx() as c:
        db.set_setting(c, "pricing", json.dumps(new))
        n = db.recompute_prices(c)
        log_activity(c, user, "changed pricing rules", "",
                     f"3PL fee table ({len(tiers)} weight tiers), {markup}× markup, +${buffer:g} ({n:,} prices recalculated)")
    return {"ok": True, "pricing": new}


def pricing_impact(pricing: dict | None = None) -> dict:
    """How many automation prices would change if `pricing` (markup, buffer, fee tiers) were saved."""
    pr = db.get_pricing()
    alt = pr
    if pricing:
        try:
            alt = {**pr, "markup": float(pricing.get("markup", pr["markup"])),
                   "fee_buffer": float(pricing.get("fee_buffer", pr["fee_buffer"]))}
            rows = (pricing.get("tables") or {}).get("current")
            if rows is not None:
                alt["tables"] = {"current": _clean_tiers(rows)}
        except (TypeError, ValueError) as e:
            raise ValueError(str(e) if str(e) and "number" not in str(e).lower() else "Every rate and the markup must be a number.")
    up = down = same = none = 0
    total = 0.0
    rows = conn().execute("SELECT weight, source_price, auto_price FROM products WHERE active=1").fetchall()
    for r in rows:
        a, b = r["auto_price"], rules.auto_price(r["weight"], r["source_price"], alt)
        if a is None or b is None:
            none += 1
        elif b > a + 0.001:
            up += 1
            total += b - a
        elif b < a - 0.001:
            down += 1
            total += b - a
        else:
            same += 1
    return {"up": up, "down": down, "same": same, "none": none,
            "avg": round(total / (up + down), 2) if up + down else 0, "products": len(rows)}

# ------------------------------------------------------------------ activity & views
ACTIVITY_KINDS = {                      # the Activity tab's "what" filter
    "rows": ("deleted", "restored"),
    "columns": ("added a column", "deleted a column"),
    "added": ("added",),
    "edits": ("edited in the sheet", "edited", "bulk edited", "bulk edited in the sheet", "pasted into the sheet",
              "filled in the sheet", "filled down in the sheet", "used the Amazon price as source price", "undid an edit", "redid an edit"),
}


def activity_feed(q: dict) -> dict:
    where, args = ["1=1"], []
    if q.get("user"):
        where.append("user=?")
        args.append(q["user"])
    if q.get("kind") in ACTIVITY_KINDS:
        acts = ACTIVITY_KINDS[q["kind"]]
        where.append(f"action IN ({','.join('?' * len(acts))})")
        args += acts
    if q.get("q"):
        where.append("(sku LIKE ? OR detail LIKE ? OR action LIKE ?)")
        args += [f"%{q['q']}%"] * 3
    c = conn()
    rows = c.execute(f"SELECT * FROM activity WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 300", args).fetchall()
    users = [r[0] for r in c.execute("SELECT DISTINCT user FROM activity ORDER BY user")]
    return {"items": [dict(r) for r in rows], "users": users}


def productivity(q: dict) -> dict:
    """Per person and day: issues resolved (products that had an issue before the edit and none after, counted
    once per product per day), products touched, and which kinds of issue. Edits made before this report existed
    carry no before/after, so they only count as 'touched'."""
    from datetime import date, timedelta
    try:
        day = date.fromisoformat(str(q.get("day") or ""))
    except ValueError:
        day = date.today()
    days = max(1, min(60, int(q.get("days") or 14)))
    first = day - timedelta(days=days - 1)
    c = conn()
    rows = c.execute("""SELECT substr(at,1,10) AS d, user, sku, at, issue_before, issue_after FROM activity
        WHERE sku<>'' AND substr(at,1,10) BETWEEN ? AND ? AND action NOT IN ('added')""", (first.isoformat(), day.isoformat())).fetchall()
    people: dict[str, dict] = {}
    series: dict[str, dict[str, set]] = {}
    for r in rows:
        u = r["user"]
        resolved = bool(r["issue_before"]) and not r["issue_after"]
        series.setdefault(u, {}).setdefault(r["d"], set())
        if resolved:
            series[u][r["d"]].add(r["sku"])
        if r["d"] != day.isoformat():
            continue
        s = people.setdefault(u, {"user": u, "resolved": set(), "touched": set(), "kinds": {}, "first": r["at"], "last": r["at"]})
        s["touched"].add(r["sku"])
        s["first"], s["last"] = min(s["first"], r["at"]), max(s["last"], r["at"])
        if resolved and r["sku"] not in s["resolved"]:
            s["resolved"].add(r["sku"])
            s["kinds"][r["issue_before"]] = s["kinds"].get(r["issue_before"], 0) + 1
    out = [{"user": s["user"], "resolved": len(s["resolved"]), "touched": len(s["touched"]), "kinds": s["kinds"],
            "first": s["first"][11:16], "last": s["last"][11:16]} for s in people.values()]
    out.sort(key=lambda x: (-x["resolved"], -x["touched"], x["user"]))
    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    return {"day": day.isoformat(), "people": out, "dates": dates,
            "series": [{"user": u, "counts": [len(v.get(d, ())) for d in dates]} for u, v in sorted(series.items())],
            "total_resolved": sum(p["resolved"] for p in out)}


def saved_views(username: str) -> list[dict]:
    rows = conn().execute("""SELECT id, username, name, params, shared FROM saved_views
        WHERE username=? OR shared=1 ORDER BY name""", (username,)).fetchall()
    return [{**dict(r), "params": json.loads(r["params"] or "{}"), "mine": r["username"] == username} for r in rows]


def save_view(username: str, name: str, params: dict, shared: bool) -> dict:
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Give the view a name.")
    keep = {k: str(v) for k, v in params.items()
            if (k in ("q", "queue", "listing", "category", "sort", "source", "inventory", "added", "color") or k.startswith("cf_")) and v}
    with tx() as c:
        c.execute("DELETE FROM saved_views WHERE username=? AND name=?", (username, name))
        c.execute("INSERT INTO saved_views(username, name, params, shared, created_at) VALUES(?,?,?,?,?)",
                  (username, name, json.dumps(keep), 1 if shared else 0, now_str()))
    return {"ok": True}


def delete_view(username: str, view_id: int, is_admin: bool) -> None:
    with tx() as c:
        c.execute("DELETE FROM saved_views WHERE id=? AND (username=? OR ?)", (view_id, username, 1 if is_admin else 0))


# ------------------------------------------------------------------ exports
def _csv_bytes(header: list[str], rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return ("﻿" + out.getvalue()).encode("utf-8")


def export_view(q: dict) -> bytes:
    where, binds = _where(q)
    cols = ["sku", "asin", "name", "category", "status", "listing_status", "source_price", "weight", "fee",
            "auto_price", "michaels_sku_number", "michaels_price", "michaels_qty", "inventory", "price", "availability", "delivery", "run_at",
            "title_score", "remarks", "link"]
    rows = conn().execute(f"SELECT {', '.join(cols)} FROM products WHERE {where} ORDER BY row_order", binds)
    return _csv_bytes([c.replace("_", " ").title() for c in cols], ([r[c] for c in cols] for r in rows))


def export_optimizer() -> bytes:
    """Same columns the earlier version exported, for pasting back into the Listed Sheet."""
    rows = conn().execute("""SELECT sku, asin, price, availability, delivery, run_at, status, checked_by,
        title, title_score, title_note FROM products WHERE active=1 ORDER BY row_order""")
    return _csv_bytes(["SKU", "ASIN", "Price", "Availability", "Estimated Delivery",
                       "Last Optimization Run (Tapicker Output)", "SOURCE STATUS", "Checked by",
                       "Amazon Title", "Title Match %", "Title Note"],
                      ([r[i] for i in range(11)] for r in rows))


def export_michaels_fixes() -> bytes:
    cond = EXTRA_QUEUES["mz-price"][2]
    rows = conn().execute(f"""SELECT sku, michaels_sku_number, name, michaels_price, auto_price FROM products
        WHERE active=1 AND {cond} ORDER BY row_order""")
    return _csv_bytes(["SKU", "Michaels SKU number", "Product", "Current Michaels price", "Automation price"],
                      ([r[i] for i in range(5)] for r in rows))





