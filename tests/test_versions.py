from fastapi.testclient import TestClient

from app import auth, db, versions
from app.main import app
from tests.test_sheet import cells, row


def _member(name="mia"):
    auth.add_user(name, name.title(), "password123", "member")
    m = TestClient(app)
    assert m.post("/api/login", json={"username": name, "password": "password123"}).status_code == 200
    return m


def _save(c, name="Before the review"):
    r = c.post("/api/versions", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_version_can_be_compared_and_restored(loaded):
    vid = _save(loaded)
    listed = loaded.get("/api/versions").json()["items"]
    assert listed[0]["name"] == "Before the review" and listed[0]["rows"] == 4 and listed[0]["kind"] == "named"
    assert loaded.get(f"/api/versions/{vid}/changes").json()["counts"] == {"changed": 0, "cells": 0, "deleted": 0, "added": 0}

    # a forgotten work list: edits, a typed override, a custom column, a delete and a new product
    key = loaded.post("/api/sheet/columns/add", json={"label": "Buyer", "type": "text"}).json()["key"]
    vid2 = _save(loaded, "With the column")
    cells(loaded, ("SKU-1", "remarks", "oops"), ("SKU-1", "weight", "99"), ("SKU-1", "fee", "9.5"), ("SKU-2", "manual_status", "OOS"), ("SKU-2", key, "Ann"))
    loaded.post("/api/products/delete", json={"skus": ["SKU-3"]})
    new = {"sku": "NEW-1", "name": "Brand new", "link": "https://www.amazon.com/dp/B000000099", "source_price": 5, "weight": 8, "category": "Craft"}
    assert loaded.post("/api/products/create", json={"product": new}).json()["ok"]

    ch = loaded.get(f"/api/versions/{vid2}/changes").json()
    assert ch["counts"]["changed"] == 2 and ch["counts"]["deleted"] == 1 and ch["counts"]["added"] == 1 and ch["counts"]["cells"] == 5
    one = next(i for i in ch["items"] if i["sku"] == "SKU-1")
    assert {(x["f"], x["then"], x["now"]) for x in one["cells"]} >= {("remarks", "", "oops"), ("weight", 16, 99)}
    assert [i["sku"] for i in loaded.get(f"/api/versions/{vid2}/changes", params={"q": "sku-2"}).json()["items"]] == ["SKU-2"]
    assert [i["kind"] for i in loaded.get(f"/api/versions/{vid2}/changes", params={"kind": "deleted"}).json()["items"]] == ["deleted"]

    # restore two chosen products: SKU-1's typed values come back, the deleted row returns, the new product stays
    r = loaded.post(f"/api/versions/{vid2}/restore", json={"skus": ["SKU-1", "SKU-3"]}).json()
    assert r["products"] == 1 and r["brought_back"] == 1 and r["removed"] == 0 and r["safety_id"]
    a = row(loaded, "SKU-1")
    assert a["remarks"] == "" and a["weight"] == 16 and "fee" not in a.get("_ovr", [])
    assert row(loaded, "SKU-3")["sku"] == "SKU-3"
    assert row(loaded, "SKU-2")["manual_status"] == "OOS"                      # not chosen: untouched
    acts = loaded.get("/api/activity").json()["items"]
    assert any(x["action"] == "restored a version" and x["sku"] == "SKU-1" for x in acts)

    # the restore was saved first, so it can be undone: go back to the "Before restoring" version
    assert loaded.post(f"/api/versions/{r['safety_id']}/restore", json={"skus": ["SKU-1"]}).json()["products"] == 1
    assert row(loaded, "SKU-1")["remarks"] == "oops"

    # everything, also removing what was added since
    r = loaded.post(f"/api/versions/{vid2}/restore", json={"everything": True, "remove_added": True}).json()
    assert r["removed"] == 1 and loaded.post("/api/sheet/rows", json={"skus": ["NEW-1"]}).json()["rows"] == []
    assert row(loaded, "SKU-2")["manual_status"] == "" and row(loaded, "SKU-2").get(key) in ("", None)
    assert loaded.get(f"/api/versions/{vid2}/changes").json()["counts"]["changed"] == 0


def test_who_may_restore_and_what_is_protected(loaded):
    vid = _save(loaded)
    cells(loaded, ("SKU-1", "remarks", "typed"), ("SKU-1", "category", "Other"))
    mia = _member()
    assert mia.get("/api/versions").status_code == 200
    assert mia.post(f"/api/versions/{vid}/restore", json={"everything": True}).status_code == 403
    assert mia.post(f"/api/versions/{vid}/restore", json={"skus": []}).status_code == 400
    assert mia.post(f"/api/versions/{vid}/delete").status_code == 403
    # a protected column is skipped for someone who may not edit it, and counted
    loaded.post("/api/locks", json={"col": "category", "mode": "admins"})
    r = mia.post(f"/api/versions/{vid}/restore", json={"skus": ["SKU-1"]}).json()
    assert r["cells"] == 1 and r["protected"] == 1
    a = row(loaded, "SKU-1")
    assert a["remarks"] == "" and a["category"] == "Other"
    assert loaded.post(f"/api/versions/{vid}/delete").status_code == 200
    assert loaded.get(f"/api/versions/{vid}/changes").status_code == 400


def test_versions_are_saved_when_something_was_edited_and_old_ones_are_pruned(loaded):
    assert versions.take_if_edited() is None or True
    c = db.conn()
    n = c.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    cells(loaded, ("SKU-1", "remarks", "a"))
    first = versions.take_if_edited()
    assert first and c.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == n + 1
    assert versions.take_if_edited() is None                                   # nothing edited since
    assert versions.take_if_edited(min_gap=30) is None
    cells(loaded, ("SKU-1", "remarks", "b"))
    assert versions.take_if_edited(min_gap=30) is None                         # too soon after the last one
    assert versions.take_if_edited() is not None
    # an old automatic version keeps one per day, a named one is never removed
    keep = _save(loaded, "Keep me")
    for at in ("2020-01-01 08:00:00", "2020-01-01 09:00:00"):
        c.execute("INSERT INTO versions(at, kind, name, by, rows, activity_id, size) VALUES(?, 'auto', '', '', 0, 0, 0)", (at,))
    c.execute("UPDATE versions SET at='2020-01-02 08:00:00' WHERE id=?", (keep,))
    versions.prune()
    kinds = [r["kind"] for r in c.execute("SELECT kind FROM versions WHERE at < '2021'")]
    assert kinds == ["named"]                                                  # the old automatic ones are past the 30 days
    assert c.execute("SELECT 1 FROM versions WHERE id=?", (keep,)).fetchone()
