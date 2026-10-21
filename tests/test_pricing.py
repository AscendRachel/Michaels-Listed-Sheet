import json
import math

from app import db


def pricing(c):
    return c.get("/api/meta").json()["pricing"]


def test_only_one_fee_table_exists(loaded):
    p = pricing(loaded)
    assert list(p["tables"]) == ["current"] and p["active_table"] == "current"
    assert loaded.get("/api/meta").json()["table_labels"] == {"current": "3PL fee by weight"}


def test_settings_saved_by_an_older_version_collapse_to_the_old_rates(loaded):
    """An older database may hold a second table and even point at it; the 'current' (old) rates win."""
    old = {"markup": 1.6, "fee_buffer": 4, "active_table": "y2026",
           "tables": {"current": [{"max": 10, "fee": 5.0}, {"max": 40, "fee": 9.0}], "y2026": [{"max": 99, "fee": 50.0}]}}
    with db.tx() as c:
        db.set_setting(c, "pricing", json.dumps(old))
    p = pricing(loaded)
    assert p["active_table"] == "current" and list(p["tables"]) == ["current"]
    assert p["tables"]["current"] == old["tables"]["current"] and p["markup"] == 1.6 and p["fee_buffer"] == 4
    with db.tx() as c:                                    # leave the shared test database as it was
        c.execute("DELETE FROM settings WHERE key='pricing'")


def test_editing_the_weight_tiers_reprices_everything(loaded):
    before = loaded.get("/api/products/SKU-1").json()["product"]["auto_price"]          # 16 oz: tier "up to 16" = 6.33
    p = pricing(loaded)
    tiers = [{"max": 4, "fee": 4.36}, {"max": 8, "fee": 4.68}, {"max": 12, "fee": 5.62}, {"max": 16, "fee": 8.0},
             {"max": 32, "fee": 9.21}]                                                    # the 3PL raised the 16 oz rate
    prev = loaded.post("/api/pricing/preview", json={"pricing": {**p, "tables": {"current": tiers}}}).json()
    assert prev["up"] >= 1 and prev["none"] >= 1
    r = loaded.post("/api/pricing", json={"pricing": {**p, "tables": {"current": tiers}}}).json()
    assert r["ok"] and list(r["pricing"]["tables"]) == ["current"]
    after = loaded.get("/api/products/SKU-1").json()["product"]
    assert after["fee"] == 13.0 and after["auto_price"] == math.floor((10 + 13.0) * 1.53) + 0.99 > before
    # heavier than the last tier can't be priced any more (and shows in the data-quality queue)
    loaded.post("/api/products/SKU-2", json={"patch": {"weight": 40}})
    assert loaded.get("/api/products/SKU-2").json()["product"]["auto_price"] is None
    assert loaded.get("/api/queues").json()["counts"]["dq-fee"] >= 1
    log = loaded.get("/api/activity", params={"q": "3PL fee table"}).json()["items"]
    assert log and "5 weight tiers" in log[0]["detail"] and "recalculated" in log[0]["detail"]


def test_tiers_are_sorted_and_validated(loaded):
    p = pricing(loaded)
    save = lambda tiers, **kw: loaded.post("/api/pricing", json={"pricing": {**p, "tables": {"current": tiers}, **kw}})
    assert save([{"max": 16, "fee": 6.0}, {"max": 4, "fee": 4.0}]).json()["pricing"]["tables"]["current"][0]["max"] == 4   # sorted
    assert save([]).status_code == 400
    assert save([{"max": 8, "fee": 1}, {"max": 8, "fee": 2}]).status_code == 400                                          # same limit twice
    assert save([{"max": 0, "fee": 1}]).status_code == 400
    assert save([{"max": 8, "fee": -1}]).status_code == 400
    assert save([{"max": "abc", "fee": 1}]).status_code == 400
    assert loaded.post("/api/pricing", json={"pricing": {**p, "markup": 9}}).status_code == 400
    assert loaded.post("/api/pricing", json={"pricing": {**p, "fee_buffer": -1}}).status_code == 400
    assert loaded.post("/api/pricing", json={"pricing": {**p, "markup": "x"}}).status_code == 400
    assert loaded.post("/api/pricing", json={"pricing": p}).json()["ok"]                                                    # put the defaults back


def test_current_pricing_impact_is_zero(loaded):
    i = loaded.get("/api/pricing/impact").json()
    assert i["up"] == 0 and i["down"] == 0 and i["products"] == 4 and "other" not in i
