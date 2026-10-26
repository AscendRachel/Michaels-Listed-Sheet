"""Business rules: source status, title matching and pricing.

Everything here is a pure function (no database, no network) so it can be unit tested.
The source-status rules follow column W ("SOURCE STATUS") of the Listed Sheet, in the same order.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from . import variations

# ------------------------------------------------------------------ text helpers
CODE_MARKERS = re.compile(r"\(function\s*\(|P\.when\(|\{\s*var\s|window\.|document\.|=>\s*\{")


def strip_code(text: str | None) -> str:
    """Cut off JavaScript that scrapers sometimes pick up along with Amazon's delivery text."""
    text = text or ""
    m = CODE_MARKERS.search(text)
    return re.sub(r"\s+", " ", text[: m.start()] if m else text).strip()


def to_float(v) -> float | None:
    try:
        f = float(str(v).replace("$", "").replace(",", "").strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def to_bool(v) -> int:
    t = str(v).strip().upper()
    if t in ("TRUE", "YES", "Y", "CHECKED"):
        return 1
    try:
        return 1 if float(t) > 0 else 0
    except ValueError:
        return 0


# Amazon image addresses look like https://m.media-amazon.com/images/I/81IhpPoiBmL._AC_SL1500_.jpg: an id, then a size
# code, then the extension. Keep only id + extension so any size can be asked for later (the grid uses small ones).
AMZ_IMAGE_RE = re.compile(r"^https?://m\.media-amazon\.com/images/I/([A-Za-z0-9%+_-]+)\.(?:[^/?#]*\.)?(jpg|jpeg|png)(?:[?#].*)?$", re.I)


def amazon_image_base(url: str | None) -> str:
    """The size-free address of an Amazon product image (' when it isn't one we can use)."""
    url = (url or "").strip()
    m = AMZ_IMAGE_RE.match(url)
    if m:
        return f"https://m.media-amazon.com/images/I/{m.group(1)}.{m.group(2).lower()}"
    return url[:500] if url.startswith("https://") and "/images/" in url else ""


ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)


def asin_from_link(link: str | None) -> str:
    m = ASIN_RE.search(link or "")
    return m.group(1).upper() if m else ""


# ------------------------------------------------------------------ source status
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MONTH_RE = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})")
RUN_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M")

STATUSES = ["Out of Stock", "OOS Different Item", "OOS Variation", "High Price", "Low Inventory", "TimeFrame", "Low Price",
            "Manual Check", "Check Inventory", "Normal", "Not checked"]


PROBLEM_STATUSES = ("Out of Stock", "OOS Different Item", "OOS Variation", "High Price", "Low Inventory", "TimeFrame",
                    "Low Price", "Manual Check", "Check Inventory")


def issue_of(p, title_threshold: float) -> str:
    """The issue a product shows right now ('' = none): a problem status, else a listing/title problem."""
    if p["status"] in PROBLEM_STATUSES:
        return p["status"]
    title, ok = p["title"] or "", p["title_ok"] or ""
    if title and ok != title:
        if p["title_conflict"]:
            return "Listing changed"
        if p["title_score"] is not None and p["title_score"] < title_threshold:
            return "Title mismatch"
    return ""


def scrap_price(text: str | None) -> float | None:
    m = re.search(r"\$([\d,]+\.?\d*)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def parse_run(run_at: str | None) -> datetime | None:
    for fmt in RUN_FORMATS:
        try:
            return datetime.strptime((run_at or "").strip()[:19], fmt)
        except ValueError:
            continue
    return None


def lead_days(delivery: str | None, run_at: str | None) -> float | None:
    """Days between the check and the earliest delivery date mentioned in the text."""
    run = parse_run(run_at)
    best = earliest_delivery(delivery, run_at)
    return None if best is None or run is None else (best - run).total_seconds() / 86400


def earliest_delivery(delivery: str | None, run_at: str | None) -> datetime | None:
    """The earliest delivery date mentioned in Amazon's delivery text (year taken from the check date)."""
    run = parse_run(run_at)
    if not run:
        return None
    best = None
    for m in MONTH_RE.finditer(delivery or ""):
        try:
            d = datetime(run.year, MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        except ValueError:
            continue
        if d < run - timedelta(days=60):
            d = d.replace(year=run.year + 1)
        if best is None or d < best:
            best = d
    return best


def _get(p, key):
    try:
        return p[key]
    except (KeyError, IndexError):
        return None


def title_problem(p, title_threshold: float = 50) -> str:
    """'changed' when the Amazon title shows a different item, 'mismatch' when too few of the catalog words are in it,
    '' when it is fine or someone already accepted it. p needs title, title_ok, title_conflict, title_score."""
    title = _get(p, "title") or ""
    if not title or (_get(p, "title_ok") or "") == title:
        return ""
    if _get(p, "title_conflict"):
        return "changed"
    score = _get(p, "title_score")
    return "mismatch" if score is not None and score < title_threshold else ""


def variation_problem(p) -> bool:
    return variations.differs(_get(p, "variation"), _get(p, "amazon_variation"))


def compute_status(p, timeframe_days: float, title_threshold: float = 50) -> str:
    """p needs: availability, inventory, source_price, price, delivery, run_at, listed, and (for the listing checks)
    title, title_ok, title_conflict, title_score, variation, amazon_variation."""
    a = p["availability"] or ""
    al = a.lower()
    inv = bool(p["inventory"])
    src = p["source_price"]
    v = scrap_price(p["price"])
    lead = lead_days(p["delivery"], p["run_at"])
    # What Amazon sells is not our item (or not our variation): its price and stock say nothing about ours.
    tp = title_problem(p, title_threshold)
    if inv and tp == "changed":
        return "OOS Different Item"
    if inv and variation_problem(p):
        return "OOS Variation"
    if inv and lead is not None and lead > timeframe_days:         # a product with Inventory unchecked is already off sale: its delivery time is no issue
        return "TimeFrame"
    if inv and a == "Currently unavailable.":
        return "Out of Stock"
    if inv and "only" in al:
        return "Low Inventory"
    if inv and src is not None and v is not None and src < v:
        return "High Price"
    if inv and v and src is not None and src > v:
        return "Low Price"
    if inv and a == "Availability not found":
        return "Manual Check"
    if (not inv and p["listed"] and a and "availability not found" not in al
            and "currently unavailable" not in al and "only" not in al):
        return "Check Inventory"
    if inv and a and tp == "mismatch":             # nothing else is wrong, but the title doesn't look like our product
        return "Manual Check"
    return "Normal" if a else "Not checked"


def explain_status(p, timeframe_days: float) -> str:
    """One plain-English line saying why a product has its status."""
    v = scrap_price(p["price"])
    money = lambda x: "—" if x is None else f"${x:,.2f}"
    st = p["status"]
    if st in ("High Price", "Low Price"):
        return f"Amazon is {money(v)}, your source price is {money(p['source_price'])}."
    if st == "TimeFrame":
        lead = lead_days(p["delivery"], p["run_at"])
        return (f"Earliest delivery is {lead:.1f} days after the check (limit {timeframe_days:g})."
                if lead is not None else "Delivery is further out than allowed.")
    if st == "Out of Stock":
        return "Amazon says “Currently unavailable.”"
    if st == "Low Inventory":
        return f"Amazon says “{p['availability']}”."
    if st == "Check Inventory":
        return f"Amazon says “{p['availability']}” but inventory is off."
    if st == "OOS Different Item":
        return "The Amazon listing changed to a different item (pack count or size no longer matches)."
    if st == "OOS Variation":
        return f"Amazon shows “{p['amazon_variation']}” but the catalog variation is “{p['variation']}”."
    if st == "Manual Check":
        if title_problem(p) == "mismatch":
            return "The Amazon title doesn't look like this product. Open it and check."
        return "Availability couldn't be read."
    if st == "Not checked":
        return "No Amazon check yet."
    return "No issues from the last check."


# ------------------------------------------------------------------ title check
def title_check(catalog: str | None, amazon: str | None) -> tuple[int | None, str]:
    """Score 0-100 for how well the Amazon title matches the catalog name, plus a short note.
    The matching itself lives in titles.py (it also reports whether the listing really changed)."""
    from . import titles
    score, note, _ = titles.check(catalog, amazon)
    return score, note

# ------------------------------------------------------------------ pricing
# One fee table: the 3PL fee for a weight in ounces (first tier whose "max" is at least the rounded weight), plus the buffer.
# To change rates later, edit the tiers on the Pricing tab; there is no second set of rates.
DEFAULT_PRICING = {
    "markup": 1.53, "fee_buffer": 5, "active_table": "current",
    "tables": {
        "current": [{"max": a, "fee": b} for a, b in [
            (4, 4.36), (8, 4.68), (12, 5.62), (16, 6.33), (32, 9.21), (48, 10.93), (64, 12.06), (80, 13.08),
            (96, 14.26), (112, 15.28), (128, 16.4), (144, 17.53), (160, 18.66)]],
    },
}
TABLE_LABELS = {"current": "3PL fee by weight"}      # one fee table; the app keeps no second "new rates" table


def fee_for(weight: float | None, pr: dict) -> float | None:
    """3PL fee for a weight in ounces: weight rounded like the sheet's ROUND(), first tier that fits, plus buffer."""
    if weight is None:
        return None
    r = math.floor(weight + 0.5)
    for t in pr["tables"].get(pr["active_table"], []):
        if r <= t["max"]:
            return round(t["fee"] + pr["fee_buffer"], 2)
    return None


def auto_from_fee(source_price: float | None, fee: float | None, pr: dict) -> float | None:
    """(source + fee) x markup, rounded down to whole dollars, plus .99."""
    if fee is None or source_price is None:
        return None
    return math.floor((source_price + fee) * pr["markup"]) + 0.99


def auto_price(weight: float | None, source_price: float | None, pr: dict) -> float | None:
    """Automation selling price for a weight: look up the 3PL fee, then price from it."""
    return auto_from_fee(source_price, fee_for(weight, pr), pr)


def price_breakdown(weight, source_price, pr: dict) -> dict:
    fee = fee_for(weight, pr)
    if fee is None or source_price is None:
        return {"fee": fee, "cost": None, "selling": None, "auto": None, "margin": None}
    cost = source_price + fee
    selling = cost * pr["markup"]
    auto = math.floor(selling) + 0.99
    return {"fee": fee, "cost": round(cost, 2), "selling": round(selling, 2), "auto": round(auto, 2),
            "margin": round((auto - cost) / auto, 4) if auto else None}



