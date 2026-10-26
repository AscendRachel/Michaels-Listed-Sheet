import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_drag_fill_series():
    r = subprocess.run(["node", str(HERE / "fill_series.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_create_batch_reports_the_new_skus(loaded):
    rows = [{"sku": "NEW-1", "name": "Frame", "link": "https://www.amazon.com/dp/B0ABCDEFGH", "source_price": 5, "weight": 8, "category": "Decor"},
            {"sku": "NEW-2", "name": "Frame two", "link": "https://www.amazon.com/dp/B0ABCDEFGI", "source_price": 5, "weight": 8, "category": "Decor"},
            {"sku": "SKU-1", "name": "Taken", "link": "https://www.amazon.com/dp/B0ABCDEFGJ", "source_price": 5, "weight": 8, "category": "Decor"}]
    r = loaded.post("/api/products/create-batch", json={"rows": rows}).json()
    assert r["created"] == 2 and r["skipped"] == 1 and r["skus"] == ["NEW-1", "NEW-2"]


def test_new_rows_keep_blank_flags_and_defaults_stay_for_the_add_page(loaded):
    base = {"name": "Frame", "link": "https://www.amazon.com/dp/B0ABCDEFGH", "source_price": 5, "weight": 8, "category": "Decor"}
    blank = {**base, "sku": "BLANK-1", "listed": False, "inventory": False, "source": "", "listing_status": ""}
    on = {**base, "sku": "ON-1", "link": "https://www.amazon.com/dp/B0ABCDEFGI", "listed": True, "inventory": "TRUE", "source": "Amazon", "listing_status": "Live"}
    page = {**base, "sku": "PAGE-1", "link": "https://www.amazon.com/dp/B0ABCDEFGJ"}               # what the Add products page sends
    assert loaded.post("/api/products/create-batch", json={"rows": [blank, on, page]}).json()["created"] == 3
    get = lambda s: loaded.get(f"/api/products/{s}").json()["product"]
    b, o, p = get("BLANK-1"), get("ON-1"), get("PAGE-1")
    assert (b["listed"], b["inventory"], b["source"], b["listing_status"]) == (0, 0, "", "")
    assert (o["listed"], o["inventory"], o["source"], o["listing_status"]) == (1, 1, "Amazon", "Live")
    assert (p["listed"], p["inventory"], p["source"], p["listing_status"]) == (1, 1, "Amazon", "Live")


def test_rows_typed_into_the_sheet_need_nothing(loaded):
    post = lambda rows: loaded.post("/api/products/create-batch", json={"rows": rows, "loose": True}).json()
    # only a remark, no SKU, no name, no link, no price: still added, with a temporary SKU
    r = post([{"remarks": "check this later"}, {"name": "Just a name"}])
    assert r["created"] == 2 and [m["sku"] for m in r["rows"]] == ["NEW-000001", "NEW-000002"] and [m["i"] for m in r["rows"]] == [0, 1]
    p = loaded.get("/api/products/NEW-000002").json()["product"]
    assert p["name"] == "Just a name" and not p["link"] and p["weight"] is None and p["auto_price"] is None
    # they appear in the Data quality queues until filled in
    c = loaded.get("/api/queues").json()["counts"]
    assert c["dq-weight"] >= 2 and c["dq-category"] >= 2 and c["dq-link"] >= 2
    # the next temporary SKU continues the numbering, and a SKU that is given must still be valid and unused
    assert post([{}])["rows"][0]["sku"] == "NEW-000003"
    assert post([{"sku": "SKU-1"}])["created"] == 0                   # SKU-1 already exists
    assert post([{"sku": "bad sku!"}])["created"] == 0
    assert post([{"sku": "MINE-1"}, {"sku": "mine-1"}])["created"] == 1  # the same SKU twice in one go: only the first
    # a temporary SKU can be renamed like any other, and the strict Add products page still requires everything
    cells_ = loaded.post("/api/sheet/cells", json={"edits": [{"sku": "NEW-000001", "field": "sku", "value": "REAL-7"}]}).json()
    assert cells_["renamed"] == {"NEW-000001": "REAL-7"}
    assert loaded.post("/api/products/create-batch", json={"rows": [{"remarks": "x"}]}).json()["created"] == 0

@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("tz", ["Asia/Manila", "America/New_York", "UTC"])
def test_times_from_the_server_read_correctly_in_any_browser_time_zone(tz):
    r = subprocess.run(["node", str(HERE / "ago_offset.mjs")], capture_output=True, text=True, env={**os.environ, "TZ": tz})
    assert r.returncode == 0, r.stdout + r.stderr
