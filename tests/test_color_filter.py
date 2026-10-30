from app import db
from tests.test_sheet import cells


def _skus(c, color):
    r = c.post("/api/sheet/skus", json={"color": color})
    assert r.status_code == 200, r.text
    return sorted(r.json()["skus"])


def test_column_conditions_and_sort_by_colour(loaded):
    def ask(**q):
        r = loaded.post("/api/sheet/skus", json=q)
        assert r.status_code == 200, r.text
        return r.json()["skus"]

    # text conditions (names: Acrylic Paint Set 12 Pack, Wooden Frame 8 inch, No Weight Item, Not Amazon)
    assert ask(cf_name="eq:wooden frame 8 inch") == ["SKU-2"]
    assert ask(cf_name="sw:No ") == ["SKU-3", "SKU-4"] or sorted(ask(cf_name="sw:No ")) == ["SKU-3", "SKU-4"]
    assert ask(cf_name="ew:Pack") == ["SKU-1"]
    assert sorted(ask(cf_name="nc:frame")) == ["SKU-1", "SKU-3", "SKU-4"]
    assert ask(cf_name="Frame") == ["SKU-2"]                                      # a plain value still means "contains"
    # number conditions
    assert sorted(ask(cf_weight="!=16")) == ["SKU-2", "SKU-4"]
    assert ask(cf_weight="4..8") == ["SKU-2", "SKU-4"] or sorted(ask(cf_weight="4..8")) == ["SKU-2", "SKU-4"]
    # sort by colour puts the rows of that colour first, a leading - puts them last
    cells(loaded, ("SKU-3", "status", "High Price"))
    db.conn().execute("UPDATE products SET inventory=1, status='High Price' WHERE sku='SKU-3'")
    red = set(ask(color="hp"))
    assert "SKU-3" in red and len(red) < 4
    first, last = ask(sort="color:hp"), ask(sort="-color:hp")
    assert set(first[:len(red)]) == red and set(last[-len(red):]) == red
    assert ask(sort="color:nonsense") == ask()                                     # an unknown colour leaves the order alone


def test_price_differences_only_count_for_products_with_inventory_on(loaded):
    c = db.conn()
    c.execute("UPDATE products SET michaels_price=99, auto_price=10, inventory=1 WHERE sku='SKU-1'")
    c.execute("UPDATE products SET michaels_price=99, auto_price=10, inventory=0 WHERE sku='SKU-2'")
    assert loaded.get("/api/queues").json()["counts"]["mz-price"] == 1
    assert loaded.post("/api/sheet/skus", json={"queue": "mz-price"}).json()["skus"] == ["SKU-1"]
    assert _skus(loaded, "cell_price") == ["SKU-1"]                           # the red cell follows the same rule
    csv = loaded.get("/api/export/michaels-fixes.csv")
    assert csv.status_code == 200 and "SKU-1" in csv.text and "SKU-2" not in csv.text         # the price-fix export too


def test_filter_by_colour_finds_the_rows_the_grid_paints(loaded):
    # SKU-1 High Price, SKU-2 Out of Stock (both with Inventory on), SKU-3 Inventory off but the marketplace has stock,
    # SKU-4 Inventory on and marketplace stock (no colour at all)
    cells(loaded, ("SKU-1", "status", "High Price"), ("SKU-2", "status", "Out of Stock"), ("SKU-2", "delivery", "FREE Prime delivery"),
          ("SKU-4", "inventory", "TRUE"), ("SKU-3", "inventory", "FALSE"))
    c = db.conn()
    c.execute("UPDATE products SET michaels_qty=5 WHERE sku IN ('SKU-3','SKU-4')")
    c.execute("UPDATE products SET michaels_qty=NULL, inventory=1 WHERE sku IN ('SKU-1','SKU-2')")
    c.execute("UPDATE products SET sold_2026=2 WHERE sku='SKU-1'")
    c.execute("UPDATE products SET status='Not checked' WHERE sku IN ('SKU-3','SKU-4')")

    assert _skus(loaded, "oosdi") == [] and _skus(loaded, "oosv") == []
    c.execute("UPDATE products SET inventory=1, status='OOS Different Item' WHERE sku='SKU-3'")
    c.execute("UPDATE products SET status='OOS Variation' WHERE sku='SKU-4'")
    assert _skus(loaded, "oosdi") == ["SKU-3"] and _skus(loaded, "oosv") == ["SKU-4"]
    c.execute("UPDATE products SET inventory=0 WHERE sku='SKU-3'")
    c.execute("UPDATE products SET status='Not checked' WHERE sku IN ('SKU-3','SKU-4')")
    assert _skus(loaded, "hp") == ["SKU-1"]
    assert _skus(loaded, "oos") == ["SKU-2"]
    assert _skus(loaded, "mzoff") == ["SKU-3"]
    assert _skus(loaded, "none") == ["SKU-4"]
    assert _skus(loaded, "hp,oos") == ["SKU-1", "SKU-2"]                      # several colours: any of them
    assert _skus(loaded, "box_sold") == ["SKU-1"]
    assert _skus(loaded, "text_prime") == ["SKU-2"]
    assert _skus(loaded, "cell_qty") == ["SKU-3"]                              # stock at the marketplace, Inventory off
    assert _skus(loaded, "nonsense") == []                                     # an unknown colour shows nothing, not everything

    # the first rule wins: a row that is High Price AND has no marketplace stock is red, not dark green
    assert "SKU-1" not in _skus(loaded, "mzzero")
    assert _skus(loaded, "mzzero") == []
    c.execute("UPDATE products SET michaels_qty=0 WHERE sku='SKU-4'")
    assert _skus(loaded, "mzzero") == ["SKU-4"] and _skus(loaded, "cell_qty") == ["SKU-3", "SKU-4"]

    # it combines with the other filters, and is kept in saved views and work lists
    assert _skus(loaded, "hp,oos,mzoff") and loaded.post("/api/sheet/skus", json={"color": "hp,oos", "q": "Wooden"}).json()["skus"] == ["SKU-2"]
    assert loaded.post("/api/views", json={"name": "Reds", "params": {"color": "hp"}}).status_code == 200
    assert loaded.get("/api/views").json()["items"][0]["params"]["color"] == "hp"
    w = loaded.post("/api/work", json={"name": "Reds", "params": {"color": "hp"}, "skus": ["SKU-1"]}).json()
    assert loaded.get(f"/api/work/{w['id']}").json()["params"] == {"color": "hp"}
