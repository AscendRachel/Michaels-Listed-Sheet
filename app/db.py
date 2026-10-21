"""SQLite access, schema and settings.

The schema is a superset of the earlier "GreentekOptimizer" database, so an existing
optimizer.db opens as-is: missing columns are added and the new computed columns are
filled in on first start.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import rules, titles

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GT_DATA", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("GT_DB", DATA_DIR / "optimizer.db"))
BACKUP_DIR = DB_PATH.parent / "backups"

PORT = int(os.environ.get("GT_PORT", "8000"))
AMAZON_BASE = os.environ.get("GT_AMAZON_BASE", "https://www.amazon.com").rstrip("/")
LEASE_SECONDS = int(os.environ.get("GT_LEASE_SECONDS", 15 * 60))   # unfinished scraper work returns to the pool
MAX_ATTEMPTS = 3                  # CAPTCHA / timeout / error retries before a product is marked failed
OFFLINE_AFTER = 90                # seconds without a heartbeat before a scraper PC shows as offline
CONFIRM_NEW_WITHIN_HOURS = 12     # a run finished this recently asks "Start a new full run?"

_WRITE = threading.RLock()
_local = threading.local()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn() -> sqlite3.Connection:
    """One connection per thread (WAL lets readers run alongside the single writer)."""
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(str(DB_PATH), isolation_level=None, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


@contextmanager
def tx():
    """Serialised write transaction: commits on success, rolls back on error."""
    with _WRITE:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")


# ------------------------------------------------------------------ schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  sku TEXT PRIMARY KEY, asin TEXT, link TEXT, name TEXT, category TEXT,
  source_price REAL, weight REAL, inventory INTEGER, listed INTEGER, listing_status TEXT,
  row_order INTEGER, active INTEGER DEFAULT 1,
  price TEXT, availability TEXT, delivery TEXT, run_at TEXT, title TEXT,
  status TEXT, last_result TEXT, checked_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, run_id INTEGER, price TEXT, availability TEXT,
  delivery TEXT, run_at TEXT, status TEXT, result TEXT, agent TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, started_by TEXT, finished_at TEXT,
  total INTEGER, state TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  run_id INTEGER, sku TEXT, seq INTEGER, state TEXT, claimed_by TEXT, lease_until REAL,
  attempts INTEGER DEFAULT 0, result TEXT, PRIMARY KEY(run_id, sku));
CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY, name TEXT, state TEXT, workers INTEGER, last_seen REAL,
  run_id INTEGER, done_session INTEGER, captchas INTEGER, rate REAL, message TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, filename TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS michaels_imports(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, user TEXT,
  filename TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, user TEXT,
  action TEXT, sku TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, name TEXT, pw_hash TEXT, salt TEXT, role TEXT,
  created_at TEXT, disabled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, username TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS brand_logo(id INTEGER PRIMARY KEY CHECK (id=1), ctype TEXT, data BLOB, at TEXT);
CREATE TABLE IF NOT EXISTS worklists(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, user_name TEXT, name TEXT, params TEXT,
  skus TEXT, created_at TEXT, state TEXT DEFAULT 'open', finished_at TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS column_locks(col TEXT PRIMARY KEY, mode TEXT, users TEXT, note TEXT, by TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS deleted_products(id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, at TEXT, by TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS custom_columns(key TEXT PRIMARY KEY, label TEXT, type TEXT, after TEXT,
  created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS saved_views(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, name TEXT,
  params TEXT, shared INTEGER DEFAULT 0, created_at TEXT);
"""

# Columns added over time (safe to run on an existing database)
PRODUCT_COLUMNS = [
    ("upc", "TEXT"), ("variation", "TEXT"), ("source", "TEXT"), ("manual_status", "TEXT"), ("remarks", "TEXT"),
    ("michaels_price", "REAL"), ("michaels_qty", "REAL"), ("sold_2025", "INTEGER"), ("sold_2026", "INTEGER"),
    ("created_in_app", "INTEGER DEFAULT 0"), ("created_at", "TEXT"), ("updated_by", "TEXT"),
    ("title_score", "INTEGER"), ("title_note", "TEXT"), ("title_ok", "TEXT"),
    ("michaels_sku_number", "TEXT"), ("michaels_updated_at", "TEXT"),
    # new in this version: stored so queues and sorting run in SQL instead of Python loops
    ("fee", "REAL"), ("auto_price", "REAL"), ("price_num", "REAL"),
    ("prev_price_num", "REAL"), ("price_alert_ack", "INTEGER DEFAULT 0"),
    # manual overrides of calculated columns ({"fee": 9.5, ...}) and values of custom columns ({"x_notes": "..."}), as JSON
    ("ovr", "TEXT"), ("extra", "TEXT"),
    ("images", "TEXT"),            # JSON list of marketplace image URLs, refreshed by every Michaels import
    ("amazon_image", "TEXT"),                 # the main Amazon product image (size-free address), read by the scraper
    ("amazon_variation", "TEXT"),             # the variation Amazon showed as selected at the last check (scraped)
    ("title_conflict", "INTEGER DEFAULT 0"),   # 1 = the Amazon title states a pack count / size that disagrees with the catalog
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_products_status ON products(status)",
    "CREATE INDEX IF NOT EXISTS ix_products_active ON products(active, row_order)",
    "CREATE INDEX IF NOT EXISTS ix_products_asin ON products(asin)",
    "CREATE INDEX IF NOT EXISTS ix_products_upc ON products(upc)",
    "CREATE INDEX IF NOT EXISTS ix_history_sku ON history(sku, id)",
    "CREATE INDEX IF NOT EXISTS ix_history_run ON history(run_id, at)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_state ON tasks(run_id, state, seq)",
    "CREATE INDEX IF NOT EXISTS ix_activity_sku ON activity(sku, id)",
    "CREATE INDEX IF NOT EXISTS ix_deleted_sku ON deleted_products(sku, id)",
]

DEFAULT_SETTINGS = {"zip_code": "32809", "timeframe_days": "6", "delay_min_seconds": "1.5", "delay_max_seconds": "3.5",
                    "history_days": "180", "title_threshold": "50",
                    # words the team treats as the same thing when comparing titles, one group per line: "sticker, decal"
                    "title_synonyms": ""}
STATUS_ALGO = "2"       # bump when compute_status changes: every stored status is re-derived once at startup
TITLE_ALGO = "2"       # bump when the title matching changes: every stored title verdict is recomputed once


def _columns(c, table):
    return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def init() -> None:
    with _WRITE:
        c = conn()
        c.executescript(SCHEMA)
        have = _columns(c, "products")
        for col, typ in PRODUCT_COLUMNS:
            if col not in have:
                c.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
        for col in ("issue_before", "issue_after", "changes"):
            if col not in _columns(c, "activity"):
                c.execute(f"ALTER TABLE activity ADD COLUMN {col} TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS ix_activity_at ON activity(at)")
        if "title" not in _columns(c, "history"):
            c.execute("ALTER TABLE history ADD COLUMN title TEXT")
        for stmt in INDEXES:
            c.execute(stmt)
        _backfill(c)
    from . import protect
    protect.seed()                      # the default column protections (the Michaels columns), once


def _backfill(c) -> None:
    """Fill the computed columns for rows saved by an earlier version."""
    todo = c.execute("""SELECT sku, weight, source_price, price, name, title FROM products
        WHERE (fee IS NULL AND weight IS NOT NULL) OR (price_num IS NULL AND COALESCE(price,'')<>'')
           OR (title_score IS NULL AND COALESCE(title,'')<>'')""").fetchall()
    if not todo:
        return
    pr = get_pricing(c)
    c.execute("BEGIN IMMEDIATE")
    try:
        for r in todo:
            score, note, conflict = titles.check(r["name"], r["title"])
            c.execute("UPDATE products SET fee=?, auto_price=?, price_num=?, title_score=?, title_note=?, title_conflict=? WHERE sku=?",
                      (rules.fee_for(r["weight"], pr), rules.auto_price(r["weight"], r["source_price"], pr),
                       rules.scrap_price(r["price"]), score, note, conflict, r["sku"]))
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ settings
def get_settings(c=None) -> dict:
    c = c or conn()
    s = dict(DEFAULT_SETTINGS)
    for r in c.execute("SELECT key, value FROM settings"):
        s[r["key"]] = r["value"]
    return s


def get_pricing(c=None) -> dict:
    c = c or conn()
    row = c.execute("SELECT value FROM settings WHERE key='pricing'").fetchone()
    p = json.loads(json.dumps(rules.DEFAULT_PRICING))
    if row:
        try:
            p.update(json.loads(row["value"]))
        except ValueError:
            pass
    # Only one fee table exists now. Settings saved by an older version may still carry a second one or point at it:
    # keep the "current" table (the old rates) and drop the rest.
    p["tables"] = {"current": (p.get("tables") or {}).get("current") or rules.DEFAULT_PRICING["tables"]["current"]}
    p["active_table"] = "current"
    return p


def set_setting(c, key: str, value: str) -> None:
    c.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))


def log_activity(c, user: str, action: str, sku: str = "", detail: str = "",
                 issue_before: str | None = None, issue_after: str | None = None, changes: list | None = None) -> None:
    """issue_before/after (from rules.issue_of) are given for edits to a product; they feed the productivity report.
    changes = [{"f": field, "o": old, "n": new}, ...] is what "Show edit history" reads for a single cell."""
    c.execute("INSERT INTO activity(at, user, action, sku, detail, issue_before, issue_after, changes) VALUES(?,?,?,?,?,?,?,?)",
              (now_str(), (user or "Someone")[:60], action, sku, (detail or "")[:500], issue_before, issue_after,
               json.dumps(changes, default=str)[:8000] if changes else None))


def issue_now(c, sku: str, tt: float) -> str:
    p = c.execute("SELECT status, title, title_ok, title_conflict, title_score FROM products WHERE sku=?", (sku,)).fetchone()
    return rules.issue_of(p, tt) if p else ""


def load_ovr(raw) -> dict:
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def load_list(raw) -> list:
    try:
        v = json.loads(raw) if raw else []
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def derive_prices(weight, source_price, ovr: dict, pr: dict) -> tuple:
    """(fee, automation price), honouring manual overrides of either column."""
    fee = ovr["fee"] if ovr.get("fee") is not None else rules.fee_for(weight, pr)
    if ovr.get("auto_price") is not None:
        return fee, ovr["auto_price"]
    return fee, rules.auto_from_fee(source_price, fee, pr)


def recompute_prices(c) -> int:
    """Re-apply the pricing rules to every product (after the rules or a fee table change).
    Cells a person overrode by hand keep their value."""
    pr = get_pricing(c)
    rows = c.execute("SELECT sku, weight, source_price, ovr FROM products").fetchall()
    c.executemany("UPDATE products SET fee=?, auto_price=? WHERE sku=?",
                  [(*derive_prices(r["weight"], r["source_price"], load_ovr(r["ovr"]), pr), r["sku"]) for r in rows])
    return len(rows)


def synonym_lines(c=None) -> list[str]:
    raw = get_settings(c).get("title_synonyms", "") or ""
    return titles.DEFAULT_SYNONYMS + [ln.strip() for ln in raw.splitlines() if ln.strip()]


def rebuild_title_model(c=None) -> None:
    """Learn how identifying each word is from the whole catalog (names and Amazon titles), and load the
    team's synonym list. Called at startup and after an import or a synonym change."""
    c = c or conn()
    base = titles.Model(synonyms=synonym_lines(c))
    df: Counter = Counter()
    n = 0
    for r in c.execute("SELECT name, title FROM products WHERE active=1"):
        for text in (r["name"], r["title"]):
            if text:
                df.update(set(titles.Fingerprint(text, base).words))
                n += 1
    titles.set_model(titles.Model(df, n, synonym_lines(c)))


def recompute_titles(c) -> int:
    """Re-judge every stored Amazon title against its catalog name. A title someone already accepted stays accepted
    when Amazon only reworded it."""
    tt = float(get_settings(c).get("title_threshold") or 50)
    rows = c.execute("SELECT sku, name, title, title_ok FROM products WHERE COALESCE(title,'')<>''").fetchall()
    out = []
    for r in rows:
        score, note, conflict = titles.check(r["name"], r["title"])
        ok = r["title_ok"]
        if ok and ok != r["title"] and titles.same_listing(ok, r["title"], max(tt, 55)):
            ok = r["title"]
        out.append((score, note, conflict, ok, r["sku"]))
    c.executemany("UPDATE products SET title_score=?, title_note=?, title_conflict=?, title_ok=? WHERE sku=?", out)
    recompute_statuses(c)                    # the listing checks feed the status
    return len(out)


def recompute_statuses(c) -> None:
    st = get_settings(c)
    tf, tt = float(st["timeframe_days"]), float(st["title_threshold"])
    rows = c.execute("SELECT * FROM products").fetchall()
    c.executemany("UPDATE products SET status=? WHERE sku=?",
                  [(load_ovr(r["ovr"]).get("status") or rules.compute_status(r, tf, tt), r["sku"]) for r in rows])


