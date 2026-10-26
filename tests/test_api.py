import math

from fastapi.testclient import TestClient

from app import auth
from app.main import app


def test_requires_login(clean):
    c = TestClient(app)
    assert c.get("/api/products").status_code == 401
    assert c.get("/api/products").json() == {"error": "Please sign in."}
    assert c.get("/", follow_redirects=False).status_code in (302, 307)


def test_login_and_wrong_password(clean):
    auth.add_user("amy", "Amy", "password123", "member")
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "amy", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"username": "amy", "password": "password123"}).status_code == 200
    assert c.get("/api/me").json()["role"] == "member"
    assert c.get("/api/team").status_code == 403          # members can't manage the team
    assert c.post("/api/pricing", json={"pricing": {}}).status_code == 403


def test_lockout_after_failed_logins(clean):
    auth.add_user("amy", "Amy", "password123", "member")
    c = TestClient(app)
    for _ in range(8):
        c.post("/api/login", json={"username": "amy", "password": "bad"})
    assert c.post("/api/login", json={"username": "amy", "password": "password123"}).status_code == 429


def test_import_computes_everything(loaded):
    r = loaded.get("/api/products", params={"size": 50}).json()
    by = {p["sku"]: p for p in r["items"]}
    assert r["total"] == 4
    assert by["SKU-1"]["auto_price"] == 32.99 and by["SKU-1"]["fee"] == 11.33
    assert by["SKU-1"]["asin"] == "B000000001"
    assert by["SKU-2"]["link"].endswith("/dp/B000000002")        # tracking params stripped
    assert by["SKU-1"]["status"] == "High Price"                 # amazon 12.50 > source 10
    assert by["SKU-2"]["status"] == "Out of Stock"
    assert by["SKU-3"]["auto_price"] is None


def test_queue_counts_and_filters(loaded):
    q = loaded.get("/api/queues").json()
    assert q["total"] == 4
    assert q["counts"]["High Price"] == 1 and q["counts"]["Out of Stock"] == 1
    assert q["counts"]["dq-weight"] == 1 and q["counts"]["dq-category"] == 1
    assert q["counts"]["dq-link"] == 1 and q["counts"]["dq-listing"] == 1
    r = loaded.get("/api/products", params={"queue": "dq-weight"}).json()
    assert [p["sku"] for p in r["items"]] == ["SKU-3"]
    r = loaded.get("/api/products", params={"queue": "Out of Stock"}).json()
    assert [p["sku"] for p in r["items"]] == ["SKU-2"]
    assert loaded.get("/api/products", params={"q": "paint"}).json()["total"] == 1
    assert loaded.get("/api/products", params={"q": "acrylic 12"}).json()["total"] == 1      # all words must match
    r = loaded.get("/api/products", params={"sort": "-auto_price"}).json()
    assert r["items"][0]["sku"] == "SKU-1"
    assert r["items"][-1]["auto_price"] is None                                              # blanks sort last


def test_edit_recomputes_and_logs(loaded):
    r = loaded.post("/api/products/SKU-3", json={"patch": {"weight": 4}}).json()
    assert r["changed"] == 1
    d = loaded.get("/api/products/SKU-3").json()
    assert d["product"]["auto_price"] == floor_price(7, 4)
    assert d["activity"][0]["detail"].startswith("Weight")
    assert loaded.get("/api/queues").json()["counts"]["dq-weight"] == 0
    assert loaded.post("/api/products/SKU-3", json={"patch": {"listing_status": "Nope"}}).status_code == 400


def floor_price(src, w):
    import math
    return math.floor((src + 4.36 + 5) * 1.53) + 0.99


def test_bulk_edit(loaded):
    r = loaded.post("/api/products/bulk", json={"skus": ["SKU-1", "SKU-2"], "patch": {"listing_status": "Deactivated"}}).json()
    assert r["changed"] == 2
    assert loaded.get("/api/products", params={"listing": "Deactivated"}).json()["total"] == 2
    assert loaded.post("/api/products/bulk", json={"skus": [], "patch": {"remarks": "x"}}).status_code == 400
    # unknown fields are ignored, not applied
    assert loaded.post("/api/products/bulk", json={"skus": ["SKU-1"], "patch": {"sku": "HACK"}}).status_code == 400


def test_create_validates_and_catches_duplicates(loaded):
    good = {"sku": "NEW-1", "name": "Thing", "link": "https://www.amazon.com/dp/B0ABCDEFGH", "source_price": 4, "weight": 8, "category": "Craft"}
    assert loaded.post("/api/products/create", json={"product": {**good, "weight": ""}}).json()["errors"]["weight"]
    assert loaded.post("/api/products/create", json={"product": {**good, "sku": "SKU-1"}}).json()["dupes"]
    r = loaded.post("/api/products/create", json={"product": good}).json()
    assert r["ok"]
    assert loaded.get("/api/products/NEW-1").json()["product"]["auto_price"] == math.floor((4 + 4.68 + 5) * 1.53) + 0.99
    # imports keep products that were added in the app
    loaded.post("/api/import", content=b"SKU,SOURCE LINK,PRODUCT NAME\nSKU-1,https://www.amazon.com/dp/B000000001,A\n", headers={"X-Filename": "s.csv"})
    assert loaded.get("/api/products/NEW-1").json()["product"]["sku"] == "NEW-1"
    assert loaded.get("/api/queues").json()["total"] == 2   # SKU-1 + NEW-1


def test_batch_preview_flags_repeats(loaded):
    rows = [{"sku": "B-1", "name": "A", "link": "https://www.amazon.com/dp/B0AAAAAAAA", "source_price": 3, "weight": 8, "category": "X"},
            {"sku": "B-1", "name": "A", "link": "https://www.amazon.com/dp/B0BBBBBBBB", "source_price": 3, "weight": 8, "category": "X"},
            {"sku": "B-2", "name": "B", "link": "https://www.amazon.com/dp/B0AAAAAAAA", "source_price": 3, "weight": 8, "category": "X"}]
    r = loaded.post("/api/products/preview-batch", json={"rows": rows}).json()
    assert [x["ok"] for x in r["rows"]] == [True, False, False]
    done = loaded.post("/api/products/create-batch", json={"rows": rows}).json()
    assert done["created"] == 1 and done["skipped"] == 2


def test_pricing_save_recalculates_everything(loaded):
    before = loaded.get("/api/products/SKU-1").json()["product"]["auto_price"]
    p = loaded.get("/api/meta").json()["pricing"]
    prev = loaded.post("/api/pricing/preview", json={"pricing": {**p, "markup": 2.0}}).json()
    assert prev["up"] >= 1
    assert loaded.post("/api/pricing", json={"pricing": {**p, "markup": 9}}).status_code == 400
    assert loaded.post("/api/pricing", json={"pricing": {**p, "markup": 2.0}}).json()["ok"]
    assert loaded.get("/api/products/SKU-1").json()["product"]["auto_price"] > before


def test_michaels_flow(loaded):
    csv_bytes = b"Seller SKU,SKU Number,Price,Quantity\nSKU-1,555,30.00,4\nSKU-2,556,9.99,0\nGHOST,1,1,1\n"
    pv = loaded.post("/api/michaels/preview", content=csv_bytes, headers={"X-Filename": "dl.csv"}).json()
    assert pv["ok"] and pv["matched"] == 2 and pv["not_in_catalog"] == 1
    loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": True})
    c = loaded.get("/api/queues").json()["counts"]
    assert c["mz-price"] == 2                  # 30.00 != 32.99 and 9.99 != its automation price
    assert c["mz-zero-on"] == 1                # SKU-2: qty 0 while inventory on
    assert c["mz-missing"] == 1                # only SKU-3 is flagged listed and absent from the file (SKU-4 isn't listed)
    # a file that matches nothing is refused instead of wiping everything
    bad = loaded.post("/api/michaels/preview", content=b"SKU,Price,Qty\nNOPE,1,1\n", headers={"X-Filename": "x.csv"}).json()
    assert bad["matched"] == 0 and bad["will_clear"] >= 1
    r = loaded.post("/api/michaels/apply", json={"token": bad["token"], "clear_missing": True})
    assert r.status_code == 400 and loaded.get("/api/queues").json()["counts"]["mz-price"] == 2
    # the Michaels SKU number is searchable, editable and exported
    assert [p["sku"] for p in loaded.get("/api/products", params={"q": "555"}).json()["items"]] == ["SKU-1"]
    assert loaded.get("/api/products/SKU-1").json()["product"]["michaels_sku_number"] == "555"
    # ...but only the import can write it: nobody can type over it (the drawer, the API or the sheet)
    r = loaded.post("/api/products/SKU-1", json={"patch": {"michaels_sku_number": "777"}})
    assert r.status_code == 400 and "Michaels download" in r.json()["error"]
    assert loaded.get("/api/products/SKU-1").json()["product"]["michaels_sku_number"] == "555"
    assert b"555" in loaded.get("/api/products/export.csv").content
    fixes = loaded.get("/api/export/michaels-fixes.csv")
    assert b"SKU-1" in fixes.content


def agent(c, path, body, key):
    return c.post("/api/agent/" + path, json=body, headers={"X-Agent-Key": key})


def test_scraper_protocol_and_price_alerts(loaded):
    key = loaded.get("/api/scraper/key").json()["key"]
    anon = TestClient(app)
    assert agent(anon, "start", {"agent_id": "a1"}, "wrong").status_code == 403
    s = agent(anon, "start", {"agent_id": "a1", "name": "PC-1", "workers": 2}, key).json()
    assert s["action"] == "created" and s["total"] == 3                       # SKU-4 has no Amazon link
    items = agent(anon, "claim", {"agent_id": "a1", "run_id": s["run_id"], "n": 10}, key).json()["items"]
    assert len(items) == 3
    # a second PC gets nothing while the first holds everything
    assert agent(anon, "claim", {"agent_id": "a2", "run_id": s["run_id"], "n": 10}, key).json()["items"] == []
    res = dict(agent_id="a1", run_id=s["run_id"], sku="SKU-1", result="ok", price="$20.00", availability="In Stock",
               delivery="", run_at="2026-02-01 10:00:00", title="Acrylic Paint Set 12 Pack")
    assert agent(anon, "result", res, key).json()["ok"]
    p = loaded.get("/api/products/SKU-1").json()["product"]
    assert p["price"] == "$20.00" and p["prev_price_num"] == 12.5 and p["price_alert_ack"] == 0
    assert p["title_score"] == 100
    assert "price-moved" not in loaded.get("/api/queues").json()["counts"]      # the Price moved issue no longer exists
    assert p["status"] == "High Price"                                       # 20.00 is above the 10.00 source price: this is what the ▲ shows
    # failures retry, then give up
    for _ in range(3):
        agent(anon, "result", dict(agent_id="a1", run_id=s["run_id"], sku="SKU-2", result="timeout"), key)
    assert loaded.get("/api/scraper").json()["run"]["failed"] == 1
    # the run finishes once nothing is left to do
    for sku in ("SKU-3",):
        agent(anon, "result", {**res, "sku": sku}, key)
    d = loaded.get("/api/scraper").json()
    assert d["run"]["state"] == "done" and d["agents"][0]["name"] == "PC-1"


def test_team_management(admin):
    assert admin.post("/api/team/add", json={"username": "sam", "name": "Sam", "password": "short"}).status_code == 400
    assert admin.post("/api/team/add", json={"username": "sam", "name": "Sam", "password": "longenough1"}).json()["ok"]
    assert len(admin.get("/api/team").json()["items"]) == 2
    assert admin.post("/api/team/remove", json={"username": "boss"}).status_code == 400
    sam = TestClient(app)
    assert sam.post("/api/login", json={"username": "sam", "password": "longenough1"}).status_code == 200
    admin.post("/api/team/remove", json={"username": "sam"})
    assert sam.get("/api/me").status_code == 401           # removing someone ends their sessions


def test_saved_views(loaded):
    assert loaded.post("/api/views", json={"name": "OOS", "params": {"queue": "Out of Stock", "junk": "x"}}).json()["ok"]
    v = loaded.get("/api/views").json()["items"][0]
    assert v["params"] == {"queue": "Out of Stock"} and v["mine"]
    loaded.post(f"/api/views/{v['id']}/delete")
    assert loaded.get("/api/views").json()["items"] == []


def test_exports(loaded):
    r = loaded.get("/api/products/export.csv", params={"queue": "Out of Stock"})
    assert r.status_code == 200 and b"SKU-2" in r.content and b"SKU-1" not in r.content
    assert b"SOURCE STATUS" in loaded.get("/api/export/optimizer.csv").content
    assert loaded.get("/api/backup").content[:2] == b"\x1f\x8b"          # gzip


def test_scraper_still_writes_columns_protected_from_people(loaded):
    # protect the columns the scraper fills with "Nobody (imports only)": people can't type there, the scraper still can
    for col in ("title", "price", "availability", "variation", "amazon_variation"):
        assert loaded.post("/api/locks", json={"col": col, "mode": "nobody"}).status_code == 200
    typed = loaded.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-1", "field": "title", "value": "typed by a person"}]}).json()
    assert typed["changed"] == 0 and typed["error_count"] == 1
    key = loaded.get("/api/scraper/key").json()["key"]
    anon = TestClient(app)
    s = agent(anon, "start", {"agent_id": "a1", "name": "PC-1", "workers": 1}, key).json()
    agent(anon, "claim", {"agent_id": "a1", "run_id": s["run_id"], "n": 10}, key)
    res = dict(agent_id="a1", run_id=s["run_id"], sku="SKU-1", result="ok", price="$22.00", availability="In Stock", delivery="",
               run_at="2026-02-01 10:00:00", title="Acrylic Paint Set 12 Pack by Amazon", variation="Color: Red")
    assert agent(anon, "result", res, key).json()["ok"]
    p = loaded.get("/api/products/SKU-1").json()["product"]
    assert p["title"] == "Acrylic Paint Set 12 Pack by Amazon" and p["price"] == "$22.00" and p["amazon_variation"] == "Color: Red"
    assert p["checked_by"] == "PC-1"


def test_each_store_has_its_own_name_logo_and_colour(loaded, monkeypatch):
    from app import brand
    page = lambda: loaded.get("/").text
    login = lambda: TestClient(app).get("/login").text
    # a fresh store: the defaults (or what IT put in the environment when starting that copy)
    assert "<title>Greentek Catalog</title>" in page() and ">GT</div>" in page()
    monkeypatch.setenv("GT_APP_NAME", "Citrus Catalog"); monkeypatch.setenv("GT_APP_MARK", "CT")
    assert "<title>Citrus Catalog</title>" in page() and "Sign in · Citrus Catalog" in login() and ">CT</div>" in page()
    # what an admin saves wins over the environment, is escaped, and shows on the sign-in page too (it is public)
    r = loaded.post("/api/brand", json={"name": "Orange <b>Shop</b>", "mark": "os", "color": "#d9480f"}).json()["brand"]
    assert r["name"] == "Orange <b>Shop</b>" and r["color"] == "#d9480f"
    p = page()
    assert "<title>Orange &lt;b&gt;Shop&lt;/b&gt;</title>" in p and "<b>Shop</b>" not in p and "--brand:#d9480f" in p and ">os</div>" in p
    assert "Orange &lt;b&gt;Shop&lt;/b&gt;" in login() and "--brand:#d9480f" in login()
    assert loaded.get("/api/meta").json()["brand"]["name"] == "Orange <b>Shop</b>"
    assert loaded.post("/api/brand", json={"name": "x", "color": "red"}).status_code == 400
    # logo: only real images, small, admins only; served publicly with safe headers; removable
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    assert loaded.post("/api/brand/logo", content=b"<svg onload=alert(1)>").status_code == 400
    assert loaded.post("/api/brand/logo", content=png + b"\x00" * (401 * 1024)).status_code == 400
    assert loaded.post("/api/brand/logo", content=png).status_code == 200
    logo = loaded.get("/api/meta").json()["brand"]["logo"]
    assert logo.startswith("/brand/logo?v=") and f'src="{logo}"' in page() and f'href="{logo}"' in page()
    got = TestClient(app).get(logo)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == png and got.headers["x-content-type-options"] == "nosniff"
    amy = TestClient(app); auth.add_user("amy2", "Amy", "password123", "member")
    amy.post("/api/login", json={"username": "amy2", "password": "password123"})
    assert amy.post("/api/brand", json={"name": "hack"}).status_code == 403 and amy.post("/api/brand/logo", content=png).status_code == 403
    assert loaded.post("/api/brand/logo/remove").json()["brand"]["logo"] == "" and TestClient(app).get("/brand/logo").status_code == 404
    assert any(a["action"] == "changed the app name and logo" for a in loaded.get("/api/activity").json()["items"])


def test_scraper_key_prefix_follows_the_stores_short_mark(loaded):
    old = loaded.get("/api/scraper/key").json()["key"]
    assert old.startswith("gt_")                                   # the default mark is GT
    loaded.post("/api/brand", json={"name": "Citrus Catalog", "mark": "CTR", "color": ""})
    assert loaded.get("/api/scraper/key").json()["key"] == old     # renaming the store never changes a key already handed out
    key = loaded.post("/api/scraper/key/rotate").json()["key"]
    assert key.startswith("ctr_") and key != old and len(key) > 20
    anon = TestClient(app)
    assert agent(anon, "start", {"agent_id": "a1", "name": "PC-1", "workers": 1}, old).status_code == 403       # the old key stops working
    assert agent(anon, "start", {"agent_id": "a1", "name": "PC-1", "workers": 1}, key).status_code == 200
    loaded.post("/api/brand", json={"name": "Odd", "mark": "!!", "color": ""})                                   # nothing usable in the mark: a plain prefix
    assert loaded.post("/api/scraper/key/rotate").json()["key"].startswith("key_")


def test_a_bad_cookie_name_cannot_break_signing_in(monkeypatch, capsys):
    import importlib
    from app import auth as auth_mod
    monkeypatch.setenv("GT_COOKIE", "gt_session_${APP_NAME}")           # what an old docker-compose v1 passes when it can't expand a nested default
    fresh = importlib.reload(auth_mod)
    try:
        assert fresh.COOKIE == "gt_session___APP_NAME_" and "not a valid cookie name" in capsys.readouterr().out
        for bad in ("my cookie", "gt;session", "", "  "):
            monkeypatch.setenv("GT_COOKIE", bad)
            name = importlib.reload(auth_mod).COOKIE
            assert name and all(ch.isalnum() or ch in "_.-" for ch in name)
    finally:
        monkeypatch.delenv("GT_COOKIE", raising=False)
        importlib.reload(auth_mod)


def test_unexpected_server_errors_come_back_as_messages_not_blank_pages(loaded, monkeypatch):
    from app import auth as auth_mod
    monkeypatch.setattr(auth_mod, "check_login", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    quiet = TestClient(app, raise_server_exceptions=False)
    r = quiet.post("/api/login", json={"username": "x", "password": "y"})
    assert r.status_code == 500 and "unexpected problem (RuntimeError)" in r.json()["error"] and "boom" not in r.json()["error"]


def test_the_app_says_clearly_when_its_data_folder_cannot_be_written(tmp_path, monkeypatch):
    from app import db as db_mod
    assert db_mod.check_data_writable() == ""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")                                           # a file where the folder should be
    monkeypatch.setattr(db_mod, "DATA_DIR", blocker / "data")
    msg = db_mod.check_data_writable()
    assert "cannot write to its data folder" in msg and "chown -R 10001:10001" in msg


def test_the_import_runs_in_the_background_and_its_result_is_kept(admin, monkeypatch):
    import time
    from app import imports
    from tests.conftest import SAMPLE_CSV
    start = lambda body=SAMPLE_CSV.encode(), name="s.csv": admin.post("/api/import/start", content=body, headers={"X-Filename": name})
    r = start()
    assert r.status_code == 200 and r.json()["state"] in ("running", "done", "error")
    for _ in range(100):
        j = admin.get("/api/import/status").json()
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done" and j["summary"]["products"] == 4 and j["filename"] == "s.csv" and j["finished_at"]
    assert admin.get("/api/products", params={"q": "SKU-1"}).json()["items"][0]["sku"] == "SKU-1"
    # the result survives (it is kept on the server): a new "page load" still sees it
    imports._LIST.state.clear()
    assert admin.get("/api/import/status").json()["state"] == "done"
    # a file that can't be read is reported as an error, not left hanging
    start(b"a,b\n1,2\n", "bad.csv")
    for _ in range(100):
        j = admin.get("/api/import/status").json()
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "error" and "SKU and SOURCE LINK" in j["error"]
    # only one at a time, and only admins
    gate = __import__("threading").Event()
    real = imports.import_products
    monkeypatch.setattr(imports, "import_products", lambda *a, **k: (gate.wait(5), real(*a, **k))[1])
    assert start().status_code == 200
    again = start()
    assert again.status_code == 400 and "already running" in again.json()["error"]
    gate.set()
    for _ in range(100):
        if admin.get("/api/import/status").json()["state"] != "running":
            break
        time.sleep(0.1)
    # a restart while an import was running marks it as cut off
    imports._LIST._save({"state": "running", "filename": "x.csv", "started_at": "2026-01-01 00:00:00"})
    imports._LIST.state.clear()
    imports.job_reset()
    assert admin.get("/api/import/status").json()["state"] == "error" and "restarted" in admin.get("/api/import/status").json()["error"]
    member = TestClient(app)
    auth.add_user("mem2", "Mem", "password123", "member")
    member.post("/api/login", json={"username": "mem2", "password": "password123"})
    assert member.post("/api/import/start", content=b"x").status_code == 403
