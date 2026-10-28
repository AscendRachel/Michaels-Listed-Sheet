import ast
import asyncio
import textwrap
from pathlib import Path

from tests.test_sheet import cells, row

ROOT = Path(__file__).resolve().parent.parent
AGENT = {}


def _agent_headers(c):
    return {"X-Agent-Key": c.get("/api/scraper/key").json()["key"]}


def test_recheck_creates_a_small_run_with_only_those_rows(loaded):
    r = loaded.post("/api/scraper/recheck", json={"skus": ["SKU-1", "SKU-1"]}).json()
    assert r["queued"] == 1 and r["joined_open_run"] is False
    dash = loaded.get("/api/scraper").json()["run"]
    assert dash["kind"] == "recheck" and dash["total"] == 1 and dash["pending"] == 1
    # a scraper PC that starts now joins it and is handed exactly that row
    h = _agent_headers(loaded)
    run = loaded.post("/api/agent/start", json={"agent_id": "pc", "name": "PC"}, headers=h).json()
    assert run["action"] == "joined" and run["total"] == 1
    got = loaded.post("/api/agent/claim", json={"agent_id": "pc", "run_id": run["run_id"], "n": 20}, headers=h).json()
    assert [i["sku"] for i in got["items"]] == ["SKU-1"]
    assert any(a["action"] == "asked for a re-check on Amazon" for a in loaded.get("/api/activity").json()["items"])
    # nothing to check, or no Amazon link: refused with a plain message
    assert loaded.post("/api/scraper/recheck", json={"skus": []}).status_code == 400
    assert loaded.post("/api/scraper/recheck", json={"skus": ["nope"]}).status_code == 400


def test_recheck_goes_to_the_front_of_a_run_that_is_already_open(loaded):
    h = _agent_headers(loaded)
    run = loaded.post("/api/agent/start", json={"agent_id": "pc", "name": "PC"}, headers=h).json()
    total = run["total"]
    assert total >= 2
    last = "SKU-3"                                    # SKU-4 has no Amazon link, so it is never in a run
    r = loaded.post("/api/scraper/recheck", json={"skus": [last]}).json()
    assert r["joined_open_run"] is True and r["run_id"] == run["run_id"]
    got = loaded.post("/api/agent/claim", json={"agent_id": "pc", "run_id": run["run_id"], "n": 1}, headers=h).json()
    assert got["items"][0]["sku"] == last
    # an already-finished row is queued again and its new result replaces the old one
    loaded.post("/api/agent/result", json={"agent_id": "pc", "run_id": run["run_id"], "sku": last, "result": "ok", "price": "$9.00",
                                           "availability": "In Stock", "delivery": "", "run_at": "2026-03-04 09:15:30"}, headers=h)
    loaded.post("/api/scraper/recheck", json={"skus": [last]})
    again = loaded.post("/api/agent/claim", json={"agent_id": "pc", "run_id": run["run_id"], "n": 1}, headers=h).json()
    assert again["items"][0]["sku"] == last
    assert loaded.get("/api/scraper").json()["run"]["total"] == total          # no duplicate task was added


def test_a_recheck_run_does_not_count_as_the_last_full_run(loaded):
    loaded.post("/api/scraper/recheck", json={"skus": ["SKU-1"]})
    h = _agent_headers(loaded)
    run = loaded.post("/api/agent/start", json={"agent_id": "pc", "name": "PC"}, headers=h).json()
    loaded.post("/api/agent/claim", json={"agent_id": "pc", "run_id": run["run_id"], "n": 5}, headers=h)
    loaded.post("/api/agent/result", json={"agent_id": "pc", "run_id": run["run_id"], "sku": "SKU-1", "result": "ok", "price": "$9.00",
                                           "availability": "In Stock", "delivery": "", "run_at": "2026-03-04 09:15:30"}, headers=h)
    assert loaded.get("/api/scraper").json()["run"]["state"] == "done"
    # starting the scraper afterwards begins a normal full run without asking "start a new full run?"
    nxt = loaded.post("/api/agent/start", json={"agent_id": "pc", "name": "PC"}, headers=h).json()
    assert nxt["action"] == "created" and nxt["total"] > 1


# ---- the scraper app: a slow page must not be reported as sold out
def _scraper_parts():
    src = (ROOT / "scraper" / "scraper_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = {"asyncio": asyncio}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "BUYBOX_SELECTOR":
            exec(ast.get_source_segment(src, node), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "read_looks_empty":
            exec(ast.get_source_segment(src, node), ns)
        if isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, ast.AsyncFunctionDef) and f.name == "read_again":
                    exec(textwrap.dedent(ast.get_source_segment(src, f)), ns)
    return ns


def test_an_empty_looking_read_is_doubted_but_a_complete_one_is_not():
    ns = _scraper_parts()
    doubt = ns["read_looks_empty"]
    assert doubt({"price": "Price not found", "availability": "Currently unavailable.", "delivery": "Delivery not found"})
    assert doubt({"price": "Price not found", "availability": "Availability not found", "delivery": "Delivery not found"})
    assert doubt({"price": "$12.99", "availability": "In Stock", "delivery": "Delivery not found"})
    assert not doubt({"price": "$12.99", "availability": "In Stock", "delivery": "FREE delivery Wednesday"})
    assert not doubt({"price": "$12.99", "availability": "Currently unavailable.", "delivery": "Delivery not found"})


class _FakePage:
    def __init__(self):
        self.waits = self.reloads = 0

    async def wait_for_selector(self, *a, **k):
        pass

    async def wait_for_timeout(self, ms):
        self.waits += 1

    async def reload(self, **k):
        self.reloads += 1


class _FakeScraper:
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


def _run_read_again(reads):
    ns = _scraper_parts()
    seq = iter(reads)

    async def extract(page):
        return next(seq)

    async def gate(page):
        pass

    async def captcha(page):
        return False

    ns.update(extract=extract, continue_shopping_gate=gate, is_captcha=captcha)
    page, me = _FakePage(), _FakeScraper()
    first = {"price": "Price not found", "availability": "Currently unavailable.", "delivery": "Delivery not found"}
    out = asyncio.run(ns["read_again"](me, page, {"sku": "S1"}, 1, first, "no price and no stock line", 1000))
    return out, page, me


def test_a_page_that_finishes_loading_is_read_correctly_the_second_time():
    good = {"price": "$12.99", "availability": "Only 1 left in stock - order soon.", "delivery": "FREE delivery Wednesday"}
    out, page, me = _run_read_again([good])
    assert out == good and page.reloads == 0 and me.lines and "read again" in me.lines[0]


def test_a_really_sold_out_product_is_still_reported_as_sold_out():
    empty = {"price": "Price not found", "availability": "Currently unavailable.", "delivery": "Delivery not found"}
    out, page, me = _run_read_again([empty, empty])
    assert out == empty and page.reloads == 1                          # waited, read, reloaded once, read again: then believed


def test_a_dead_browser_is_told_apart_from_a_slow_page():
    src = (ROOT / "scraper" / "scraper_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "browser_gone":
            exec(ast.get_source_segment(src, node), ns)
    gone = ns["browser_gone"]
    assert gone(Exception("Page.goto: Connection closed while reading from the driver"))
    assert gone(Exception("Page.goto: Target page, context or browser has been closed"))
    assert not gone(Exception("Page.goto: net::ERR_CONNECTION_RESET at https://www.amazon.com"))
    assert not gone(Exception("Page.goto: Timeout 30000ms exceeded."))
