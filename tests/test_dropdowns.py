from fastapi.testclient import TestClient

from app import auth
from app.main import app
from tests.test_sheet import cells, row


def _member(name="mia"):
    auth.add_user(name, name.title(), "password123", "member")
    m = TestClient(app)
    assert m.post("/api/login", json={"username": name, "password": "password123"}).status_code == 200
    return m


def test_a_column_can_become_a_fixed_dropdown(loaded):
    cells(loaded, ("SKU-1", "manual_status", "5"), ("SKU-2", "manual_status", "4"), ("SKU-3", "manual_status", "10"))
    # the dropdown starts from what the column holds now, numbers in order
    assert loaded.get("/api/dropdowns/values", params={"col": "manual_status"}).json()["values"] == ["4", "5", "10"]
    assert loaded.post("/api/dropdowns", json={"col": "manual_status", "values": ["3", "4", "5", "6", " 5 "], "enabled": True}).status_code == 200
    meta = loaded.get("/api/meta").json()
    assert meta["dropdowns"]["manual_status"] == ["3", "4", "5", "6"] and meta["manual_statuses"] == ["3", "4", "5", "6"]
    # a listed value is saved, anything else is refused and says what is allowed
    assert cells(loaded, ("SKU-1", "manual_status", "6"))["changed"] == 1 and row(loaded, "SKU-1")["manual_status"] == "6"
    bad = cells(loaded, ("SKU-1", "manual_status", "9"))
    assert bad["changed"] == 0 and bad["error_count"] == 1 and "isn't in the dropdown" in bad["errors"][0]["error"] and "3, 4, 5, 6" in bad["errors"][0]["error"]
    # clearing a cell is always allowed; a value that is already there (even one outside the list) is not an error
    assert cells(loaded, ("SKU-1", "manual_status", ""))["changed"] == 1
    assert cells(loaded, ("SKU-3", "manual_status", "10"))["error_count"] == 0
    # the drawer and bulk edits obey it too
    assert loaded.post("/api/products/SKU-2", json={"patch": {"manual_status": "99"}}).status_code == 400
    assert loaded.post("/api/products/bulk", json={"skus": ["SKU-2"], "patch": {"manual_status": "99"}, "action": "x"}).status_code == 400
    assert loaded.post("/api/products/bulk", json={"skus": ["SKU-2"], "patch": {"manual_status": "3"}, "action": "x"}).status_code == 200
    # the values can be edited, then the dropdown turned off: free typing again
    loaded.post("/api/dropdowns", json={"col": "manual_status", "values": ["Done", "Not yet"], "enabled": True})
    assert cells(loaded, ("SKU-1", "manual_status", "done"))["changed"] == 1 and row(loaded, "SKU-1")["manual_status"] == "Done"
    loaded.post("/api/dropdowns", json={"col": "manual_status", "values": [], "enabled": False})
    assert "manual_status" not in loaded.get("/api/meta").json()["dropdowns"]
    assert cells(loaded, ("SKU-1", "manual_status", "anything"))["changed"] == 1
    assert any(a["action"] == "changed a dropdown" for a in loaded.get("/api/activity").json()["items"])


def test_custom_text_columns_can_be_dropdowns_and_others_cannot(loaded):
    key = loaded.post("/api/sheet/columns/add", json={"label": "Buyer", "type": "text"}).json()["key"]
    assert loaded.post("/api/dropdowns", json={"col": key, "values": ["Ann", "Ben"], "enabled": True}).status_code == 200
    assert cells(loaded, ("SKU-1", key, "ann"))["changed"] == 1 and row(loaded, "SKU-1")[key] == "Ann"
    assert cells(loaded, ("SKU-1", key, "Zed"))["error_count"] == 1
    # names, links, numbers and columns that don't exist can't be dropdowns; at least one value is needed
    for col in ("name", "link", "weight", "sold_2026", "nope"):
        assert loaded.post("/api/dropdowns", json={"col": col, "values": ["a"], "enabled": True}).status_code == 400, col
    assert loaded.post("/api/dropdowns", json={"col": "remarks", "values": [], "enabled": True}).status_code == 400


def test_only_admins_set_dropdowns_and_everyone_uses_them(loaded):
    mia = _member()
    assert mia.post("/api/dropdowns", json={"col": "remarks", "values": ["a"], "enabled": True}).status_code == 403
    loaded.post("/api/dropdowns", json={"col": "remarks", "values": ["ok", "recheck"], "enabled": True})
    assert mia.get("/api/meta").json()["dropdowns"]["remarks"] == ["ok", "recheck"]
    assert mia.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-2", "field": "remarks", "value": "nope"}]}).json()["error_count"] == 1
    assert mia.post("/api/sheet/cells", json={"edits": [{"sku": "SKU-2", "field": "remarks", "value": "RECHECK"}]}).json()["changed"] == 1
