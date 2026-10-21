import math


def test_import_reads_sku_number_from_the_sheet(loaded):
    assert loaded.get("/api/products/SKU-1").json()["product"]["michaels_sku_number"] == "430503183213510657"
    assert [p["sku"] for p in loaded.get("/api/products", params={"q": "4305031832"}).json()["items"]] == ["SKU-1"]


def test_view_is_a_frozen_sku_list_with_rows_on_demand(loaded):
    v = loaded.get("/api/sheet/skus", params={"queue": "Out of Stock"}).json()
    assert v["skus"] == ["SKU-2"]
    r = loaded.post("/api/sheet/rows", json={"skus": ["SKU-1", "SKU-3", "NOPE"]}).json()["rows"]
    assert [x["sku"] for x in r] == ["SKU-1", "SKU-3"]
    one = r[0]
    # the sheet's calculated columns
    assert one["cost"] == 21.33 and one["selling"] == round(21.33 * 1.53, 2) and one["auto_price"] == 32.99
    assert abs(one["markup_pct"] - 0.53) < 1e-6 and one["sold_total"] == 0
    assert one["price_num"] == 12.5                                          # kept internally for statuses and "Use Amazon price"
    for gone in ("scrap_price", "new_fee", "new_selling", "new_auto", "lead_days", "delivery_date"):
        assert gone not in one                                               # these columns were removed from the sheet


def test_cell_edits_keep_the_row_in_the_view_and_give_undo_data(loaded):
    flt = {"cf_status": "High Price"}
    assert loaded.get("/api/sheet/skus", params=flt).json()["skus"] == ["SKU-1"]
    # the "high price" job: put the Amazon price (12.50) in as the new source price
    res = loaded.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-1", "field": "source_price", "value": "$12.50"}], "filter": flt}).json()
    assert res["changed"] == 1 and res["left_view"] == ["SKU-1"]       # no longer High Price -> flagged, not removed
    assert res["before"] == [{"sku": "SKU-1", "field": "source_price", "value": 10.0}]
    assert res["rows"][0]["status"] == "Normal" and res["rows"][0]["auto_price"] == math.floor((12.5 + 11.33) * 1.53) + 0.99
    # undo with the returned values
    loaded.post("/api/sheet/cells", json={"edits": res["before"]})
    assert loaded.get("/api/products/SKU-1").json()["product"]["status"] == "High Price"


def test_use_amazon_price_for_a_whole_view(loaded):
    skus = loaded.get("/api/sheet/skus", params={"queue": "High Price"}).json()["skus"]
    r = loaded.post("/api/sheet/use-amazon-price", json={"skus": skus + ["SKU-4"], "filter": {"queue": "High Price"}}).json()
    assert r["changed"] == 1 and r["skipped_no_price"] == 1 and r["left_view"] == ["SKU-1"]
    assert loaded.get("/api/products/SKU-1").json()["product"]["source_price"] == 12.5
    assert "used the Amazon price" in loaded.get("/api/products/SKU-1").json()["activity"][0]["action"]


def test_paste_style_edits_report_bad_cells_and_apply_the_rest(loaded):
    r = loaded.post("/api/sheet/cells", json={"edits": [
        {"sku": "SKU-1", "field": "listing_status", "value": "Deactivated"},
        {"sku": "SKU-2", "field": "listing_status", "value": "Banana"},
        {"sku": "SKU-2", "field": "name", "value": "Should not be saved"},    # same row as a bad cell: skipped as a whole
        {"sku": "SKU-3", "field": "weight", "value": "4"},
        {"sku": "SKU-3", "field": "inventory", "value": "TRUE"},
    ]}).json()
    assert r["changed"] == 2 and r["error_count"] == 1
    assert loaded.get("/api/products/SKU-2").json()["product"]["name"] == "Wooden Frame 8 inch"
    assert loaded.get("/api/products/SKU-3").json()["product"]["inventory"] == 1
    assert loaded.get("/api/products/SKU-2").json()["product"]["listing_status"] == "Live"


def cells(c, *edits, **kw):
    return c.post("/api/sheet/cells", json={"edits": [{"sku": s, "field": f, "value": v} for s, f, v in edits], **kw}).json()


def row(c, sku):
    return c.post("/api/sheet/rows", json={"skus": [sku]}).json()["rows"][0]


def test_every_stored_column_is_editable(loaded):
    r = cells(loaded, ("SKU-1", "availability", "Only 2 left in stock"), ("SKU-1", "asin", "B0NEWASIN1"),
              ("SKU-1", "sold_2026", "5"))
    assert r["changed"] == 1 and r["error_count"] == 0
    x = row(loaded, "SKU-1")
    assert x["availability"] == "Only 2 left in stock" and x["asin"] == "B0NEWASIN1"
    assert x["status"] == "Low Inventory" and x["sold_total"] == 5            # recalculated from what was typed
    cells(loaded, ("SKU-1", "price", "$9.00"))
    x = row(loaded, "SKU-1")
    assert x["price_num"] == 9.0 and x["status"] == "Low Inventory"


def test_typing_the_amazon_price_updates_the_status(loaded):
    r = cells(loaded, ("SKU-1", "price", "$8"))
    x = row(loaded, "SKU-1")
    assert x["price"] == "$8" and x["price_num"] == 8.0 and x["status"] == "Low Price"          # source 10 > amazon 8
    assert r["before"] == [{"sku": "SKU-1", "field": "price", "value": "$12.50"}]
    cells(loaded, *[(b["sku"], b["field"], b["value"]) for b in r["before"]])                    # undo
    assert row(loaded, "SKU-1")["status"] == "High Price"
    assert cells(loaded, ("SKU-1", "scrap_price", "8"))["error_count"] == 1                      # that column no longer exists


def test_calculated_columns_take_overrides_and_revert(loaded):
    base = row(loaded, "SKU-1")
    r = cells(loaded, ("SKU-1", "fee", "10"))                                  # type over the 3PL fee formula
    x = row(loaded, "SKU-1")
    assert x["fee"] == 10 and x["_ovr"] == ["fee"]
    assert x["auto_price"] == math.floor((10 + 10) * 1.53) + 0.99              # the price follows the typed fee
    assert r["before"] == [{"sku": "SKU-1", "field": "fee", "value": None}]
    # pricing rules changing must not wipe the typed value
    p = loaded.get("/api/meta").json()["pricing"]
    loaded.post("/api/pricing", json={"pricing": {**p, "markup": 2.0}})
    assert row(loaded, "SKU-1")["fee"] == 10
    # blank = back to the formula
    cells(loaded, ("SKU-1", "fee", ""))
    y = row(loaded, "SKU-1")
    assert y["fee"] == 11.33 and y["_ovr"] == []
    loaded.post("/api/pricing", json={"pricing": {**p, "markup": 1.53}})
    assert row(loaded, "SKU-1")["auto_price"] == base["auto_price"]
    # display-only columns, a typed automation price, and validation
    cells(loaded, ("SKU-1", "auto_price", "$49.99"), ("SKU-1", "markup_pct", "60%"), ("SKU-1", "selling", "55.99"))
    z = row(loaded, "SKU-1")
    assert z["auto_price"] == 49.99 and z["markup_pct"] == 0.6 and z["selling"] == 55.99
    assert cells(loaded, ("SKU-1", "fee", "abc"))["error_count"] == 1
    assert cells(loaded, ("SKU-1", "status", "Banana"))["error_count"] == 1


def test_status_override_lasts_until_the_next_scrape(loaded):
    cells(loaded, ("SKU-1", "status", "normal"))
    assert row(loaded, "SKU-1")["status"] == "Normal" and "status" in row(loaded, "SKU-1")["_ovr"]
    cells(loaded, ("SKU-1", "remarks", "edit elsewhere"))                        # other edits keep it
    assert row(loaded, "SKU-1")["status"] == "Normal"
    key = loaded.get("/api/scraper/key").json()["key"]
    h = {"X-Agent-Key": key}
    run = loaded.post("/api/agent/start", json={"agent_id": "a", "name": "PC"}, headers=h).json()["run_id"]
    loaded.post("/api/agent/claim", json={"agent_id": "a", "run_id": run, "n": 10}, headers=h)
    loaded.post("/api/agent/result", json=dict(agent_id="a", run_id=run, sku="SKU-1", result="ok", price="$20.00",
                                              availability="In Stock", delivery="", run_at="2026-02-01 10:00:00"), headers=h)
    x = row(loaded, "SKU-1")
    assert x["status"] == "High Price" and x["_ovr"] == []                        # the fresh check wins


def test_import_keeps_typed_fee(loaded):
    cells(loaded, ("SKU-1", "fee", "10"))
    from tests.conftest import SAMPLE_CSV
    loaded.post("/api/import", content=SAMPLE_CSV.encode(), headers={"X-Filename": "again.csv"})
    assert row(loaded, "SKU-1")["fee"] == 10


def test_custom_columns(loaded):
    member_login(loaded)
    r = loaded.post("/api/sheet/columns/add", json={"label": "Buyer notes", "type": "text"}).json()
    assert r["key"] == "x_buyer_notes"
    n = loaded.post("/api/sheet/columns/add", json={"label": "Reorder qty", "type": "number", "after": "x_buyer_notes"}).json()["key"]
    d = loaded.post("/api/sheet/columns/add", json={"label": "Checked on", "type": "date"}).json()["key"]
    ck = loaded.post("/api/sheet/columns/add", json={"label": "Reviewed", "type": "checkbox"}).json()["key"]
    assert loaded.post("/api/sheet/columns/add", json={"label": "buyer NOTES", "type": "text"}).status_code == 400
    assert [c["key"] for c in loaded.get("/api/meta").json()["custom_columns"]][:2] == ["x_buyer_notes", n]
    assert cells(loaded, ("SKU-1", "x_buyer_notes", "call vendor"), ("SKU-2", "x_buyer_notes", "ok"), ("SKU-1", n, "$12"),
                 ("SKU-1", d, "3/31/2026"), ("SKU-1", ck, "TRUE"))["changed"] == 2
    x = row(loaded, "SKU-1")
    assert (x["x_buyer_notes"], x[n], x[d], x[ck]) == ("call vendor", 12, "2026-03-31", 1)
    assert cells(loaded, ("SKU-1", d, "yesterday-ish"))["error_count"] == 1
    # filter and sort on a custom column
    f = lambda **kw: loaded.get("/api/sheet/skus", params=kw).json()["skus"]
    assert f(cf_x_buyer_notes="vendor") == ["SKU-1"] and f(cf_x_buyer_notes="(not blank)") == ["SKU-1", "SKU-2"]
    assert f(**{"cf_" + n: ">5"}) == ["SKU-1"] and f(**{"cf_" + ck: "TRUE"}) == ["SKU-1"]
    assert f(sort="-x_buyer_notes")[:2] == ["SKU-2", "SKU-1"] and f(sort="x_buyer_notes")[:2] == ["SKU-1", "SKU-2"]
    # undo restores, clearing removes
    r = cells(loaded, ("SKU-1", "x_buyer_notes", "changed"))
    assert r["before"] == [{"sku": "SKU-1", "field": "x_buyer_notes", "value": "call vendor"}]
    cells(loaded, ("SKU-1", "x_buyer_notes", ""))
    assert "x_buyer_notes" not in row(loaded, "SKU-1")
    # deleting a column removes its values everywhere and is admin only
    assert loaded.post("/api/sheet/columns/delete", json={"key": "x_nope"}).status_code == 400
    assert loaded.post("/api/sheet/columns/delete", json={"key": "name"}).status_code == 400      # built-in
    out = loaded.post("/api/sheet/columns/delete", json={"key": "x_buyer_notes"}).json()
    assert [c["key"] for c in out["columns"]][0] == n and out["columns"][0]["after"] in (None, "")
    assert "x_buyer_notes" not in row(loaded, "SKU-2")
    assert loaded.get("/api/sheet/skus", params={"cf_x_buyer_notes": "ok"}).json()["total"] == 4   # filter on a deleted column is ignored


def member_login(admin_client):
    """Prove members can't add/delete columns (but can edit values)."""
    from fastapi.testclient import TestClient
    from app import auth
    from app.main import app
    auth.add_user("mia", "Mia", "password123", "member")
    m = TestClient(app)
    m.post("/api/login", json={"username": "mia", "password": "password123"})
    assert m.post("/api/sheet/columns/add", json={"label": "X", "type": "text"}).status_code == 403
    assert m.post("/api/sheet/columns/delete", json={"key": "x_x"}).status_code == 403
    assert m.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-1", "field": "remarks", "value": "member edit"}]}).json()["changed"] == 1


def test_sku_rename_follows_history_and_undoes(loaded):
    key = loaded.get("/api/scraper/key").json()["key"]
    h = {"X-Agent-Key": key}
    run = loaded.post("/api/agent/start", json={"agent_id": "a", "name": "PC"}, headers=h).json()["run_id"]
    loaded.post("/api/agent/claim", json={"agent_id": "a", "run_id": run, "n": 10}, headers=h)
    loaded.post("/api/agent/result", json=dict(agent_id="a", run_id=run, sku="SKU-1", result="ok", price="$20.00",
                                              availability="In Stock", delivery="", run_at="2026-02-01 10:00:00"), headers=h)
    r = cells(loaded, ("SKU-1", "sku", "GTK-0001"), ("SKU-1", "remarks", "renamed"))
    assert r["renamed"] == {"SKU-1": "GTK-0001"} and r["updated_skus"] == ["GTK-0001"]
    d = loaded.get("/api/products/GTK-0001").json()
    assert d["product"]["remarks"] == "renamed" and len(d["history"]) == 1 and d["activity"]
    assert loaded.get("/api/products/SKU-1").status_code == 400
    assert cells(loaded, ("GTK-0001", "sku", "sku-2"))["error_count"] == 1               # already used (any case)
    assert cells(loaded, ("GTK-0001", "sku", "bad sku!"))["error_count"] == 1
    undo = cells(loaded, *[(b["sku"], b["field"], b["value"]) for b in r["before"]])
    assert undo["renamed"] == {"GTK-0001": "SKU-1"}
    x = loaded.get("/api/products/SKU-1").json()["product"]
    assert x["remarks"] == "" and len(loaded.get("/api/products/SKU-1").json()["history"]) == 1


def test_michaels_import_brings_the_product_images(loaded):
    # one column per image
    f1 = (b"Seller SKU,SKU Number,Price,Quantity,Image 1,Image 2,Image Count\n"
          b"SKU-1,555,30.00,4,https://cdn.example.com/a.jpg,//cdn.example.com/b.jpg,2\n"
          b"SKU-2,556,9.99,1,https://cdn.example.com/c.jpg,,1\n"
          b"SKU-3,557,5,2,,,0\n")
    pv = loaded.post("/api/michaels/preview", content=f1, headers={"X-Filename": "dl.csv"}).json()
    assert pv["ok"] and pv["has_images"] and pv["image_columns"] == ["Image 1", "Image 2"]
    assert pv["with_images"] == 2 and pv["image_urls"] == 3
    loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": True})
    assert row(loaded, "SKU-1")["images"] == ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    assert row(loaded, "SKU-2")["images"] == ["https://cdn.example.com/c.jpg"] and row(loaded, "SKU-3")["images"] == []
    # one column holding several links, a newer download replaces the old ones, products missing from it are cleared
    f2 = b"SKU,SKU Number,Price,Qty,Image URLs\nSKU-1,555,30,4,\"https://x.test/1.png | https://x.test/2.png; https://x.test/3.png\"\n"
    pv = loaded.post("/api/michaels/preview", content=f2, headers={"X-Filename": "dl2.csv"}).json()
    assert pv["has_images"] and pv["with_images"] == 1
    loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": True})
    assert row(loaded, "SKU-1")["images"] == ["https://x.test/1.png", "https://x.test/2.png", "https://x.test/3.png"]
    assert row(loaded, "SKU-2")["images"] == []
    # a download without image columns leaves the images alone; columns can be picked by hand
    loaded.post("/api/michaels/apply", json={"token": loaded.post("/api/michaels/preview", content=f1, headers={"X-Filename": "a.csv"}).json()["token"], "clear_missing": False})
    f3 = b"SKU,Price,Qty,Pics\nSKU-1,30,4,https://y.test/z.jpg\n"
    p3 = loaded.post("/api/michaels/preview", content=f3, headers={"X-Filename": "dl3.csv"}).json()
    assert p3["has_images"] is False                                                            # "Pics" isn't a name we know
    p3 = loaded.post("/api/michaels/preview", content=f3, headers={"X-Filename": "dl3.csv", "X-Mapping": '{"sku":0,"price":1,"qty":2,"images":[3]}'}).json()
    assert p3["has_images"] and p3["with_images"] == 1


def test_rounded_sku_numbers_never_overwrite_good_ones(loaded):
    # SKU-1 already has a good number from the Listed Sheet; SKU-2 has none. The file has Excel-rounded numbers for both.
    f = b"seller_sku,sku_number,price,quantity\nSKU-1,4.30503E+17,30,4\nSKU-2,4.36087E+17,9.99,1\nSKU-3,557,5,2\n"
    pv = loaded.post("/api/michaels/preview", content=f, headers={"X-Filename": "dl.csv"}).json()
    assert pv["rounded_sku_numbers"] == 2 and pv["rounded_kept"] == 1 and pv["rounded_missing"] == 1
    assert pv["sku_numbers_new"] == 1                                                  # only SKU-3's number is usable
    loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": False})
    assert row(loaded, "SKU-1")["michaels_sku_number"] == "430503183213510657"           # untouched
    assert not row(loaded, "SKU-2")["michaels_sku_number"]                               # stays blank, not "4.36087E+17"
    assert row(loaded, "SKU-3")["michaels_sku_number"] == "557"
    assert row(loaded, "SKU-1")["michaels_price"] == 30                                  # price/qty still update


def test_column_filters(loaded):
    f = lambda **kw: loaded.get("/api/sheet/skus", params=kw).json()["skus"]
    assert f(cf_name="frame") == ["SKU-2"]
    assert f(cf_source_price=">6") == ["SKU-1", "SKU-3"]
    assert f(cf_source_price="3-6") == ["SKU-2", "SKU-4"]
    assert f(cf_weight="(blank)") == ["SKU-3"]
    assert f(cf_listed="FALSE") == ["SKU-4"]
    assert f(cf_category="(blank)") == ["SKU-3"]
    assert f(cf_status="Out", cf_listing_status="Live") == ["SKU-2"]
    assert f(cf_source_price="banana") == []
    assert f(sort="-source_price")[0] == "SKU-1" and f(sort="-selling")[-1] in ("SKU-3", "SKU-4")

def test_scraped_by_column_records_the_scraper_pc_and_time(loaded):
    key = loaded.get("/api/scraper/key").json()["key"]
    h = {"X-Agent-Key": key}
    run = loaded.post("/api/agent/start", json={"agent_id": "a1", "name": "OFFICE-PC-7"}, headers=h).json()["run_id"]
    loaded.post("/api/agent/claim", json={"agent_id": "a1", "run_id": run, "n": 10}, headers=h)
    loaded.post("/api/agent/result", json=dict(agent_id="a1", run_id=run, sku="SKU-1", result="ok", price="$20.00", availability="In Stock",
                                              delivery="", run_at="2026-03-04 09:15:30", title="Acrylic Paint Set 12 Pack"), headers=h)
    x = row(loaded, "SKU-1")
    assert x["run_at"] == "2026-03-04 09:15:30" and x["checked_by"] == "OFFICE-PC-7"          # time and PC are stored together
    assert row(loaded, "SKU-2")["checked_by"] in (None, "")                                    # never scraped: blank
    f = lambda **kw: loaded.get("/api/sheet/skus", params=kw).json()["skus"]
    assert f(cf_checked_by="office-pc") == ["SKU-1"] and f(cf_checked_by="(blank)") == ["SKU-2", "SKU-3", "SKU-4"]
    assert f(sort="-checked_by")[0] == "SKU-1"
    assert cells(loaded, ("SKU-1", "checked_by", "OFFICE-PC-2"))["changed"] == 1               # editable like every column
    assert row(loaded, "SKU-1")["checked_by"] == "OFFICE-PC-2"
    assert b"OFFICE-PC-2" in loaded.get("/api/export/optimizer.csv").content                   # and in the results export


def test_productivity_counts_resolved_issues_per_person_and_day(loaded):
    # SKU-1 is High Price: fixing the source price resolves it; touching a product with no issue does not count
    cells(loaded, ("SKU-1", "source_price", "$12.50"))
    cells(loaded, ("SKU-1", "remarks", "again"))                    # same product again: still one
    cells(loaded, ("SKU-3", "remarks", "just a note"))
    r = loaded.get("/api/productivity").json()
    assert r["total_resolved"] == 1
    me = r["people"][0]
    assert me["resolved"] == 1 and me["touched"] == 2 and me["kinds"] == {"High Price": 1}
    assert sum(r["series"][0]["counts"]) == 1 and len(r["dates"]) == 14
    assert loaded.get("/api/productivity", params={"day": "2020-01-01"}).json()["people"] == []


def test_value_picker_filters(loaded):
    import json
    cells(loaded, ("SKU-1", "manual_status", "In Stock"), ("SKU-2", "manual_status", "OOS"), ("SKU-3", "manual_status", "in stock"))
    v = loaded.get("/api/sheet/values", params={"col": "manual_status"}).json()["values"]
    by = {x["v"].lower(): x["n"] for x in v}
    assert by["in stock"] == 2 and by["oos"] == 1 and v[-1]["v"] == "(blank)"             # same word in any case is one value; blanks last
    pick = lambda *vals: {"cf_manual_status": "in:" + json.dumps(list(vals))}
    skus = lambda p: set(loaded.get("/api/sheet/skus", params=p).json()["skus"])
    assert skus(pick("In Stock")) == {"SKU-1", "SKU-3"}
    assert skus(pick("In Stock", "OOS")) == {"SKU-1", "SKU-2", "SKU-3"}
    assert "SKU-4" in skus(pick("(blank)")) and "SKU-1" not in skus(pick("(blank)"))
    assert skus(pick()) == set()                                                          # nothing ticked: nothing shown
    # the list under a column ignores that column's own filter but respects the others
    both = loaded.get("/api/sheet/values", params={"col": "manual_status", **pick("OOS"), "cf_category": "zzz-no-match"}).json()["values"]
    assert both == []
    same = loaded.get("/api/sheet/values", params={"col": "manual_status", **pick("OOS")}).json()["values"]
    assert {x["v"].lower() for x in same} >= {"in stock", "oos"}
    # TRUE/FALSE and status pickers
    assert skus({"cf_inventory": 'in:["FALSE"]'}) == skus({"cf_inventory": "FALSE"})
    assert skus({"cf_status": 'in:["High Price","Out of Stock"]'}) == skus({"queue": "High Price"}) | skus({"queue": "Out of Stock"})
    assert loaded.get("/api/sheet/values", params={"col": "weight"}).status_code == 400   # numbers use ranges


def test_sold_counts_work_like_countif_over_a_range(loaded, monkeypatch):
    from app import sold
    tracker25 = [["header", "x"], ["note"], ["date", "sku", "qty"], ["1/2", "SKU-1", "1"], ["1/3", "sku-1", "2"], ["1/4", "SKU-2", ""], ["", " ", ""]]
    tracker26 = [["a", "b"], ["x", "SKU-1"], ["SKU-1", "SKU-1"], ["y", "SKU-3"]]
    pages = {"https://docs.google.com/spreadsheets/d/AAA/edit?gid=1": tracker25, "https://docs.google.com/spreadsheets/d/BBB/edit?gid=2": tracker26}
    monkeypatch.setattr(sold, "fetch_rows", lambda url: pages[url])
    assert sold.count_values(tracker25, "A4:C")[0]["sku-1"] == 2             # starts at row 4; case ignored; any column in range
    assert sold.count_values(tracker25, "A4:A")[0]["sku-1"] == 0             # a narrower range leaves the column out
    cfg = loaded.post("/api/sold/config", json={"2025": {"url": "https://docs.google.com/spreadsheets/d/AAA/edit?gid=1", "range": "A4:C"},
                                                "2026": {"url": "https://docs.google.com/spreadsheets/d/BBB/edit?gid=2", "range": "A2:B"}}).json()
    assert cfg["config"]["2025"]["range"] == "A4:C"
    st = loaded.post("/api/sold/sync", json={}).json()["status"]
    assert st["ok"] and st["years"]["2025"]["sales"] == 3 and st["years"]["2026"]["sales"] == 4     # 2026: SKU-1 three times + SKU-3 once
    p = lambda s: loaded.get(f"/api/products/{s}").json()["product"]
    assert (p("SKU-1")["sold_2025"], p("SKU-1")["sold_2026"]) == (2, 3)
    assert (p("SKU-2")["sold_2025"], p("SKU-2")["sold_2026"]) == (1, 0)
    row = loaded.post("/api/sheet/rows", json={"skus": ["SKU-1"]}).json()["rows"][0]
    assert row["sold_total"] == 5                                              # TOTAL PRODUCT SOLD = 2025 + 2026
    # a sheet with none of the catalog's SKUs, or nothing in range, changes nothing
    pages["https://docs.google.com/spreadsheets/d/AAA/edit?gid=1"] = [["", ""], ["", ""], ["", ""], ["zzz", "yyy"]]
    st = loaded.post("/api/sold/sync", json={"years": ["2025"]}).json()["status"]
    assert "None of your SKUs" in st["years"]["2025"]["error"] and p("SKU-1")["sold_2025"] == 2
    pages["https://docs.google.com/spreadsheets/d/AAA/edit?gid=1"] = [["only a header"]]
    assert "Nothing found" in loaded.post("/api/sold/sync", json={"years": ["2025"]}).json()["status"]["years"]["2025"]["error"]
    # uploading a file instead of a link
    r = loaded.post("/api/sold/upload", content=b"d,sku\n1,SKU-2\n2,SKU-2\n3,SKU-2\n4,SKU-2\n", headers={"X-Filename": "t.csv", "X-Year": "2025"})
    assert r.status_code == 200, r.text
    assert loaded.get("/api/products/SKU-2").json()["product"]["sold_2025"] == 2       # the 2025 range starts at row 4, so only the last two lines count
    assert loaded.post("/api/sold/config", json={"2025": {"url": "https://evil.example.com/x"}}).status_code == 400


def test_delete_and_restore_products(loaded):
    r = loaded.post("/api/products/delete", json={"skus": ["SKU-2", "SKU-3", "NOPE"]}).json()
    assert r["deleted"] == 2 and r["skus"] == ["SKU-2", "SKU-3"]
    assert loaded.get("/api/products/SKU-2").status_code == 400            # "Product not found."
    assert "SKU-2" not in loaded.get("/api/sheet/skus").json()["skus"]
    assert any(a["action"] == "deleted" for a in loaded.get("/api/activity", params={"q": "SKU-2"}).json()["items"])
    # undo: the product comes back exactly as it was
    back = loaded.post("/api/products/restore", json={"skus": ["SKU-2", "SKU-3"]}).json()
    assert back["restored"] == 2
    p = loaded.get("/api/products/SKU-2").json()["product"]
    assert p["name"] == "Wooden Frame 8 inch" and p["source_price"] == 5.0
    # a SKU that was deleted and then used again is not overwritten by a restore
    loaded.post("/api/products/delete", json={"skus": ["SKU-3"]})
    new = {"sku": "SKU-3", "name": "Another", "link": "https://www.amazon.com/dp/B0ABCDEFGH", "source_price": 5, "weight": 8, "category": "Decor"}
    assert loaded.post("/api/products/create-batch", json={"rows": [new]}).json()["created"] == 1
    assert loaded.post("/api/products/restore", json={"skus": ["SKU-3"]}).json()["restored"] == 0
    assert loaded.get("/api/products/SKU-3").json()["product"]["name"] == "Another"
    assert loaded.post("/api/products/delete", json={"skus": []}).status_code == 400


def test_recently_deleted_list_survives_and_feeds_restore(loaded):
    loaded.post("/api/products/delete", json={"skus": ["SKU-2"]})
    loaded.post("/api/products/delete", json={"skus": ["SKU-3"]})
    items = loaded.get("/api/products/deleted").json()["items"]
    assert [x["sku"] for x in items] == ["SKU-3", "SKU-2"] and items[1]["name"] == "Wooden Frame 8 inch" and items[0]["by"]
    loaded.post("/api/products/restore", json={"skus": ["SKU-2"]})
    assert [x["sku"] for x in loaded.get("/api/products/deleted").json()["items"]] == ["SKU-3"]      # restored ones leave the list


def test_activity_filter_shows_who_deleted_rows_and_columns(loaded):
    loaded.post("/api/products/delete", json={"skus": ["SKU-2"]})
    col = loaded.post("/api/sheet/columns/add", json={"label": "Notes", "type": "text"}).json()
    key = col["columns"][-1]["key"]
    loaded.post("/api/sheet/columns/delete", json={"key": key})
    cells(loaded, ("SKU-1", "remarks", "hello"))
    feed = lambda kind: loaded.get("/api/activity", params={"kind": kind}).json()["items"]
    rows = feed("rows")
    assert [(a["action"], a["sku"], a["user"]) for a in rows] == [("deleted", "SKU-2", "The Boss")]
    assert [a["action"] for a in feed("columns")] == ["deleted a column", "added a column"]
    assert {a["action"] for a in feed("edits")} == {"edited in the sheet"}
    assert len(feed("")) >= 4


def test_notin_filters_and_long_filters_travel_in_the_body(loaded):
    import json
    cells(loaded, ("SKU-1", "manual_status", "In Stock"), ("SKU-2", "manual_status", "OOS"), ("SKU-3", "manual_status", "Check"))
    skus = lambda p: set(loaded.post("/api/sheet/skus", json=p).json()["skus"])
    assert skus({"cf_manual_status": "notin:" + json.dumps(["OOS"])}) == {"SKU-1", "SKU-3", "SKU-4"}        # everything except OOS, blanks included
    assert skus({"cf_manual_status": "notin:" + json.dumps(["OOS", "(blank)"])}) == {"SKU-1", "SKU-3"}
    assert skus({"cf_manual_status": "notin:" + json.dumps([])}) == {"SKU-1", "SKU-2", "SKU-3", "SKU-4"}
    assert skus({"cf_inventory": "notin:" + json.dumps(["TRUE"])}) == skus({"cf_inventory": "FALSE"})
    # 5,000 long values: far too much for a web address, fine in a request body
    huge = ["Delivery text number %05d " % i + "x" * 150 for i in range(3000)] + ["OOS"]
    assert skus({"cf_manual_status": "notin:" + json.dumps(huge)}) == {"SKU-1", "SKU-3", "SKU-4"}
    v = loaded.post("/api/sheet/values", json={"col": "manual_status", "cf_manual_status": "in:[]"}).json()["values"]
    assert {x["v"] for x in v} >= {"In Stock", "OOS", "Check"}
    r = loaded.post("/api/products/export.csv", json={"cf_manual_status": "in:" + json.dumps(["OOS"])})
    assert r.status_code == 200 and b"SKU-2" in r.content and b"SKU-1," not in r.content


def test_show_edit_history_of_one_cell(loaded):
    hist = lambda sku, field: loaded.get("/api/sheet/history", params={"sku": sku, "field": field}).json()["events"]
    cells(loaded, ("SKU-1", "source_price", "11"))
    cells(loaded, ("SKU-1", "source_price", "12.5"), ("SKU-1", "remarks", "checked"))
    ev = hist("SKU-1", "source_price")
    assert [(e["old"], e["new"], e["who"], e["kind"]) for e in ev] == [("11", "12.5", "The Boss", "edit"), ("10", "11", "The Boss", "edit")]
    assert [e["new"] for e in hist("SKU-1", "remarks")] == ["checked"]                 # other cells keep their own history
    assert hist("SKU-1", "name") == []
    # TRUE/FALSE cells, calculated overrides, SKU renames and custom columns are recorded too
    cells(loaded, ("SKU-1", "inventory", "FALSE"), ("SKU-1", "fee", "9.5"))
    assert [(e["old"], e["new"]) for e in hist("SKU-1", "inventory")] == [("TRUE", "FALSE")]
    assert [(e["old"], e["new"]) for e in hist("SKU-1", "fee")][0][1] == "9.5"
    cells(loaded, ("SKU-1", "sku", "SKU-1B"))
    assert [(e["old"], e["new"]) for e in hist("SKU-1B", "sku")] == [("SKU-1", "SKU-1B")]
    # entries written before per-cell history existed are read from their text
    from app import db as _db
    with _db.tx() as c:
        c.execute("INSERT INTO activity(at, user, action, sku, detail) VALUES('2026-01-01 09:00:00','Old User','edited','SKU-2','Name: Wooden Frame → Wooden Frame 8 inch; Weight: 3 → 4')")
    assert [(e["who"], e["old"], e["new"]) for e in hist("SKU-2", "weight")] == [("Old User", "3", "4")]
    # the scraper's changes show up for the columns it fills, with the scraper PC as the "who"
    with _db.tx() as c:
        for i, (price, at) in enumerate([("$10.00", "2026-02-01 10:00:00"), ("$10.00", "2026-02-02 10:00:00"), ("$12.00", "2026-02-03 10:00:00")]):
            c.execute("INSERT INTO history(sku, run_id, price, availability, run_at, status, result, agent, at) VALUES('SKU-3',1,?,?,?,?,?,?,?)",
                      (price, "In Stock", at, "Normal", "ok", "PC-7", at))
    ev = hist("SKU-3", "price")
    assert len(ev) == 1 and ev[0]["who"] == "Scraper (PC-7)" and (ev[0]["old"], ev[0]["new"]) == ("$10.00", "$12.00") and ev[0]["kind"] == "scraper"
    assert loaded.get("/api/sheet/history", params={"sku": "NOPE", "field": "name"}).status_code == 400


def test_michaels_columns_are_locked_to_the_import(loaded):
    # a paste/edit that mixes locked and normal cells saves the normal ones and refuses only the locked ones
    r = cells(loaded, ("SKU-1", "michaels_sku_number", "999"), ("SKU-1", "michaels_price", "1"), ("SKU-1", "michaels_qty", "2"),
              ("SKU-1", "remarks", "kept"))
    assert r["changed"] == 1 and r["error_count"] == 3 and all("Michaels download" in e["error"] for e in r["errors"])
    x = row(loaded, "SKU-1")
    assert x["michaels_sku_number"] == "430503183213510657" and x["remarks"] == "kept"
    # clearing (Delete key) is refused too, so an empty cell always means "not in the download"
    r = cells(loaded, ("SKU-1", "michaels_sku_number", ""))
    assert r["changed"] == 0 and r["error_count"] == 1
    assert row(loaded, "SKU-1")["michaels_sku_number"] == "430503183213510657"
    # the import itself still writes them
    csv = b"SKU,Price,Qty,SKU Number\nSKU-1,31,8,777\n"
    pv = loaded.post("/api/michaels/preview", content=csv, headers={"X-Filename": "m.csv"}).json()
    assert loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": False}).status_code == 200
    x = row(loaded, "SKU-1")
    assert (x["michaels_sku_number"], x["michaels_price"], x["michaels_qty"]) == ("777", 31, 8)
    # bulk edits can't reach them either
    assert loaded.post("/api/products/bulk", json={"skus": ["SKU-1"], "patch": {"michaels_qty": 1}, "action": "x"}).status_code == 400


def _member(name):
    from app import auth
    from app.main import app
    from fastapi.testclient import TestClient
    auth.add_user(name, name.title(), "password123", "member")
    m = TestClient(app)
    assert m.post("/api/login", json={"username": name, "password": "password123"}).status_code == 200
    return m


def test_admin_chooses_who_can_edit_each_column(loaded):
    mia, ben = _member("mia"), _member("ben")
    edit = lambda who, field, value: who.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-2", "field": field, "value": value}]}).json()
    # the Michaels columns start protected: nobody types in them, not even an admin
    meta = loaded.get("/api/meta").json()["locks"]
    assert set(meta) == {"michaels_sku_number", "michaels_price", "michaels_qty"} and meta["michaels_qty"]["mode"] == "nobody"
    assert edit(loaded, "michaels_qty", "5")["error_count"] == 1
    # only admins can protect or unprotect
    assert mia.post("/api/locks", json={"col": "remarks", "mode": "admins"}).status_code == 403
    # admins only
    assert loaded.post("/api/locks", json={"col": "remarks", "mode": "admins"}).status_code == 200
    assert edit(mia, "remarks", "x")["error_count"] == 1 and "only admins" in edit(mia, "remarks", "x")["errors"][0]["error"]
    assert edit(loaded, "remarks", "boss was here")["changed"] == 1
    # admins plus chosen people (a name that isn't a real user is ignored)
    loaded.post("/api/locks", json={"col": "remarks", "mode": "people", "users": ["ben", "ghost"]})
    assert loaded.get("/api/meta").json()["locks"]["remarks"]["users"] == ["ben"]
    assert edit(ben, "remarks", "ben ok")["changed"] == 1 and edit(mia, "remarks", "mia no")["error_count"] == 1
    # other cells in the same save still go through; the protected one is refused
    both = mia.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-2", "field": "remarks", "value": "no"}, {"sku": "SKU-2", "field": "category", "value": "Cat"}]}).json()
    assert both["changed"] == 1 and both["error_count"] == 1
    # the drawer's edit, bulk edits and new rows obey the same rule
    assert mia.post("/api/products/SKU-2", json={"patch": {"remarks": "no"}}).status_code == 400
    assert mia.post("/api/products/bulk", json={"skus": ["SKU-2"], "patch": {"remarks": "no"}, "action": "x"}).status_code == 400
    assert ben.post("/api/products/bulk", json={"skus": ["SKU-2"], "patch": {"remarks": "ben bulk"}, "action": "x"}).status_code == 200
    loaded.post("/api/locks", json={"col": "category", "mode": "admins"})
    row_ = {"sku": "NEW-9", "name": "N", "link": "https://www.amazon.com/dp/B0ABCDEFGH", "source_price": 5, "weight": 8, "category": "Decor"}
    assert mia.post("/api/products/create-batch", json={"rows": [row_]}).status_code == 400          # CATEGORY is admins-only
    assert loaded.post("/api/products/create-batch", json={"rows": [row_]}).json()["created"] == 1
    # remove a protection; unlock a Michaels column and the allowed person can type in it
    loaded.post("/api/locks", json={"col": "remarks", "mode": "none"})
    assert edit(mia, "remarks", "free again")["changed"] == 1
    loaded.post("/api/locks", json={"col": "michaels_qty", "mode": "admins", "note": "fix by hand"})
    assert edit(loaded, "michaels_qty", "5")["changed"] == 1 and edit(mia, "michaels_qty", "6")["error_count"] == 1
    # a column that doesn't exist, or a mode that doesn't exist, is refused; the log says who protected what
    assert loaded.post("/api/locks", json={"col": "nope", "mode": "admins"}).status_code == 400
    assert loaded.post("/api/locks", json={"col": "remarks", "mode": "sometimes"}).status_code == 400
    assert any(a["action"] == "protected a column" for a in loaded.get("/api/activity").json()["items"])
    # imports are never blocked by protection
    pv = loaded.post("/api/michaels/preview", content=b"SKU,Price,Qty,SKU Number\nSKU-1,31,8,777\n", headers={"X-Filename": "m.csv"}).json()
    assert loaded.post("/api/michaels/apply", json={"token": pv["token"], "clear_missing": False}).status_code == 200
    assert row(loaded, "SKU-1")["michaels_sku_number"] == "777"


def test_work_list_survives_reloads_and_only_closes_on_confirm(loaded):
    params = {"queue": "High Price"}
    skus = loaded.get("/api/sheet/skus", params=params).json()["skus"]
    assert skus == ["SKU-1"]
    wid = loaded.post("/api/work", json={"name": "High Price today", "params": params, "skus": skus}).json()["id"]
    # nothing done yet: reloading the page (asking again) shows the same list, one row still open
    w = loaded.get(f"/api/work/{wid}").json()
    assert w["skus"] == ["SKU-1"] and w["resolved"] == [] and w["edited"] == [] and w["open"] == 1
    assert [x["name"] for x in loaded.get("/api/work").json()["items"]] == ["High Price today"]
    # finishing with the row still unresolved asks first and doesn't close anything
    r = loaded.post(f"/api/work/{wid}/finish", json={}).json()
    assert r["ok"] is False and r["unresolved"] == 1 and loaded.get(f"/api/work/{wid}").status_code == 200
    # the employee fixes the price: the row is now resolved and edited, but it is still in the saved list
    cells(loaded, ("SKU-1", "source_price", "$12.50"))
    assert loaded.get("/api/sheet/skus", params=params).json()["skus"] == []                # gone from the normal view
    w = loaded.get(f"/api/work/{wid}").json()
    assert w["skus"] == ["SKU-1"] and w["resolved"] == ["SKU-1"] and w["edited"] == ["SKU-1"] and w["open"] == 0
    prog = loaded.get("/api/work").json()["items"][0]
    assert (prog["total"], prog["edited"], prog["resolved"], prog["open"]) == (1, 1, 1, 0)
    # confirm: everything resolved, the list closes and the log records it
    done = loaded.post(f"/api/work/{wid}/finish", json={}).json()
    assert done["ok"] and done["resolved"] == 1 and done["skipped"] == 0
    assert loaded.get("/api/work").json()["items"] == [] and loaded.get(f"/api/work/{wid}").status_code == 400
    assert any(a["action"] == "finished a work list" for a in loaded.get("/api/activity").json()["items"])
    # finishing anyway records the unresolved rows as skipped; discarding just closes the list
    cells(loaded, ("SKU-1", "source_price", "5"))                                              # High Price again
    w2 = loaded.post("/api/work", json={"name": "again", "params": params, "skus": ["SKU-1"]}).json()["id"]
    forced = loaded.post(f"/api/work/{w2}/finish", json={"force": True}).json()
    assert forced["ok"] and forced["skipped"] == 1 and forced["forced"] is True
    w3 = loaded.post("/api/work", json={"name": "x", "params": params, "skus": ["SKU-1"]}).json()["id"]
    assert loaded.post(f"/api/work/{w3}/discard").json()["ok"] and loaded.get("/api/work").json()["items"] == []
    # another person can't see or touch my list; an empty list isn't started
    mia = _member("wanda")
    w4 = loaded.post("/api/work", json={"name": "mine", "params": params, "skus": ["SKU-1"]}).json()["id"]
    assert mia.get(f"/api/work/{w4}").status_code == 400 and mia.get("/api/work").json()["items"] == []
    assert loaded.post("/api/work", json={"name": "empty", "params": params, "skus": []}).status_code == 400
