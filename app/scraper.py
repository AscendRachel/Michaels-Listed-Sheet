"""Scraper coordination: runs, work hand-out to scraper PCs, results, dashboard data, settings and backups.

The scraper PCs speak the same /api/agent/* protocol as the earlier version, so the existing
scraper app keeps working unchanged.
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from . import auth, db, rules, titles
from .db import (BACKUP_DIR, CONFIRM_NEW_WITHIN_HOURS, DB_PATH, LEASE_SECONDS, MAX_ATTEMPTS, OFFLINE_AFTER, conn,
                 log_activity, now_str, tx)
from .rules import strip_code


# ------------------------------------------------------------------ runs
def open_run():
    return conn().execute("SELECT * FROM runs WHERE state='open' ORDER BY id DESC LIMIT 1").fetchone()


def run_counts(run_id: int) -> dict:
    c = {"pending": 0, "claimed": 0, "done": 0, "failed": 0}
    for r in conn().execute("SELECT state, COUNT(*) n FROM tasks WHERE run_id=? GROUP BY state", (run_id,)):
        c[r["state"]] = r["n"]
    return c


def maybe_finish(c, run_id: int) -> None:
    n = c.execute("SELECT COUNT(*) FROM tasks WHERE run_id=? AND state IN ('pending','claimed')", (run_id,)).fetchone()[0]
    if n == 0:
        c.execute("UPDATE runs SET state='done', finished_at=? WHERE id=? AND state='open'", (now_str(), run_id))


def prune_history() -> None:
    days = int(float(db.get_settings().get("history_days", 180) or 180))
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with tx() as c:
        c.execute("DELETE FROM history WHERE COALESCE(at, run_at) < ?", (cutoff,))


def _create_run(c, started_by: str) -> int:
    cur = c.execute("INSERT INTO runs(started_at, started_by, total, state) VALUES(?,?,0,'open')", (now_str(), started_by))
    run_id = cur.lastrowid
    # Every active product with an Amazon link, oldest-checked first.
    c.execute("""INSERT INTO tasks(run_id, sku, seq, state)
        SELECT ?, sku, ROW_NUMBER() OVER (ORDER BY COALESCE(run_at,'') ASC, row_order ASC), 'pending'
        FROM products WHERE active=1 AND link LIKE '%amazon.%'""", (run_id,))
    total = c.execute("SELECT COUNT(*) FROM tasks WHERE run_id=?", (run_id,)).fetchone()[0]
    c.execute("UPDATE runs SET total=? WHERE id=?", (total, run_id))
    return run_id


def _touch_agent(c, agent_id, name, state, workers=None, run_id=None, message=None, done=None, captchas=None, rate=None):
    c.execute("""INSERT INTO agents(id, name, state, workers, last_seen, run_id, done_session, captchas, rate, message)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, state=excluded.state,
          workers=COALESCE(excluded.workers, agents.workers), last_seen=excluded.last_seen,
          run_id=COALESCE(excluded.run_id, agents.run_id), done_session=COALESCE(excluded.done_session, agents.done_session),
          captchas=COALESCE(excluded.captchas, agents.captchas), rate=COALESCE(excluded.rate, agents.rate),
          message=COALESCE(excluded.message, agents.message)""",
              (agent_id, name, state, workers, time.time(), run_id, done, captchas, rate, message))


def agent_start(body: dict) -> dict:
    agent_id, name = body["agent_id"], body.get("name") or "PC"
    if not open_run():
        try:
            prune_history()
        except sqlite3.Error:
            pass
    with tx() as c:
        run = c.execute("SELECT * FROM runs WHERE state='open' ORDER BY id DESC LIMIT 1").fetchone()
        if run:
            action, run_id = "joined", run["id"]
        else:
            last = c.execute("SELECT * FROM runs WHERE state='done' ORDER BY id DESC LIMIT 1").fetchone()
            recent = last and last["finished_at"] and \
                datetime.now() - (rules.parse_run(last["finished_at"]) or datetime.min) < timedelta(hours=CONFIRM_NEW_WITHIN_HOURS)
            if recent and not body.get("confirm_new"):
                return {"action": "confirm", "message":
                        f"The last full run finished at {last['finished_at']} ({last['total']:,} products). Start a new full run?"}
            run_id, action = _create_run(c, name), "created"
        _touch_agent(c, agent_id, name, "running", body.get("workers"), run_id, "Started")
        total = c.execute("SELECT total FROM runs WHERE id=?", (run_id,)).fetchone()["total"]
        s = db.get_settings(c)
    return {"action": action, "run_id": run_id, "total": total, "zip_code": s["zip_code"],
            "delay_min_seconds": float(s["delay_min_seconds"]), "delay_max_seconds": float(s["delay_max_seconds"])}


def agent_claim(body: dict) -> dict:
    agent_id, run_id = body["agent_id"], int(body["run_id"])
    n = max(1, min(int(body.get("n", 20)), 100))
    with tx() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run or run["state"] != "open":
            return {"items": [], "run_state": run["state"] if run else "missing"}
        now = time.time()
        rows = c.execute("""SELECT t.sku, p.asin, p.link FROM tasks t JOIN products p ON p.sku=t.sku
            WHERE t.run_id=? AND (t.state='pending' OR (t.state='claimed' AND t.lease_until < ?))
            ORDER BY t.seq LIMIT ?""", (run_id, now, n)).fetchall()
        c.executemany("UPDATE tasks SET state='claimed', claimed_by=?, lease_until=? WHERE run_id=? AND sku=?",
                      [(agent_id, now + LEASE_SECONDS, run_id, r["sku"]) for r in rows])
        counts = run_counts(run_id)
        state = "open"
        if not rows and counts["claimed"] == 0 and counts["pending"] == 0:
            maybe_finish(c, run_id)
            state = "done"
    return {"items": [dict(r) for r in rows], "run_state": state, "progress": {"total": run["total"], **counts}}


def agent_result(body: dict) -> dict:
    agent_id, run_id, sku = body["agent_id"], int(body["run_id"]), body["sku"]
    result = body.get("result", "error")
    with tx() as c:
        tf = float(db.get_settings(c)["timeframe_days"])
        task = c.execute("SELECT * FROM tasks WHERE run_id=? AND sku=?", (run_id, sku)).fetchone()
        p = c.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not task or not p:
            return {"ok": False, "reason": "unknown product"}
        if task["state"] == "done" and result not in ("ok", "unavailable_page"):
            return {"ok": True, "ignored": True}
        agent = c.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone()
        agent_name = agent["name"] if agent else agent_id
        if result in ("ok", "unavailable_page"):
            rec = dict(p)
            rec.update({"price": strip_code(body.get("price", "")), "availability": strip_code(body.get("availability", "")),
                        "delivery": strip_code(body.get("delivery", "")), "run_at": body.get("run_at") or now_str()})
            ovr = db.load_ovr(p["ovr"])
            if "status" in ovr:                       # a fresh check replaces a status someone typed over
                del ovr["status"]
                c.execute("UPDATE products SET ovr=? WHERE sku=?", (json.dumps(ovr) if ovr else None, sku))
            new_title = strip_code(body.get("title", ""))[:500]
            title = new_title or (p["title"] or "")
            score, note, conflict = titles.check(p["name"], title)
            # Amazon rewording a title someone already accepted is not a new problem: the approval follows the new wording.
            # (If the pack count or size changed, same_listing is False and the title is reviewed again.)
            title_ok = p["title_ok"]
            if title_ok and title and title_ok != title and titles.same_listing(
                    title_ok, title, max(float(db.get_settings(c).get("title_threshold") or 50), 55)):
                title_ok = title
            # The variation Amazon showed as selected. Old scraper PCs don't send it, and a scraper that couldn't read the
            # page sends nothing: either way the stored value is kept. An empty string means "this page has no variations".
            amazon_variation = p["amazon_variation"]
            if body.get("variation") is not None:
                amazon_variation = strip_code(str(body["variation"]))[:300]
            # The main Amazon image. Kept as an address without a size code; a missing or unusable one keeps the stored image.
            amazon_image = rules.amazon_image_base(body.get("image")) or p["amazon_image"]
            rec.update({"title": title, "title_ok": title_ok, "title_score": score, "title_conflict": conflict,
                        "amazon_variation": amazon_variation})
            status = rules.compute_status(rec, tf, float(db.get_settings(c).get("title_threshold") or 50))
            price_num = rules.scrap_price(rec["price"])
            prev, ack = p["prev_price_num"], p["price_alert_ack"]
            if price_num is not None and p["price_num"] is not None and abs(price_num - p["price_num"]) > 0.004:
                prev, ack = p["price_num"], 0              # the price moved since the last check: raise an alert
            c.execute("""UPDATE products SET price=?, availability=?, delivery=?, run_at=?, title=?, status=?,
                last_result=?, checked_by=?, title_score=?, title_note=?, title_conflict=?, title_ok=?, price_num=?,
                prev_price_num=?, price_alert_ack=?, amazon_variation=?, amazon_image=? WHERE sku=?""",
                      (rec["price"], rec["availability"], rec["delivery"], rec["run_at"], title, status, result,
                       agent_name, score, note, conflict, title_ok, price_num if price_num is not None else p["price_num"],
                       prev, ack, amazon_variation, amazon_image, sku))
            c.execute("""INSERT INTO history(sku, run_id, price, availability, delivery, run_at, status, result, agent, at, title)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (sku, run_id, rec["price"], rec["availability"], rec["delivery"],
                                                   rec["run_at"], status, result, agent_name, now_str(), new_title))
            c.execute("UPDATE tasks SET state='done', result=?, claimed_by=? WHERE run_id=? AND sku=?",
                      (result, agent_id, run_id, sku))
        else:
            attempts = (task["attempts"] or 0) + 1
            new_state = "failed" if attempts >= MAX_ATTEMPTS else "pending"
            c.execute("UPDATE tasks SET state=?, attempts=?, result=?, claimed_by=NULL, lease_until=NULL WHERE run_id=? AND sku=?",
                      (new_state, attempts, result, run_id, sku))
            if new_state == "failed":
                c.execute("UPDATE products SET last_result=?, checked_by=?, updated_at=? WHERE sku=?",
                          (result, agent_name, now_str(), sku))
        maybe_finish(c, run_id)
    return {"ok": True}


def agent_release(body: dict) -> dict:
    agent_id, run_id, skus = body["agent_id"], int(body.get("run_id") or 0), body.get("skus")
    with tx() as c:
        if skus:
            c.executemany("""UPDATE tasks SET state='pending', claimed_by=NULL, lease_until=NULL
                WHERE run_id=? AND sku=? AND state='claimed' AND claimed_by=?""", [(run_id, s, agent_id) for s in skus])
        else:
            c.execute("""UPDATE tasks SET state='pending', claimed_by=NULL, lease_until=NULL
                WHERE run_id=? AND state='claimed' AND claimed_by=?""", (run_id, agent_id))
        _touch_agent(c, agent_id, body.get("name") or "PC", body.get("state") or "stopped", message=body.get("message"),
                     done=body.get("done"), captchas=body.get("captchas"))
    return {"ok": True}


def agent_heartbeat(body: dict) -> dict:
    with tx() as c:
        _touch_agent(c, body["agent_id"], body.get("name") or "PC", body.get("state") or "running", body.get("workers"),
                     body.get("run_id"), body.get("message"), body.get("done"), body.get("captchas"), body.get("rate"))
    run = open_run()
    prog = {"run_id": run["id"], "total": run["total"], **run_counts(run["id"])} if run else None
    return {"ok": True, "progress": prog}


AGENT_ROUTES = {"start": agent_start, "claim": agent_claim, "result": agent_result,
                "release": agent_release, "heartbeat": agent_heartbeat}


# ------------------------------------------------------------------ dashboard
def dashboard() -> dict:
    c = conn()
    run = open_run()
    last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    show = run or last
    run_info = None
    if show:
        counts = run_counts(show["id"])
        first = c.execute("SELECT MIN(at) a FROM history WHERE run_id=?", (show["id"],)).fetchone()["a"]
        recent = c.execute("SELECT COUNT(*) FROM history WHERE run_id=? AND at >= ?",
                           (show["id"], (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S"))).fetchone()[0]
        since_first = (datetime.now() - (rules.parse_run(first) or datetime.now())).total_seconds() if first else 0
        window = min(900, since_first)
        rate = int(recent / window * 3600) if window >= 60 else 0           # per hour over the last 15 minutes
        left = counts["pending"] + counts["claimed"]
        run_info = {**dict(show), **counts, "rate_per_hour": rate, "first_result_at": first,
                    "eta_hours": round(left / rate, 1) if rate and show["state"] == "open" else None}
    now = time.time()
    agents = []
    for a in c.execute("SELECT * FROM agents ORDER BY name"):
        d = dict(a)
        d["online"] = now - (a["last_seen"] or 0) < OFFLINE_AFTER
        if not d["online"]:
            d["state"] = "offline"
        d["last_seen_ago"] = int(now - (a["last_seen"] or 0))
        if run:
            d["claimed_now"] = c.execute("SELECT COUNT(*) FROM tasks WHERE run_id=? AND claimed_by=? AND state='claimed'",
                                         (run["id"], a["id"])).fetchone()[0]
            d["done_this_run"] = c.execute("SELECT COUNT(*) FROM tasks WHERE run_id=? AND claimed_by=? AND state='done'",
                                           (run["id"], a["id"])).fetchone()[0]
        agents.append(d)
    totals = c.execute("""SELECT COUNT(*) total, SUM(CASE WHEN link LIKE '%amazon.%' THEN 1 ELSE 0 END) scrapable,
        SUM(CASE WHEN COALESCE(link,'')='' THEN 1 ELSE 0 END) no_link FROM products WHERE active=1""").fetchone()
    imp = c.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    runs = [dict(r) for r in c.execute("SELECT id, started_at, started_by, finished_at, total, state FROM runs ORDER BY id DESC LIMIT 8")]
    return {"run": run_info, "agents": agents, "totals": dict(totals), "recent_runs": runs,
            "last_import": ({"at": imp["at"], "filename": imp["filename"], **json.loads(imp["summary"])} if imp else None),
            "settings": db.get_settings(c), "server_time": now_str()}


def cancel_run(user: str) -> None:
    with tx() as c:
        run = c.execute("SELECT * FROM runs WHERE state='open' ORDER BY id DESC LIMIT 1").fetchone()
        if run:
            c.execute("UPDATE runs SET state='cancelled', finished_at=? WHERE id=?", (now_str(), run["id"]))
            c.execute("UPDATE tasks SET state='failed', result='cancelled' WHERE run_id=? AND state IN ('pending','claimed')",
                      (run["id"],))
        log_activity(c, user, "cancelled the scraper run")


def save_settings(body: dict, user: str) -> dict:
    synonyms_changed = False
    with tx() as c:
        changed = []
        if "title_synonyms" in body:                       # free text, may be emptied
            text = "\n".join(ln.strip() for ln in str(body["title_synonyms"]).splitlines() if ln.strip())[:4000]
            if text != (db.get_settings(c).get("title_synonyms") or ""):
                db.set_setting(c, "title_synonyms", text)
                synonyms_changed = True
                changed.append(f"title synonyms ({len(text.splitlines())} groups)")
        for k in db.DEFAULT_SETTINGS:
            if k == "title_synonyms":
                continue
            if k in body and str(body[k]).strip():
                v = str(body[k]).strip()
                if k == "zip_code" and not re.fullmatch(r"\d{5}", v):
                    raise ValueError("ZIP code must be 5 digits.")
                if k != "zip_code":
                    float(v)
                db.set_setting(c, k, v)
                changed.append(f"{k}={v}")
        if "timeframe_days" in body or "title_threshold" in body:
            db.recompute_statuses(c)
        if changed:
            log_activity(c, user, "changed scraper settings", "", ", ".join(changed))
        result = {"ok": True, "settings": db.get_settings(c)}
    if synonyms_changed:                                  # the words now mean something different: re-judge every title
        db.rebuild_title_model()
        with tx() as c:
            db.recompute_titles(c)
    return result


# ------------------------------------------------------------------ backups
def make_backup(dest) -> None:
    """Consistent copy of the live database (safe while the app is in use)."""
    src, dst = sqlite3.connect(str(DB_PATH)), sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def daily_backup() -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"optimizer-{datetime.now():%Y-%m-%d}.db.gz"
    if target.exists():
        return
    tmp = BACKUP_DIR / "tmp-backup.db"
    make_backup(tmp)
    with open(tmp, "rb") as f_in, gzip.open(target, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp.unlink()
    for old in sorted(BACKUP_DIR.glob("optimizer-*.db.gz"))[:-7]:      # keep the newest 7
        old.unlink()


def backup_loop() -> None:
    while True:
        try:
            daily_backup()
        except Exception as e:                                          # never let a failed backup kill the thread
            print(f"Backup failed: {e}", flush=True)
        time.sleep(3600)                                                # checks hourly, makes one copy per day


def backup_download() -> bytes:
    BACKUP_DIR.mkdir(exist_ok=True)
    tmp = BACKUP_DIR / f"download-{secrets.token_hex(4)}.db"
    make_backup(tmp)
    try:
        return gzip.compress(tmp.read_bytes(), compresslevel=6)
    finally:
        tmp.unlink()


def start_background() -> None:
    threading.Thread(target=backup_loop, daemon=True).start()
    from . import sold
    sold.start_background()

