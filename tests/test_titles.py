import pytest

from app import db, titles
from tests.test_sheet import cells, row


def score(a, b):
    return titles.check(a, b)


# ---- rewording must not raise an alarm (patterns taken from real catalog/Amazon pairs)
@pytest.mark.parametrize("catalog, amazon", [
    # Amazon trimmed a long keyword-stuffed name to its first part
    ("PINFFSHU Horror Gothic Skull Diamond Art Kits for Adults Halloween | Round Full Drill 5D Diamond Painting Kits Gem Dot",
     "PINFFSHU Horror Gothic Skull Diamond Art Kits for Adults Halloween"),
    # Amazon reworded and added keywords
    ("Acrylic Paint Set 12 Pack", "Acrylic Paint Set, 12 Pack Art Supplies"),
    # same size written in another unit
    ("11.8x15.7 Inch Diamond Painting Kit Forest Lake Wall Decor", "Diamond Painting Kit Forest Lake Wall Decor 30x40cm"),
    # fractions, a space-separated mixed number, inch marks
    ("Satin Ribbon 1-1/2\" x 100 Yards Red", "Red Satin Ribbon 1.5 inch x 100 yds"),
    ("2 1/2 Inch Angled Stain Brush Bulk Pack Wood Paint Brushes", "Magimate 2-1/2\" Angle Stain Brush Pack of 12 Nylon"),
    # a typo that drops one number of a dimension
    ("HummingBox Gift Box 9.5 X 6 X 3 inch Magnetic Lid", "HummingBox Gift Box 9.5 X 6 3 inch Red Magnetic Lid"),
    # plural vs singular, joined vs split words, a brand typo
    ("Floral Pillow Cases 18x18 Set of 4 Decorative", "Floral Pillowcases 18 x 18 inch 4 Pack Decorative"),
    ("4 Pcs LINGFANG Diamond Painting Bookmark Leather", "LINFANG 4 Pcs Diamond Painting Bookmarks Leather"),
    # the catalog name was cut off in the middle of a number: that half-number is not a measurement
    ("MAYPLUSS Winter Snowflake Christmas Wrapping Paper - 17 Inch X 3...",
     "MAYPLUSS Winter Snowflake Christmas Wrapping Paper - 17 Inch X 33 Feet - White"),
])
def test_rewording_is_not_a_problem(catalog, amazon):
    s, note, conflict = score(catalog, amazon)
    assert conflict == 0 and s >= 60, (s, note)


# ---- a real change of listing: the facts disagree
@pytest.mark.parametrize("catalog, amazon, words", [
    ("OuMuaMua 12pcs Crystal Christmas Ornaments", "OuMuaMua 24pcs Crystal Snowflake Ornaments", "Pack size differs"),
    ("20 Inch Spring Tulip Wreath Front Door", "22 Inch Spring Tulip Wreath Front Door", "Size differs"),
    ("Egofine 2 Pack 5x7 Rotating Floating Picture Frames", "Egofine 2 Pack 4x6 Rotating Floating Picture Frames", "Size differs"),
    ("hatisan 11 Ounces Fake Snow Decoration", "hatisan 13 Ounces Fake Snow Decoration", "Size differs"),
    ("Solid Double Face Ribbon 3/8\" X 25 Yards", "Double Faced Ribbon 5/8\" x 50 Yards", "Size differs"),
    ("12600Pcs Resin Jelly Rhinestones", "18000Pcs Resin Jelly Rhinestones", "Pack size differs"),     # not read as 2600 / 8000
])
def test_changed_facts_flag_the_listing(catalog, amazon, words):
    s, note, conflict = score(catalog, amazon)
    assert conflict == 1 and words in note and s <= 40


def test_a_different_product_is_a_low_match_not_a_changed_listing():
    s, note, conflict = score("Garden Hose Nozzle Brass Spray", "Wireless Bluetooth Headphones Noise Cancelling")
    assert conflict == 0 and s < 20 and "Key words not on Amazon" in note and "New on Amazon" in note
    assert score("Something", "")[0] is None and score("", "Something")[0] is None


def test_agreeing_facts_count_as_evidence():
    a, b = "300 Pack Paper Cups 8 oz Disposable Hot Cold Beverage Drinking", "Clawsoff 300 Pack Paper Cups 8 oz Bulk Coffee Navy Floral"
    s, _, conflict = score(a, b)
    assert conflict == 0 and s >= 50            # the shared pack count and size keep it above the default alert line (50)
    assert s > titles.check("Paper Cups Disposable Hot Cold Beverage Drinking", "Clawsoff Paper Cups Bulk Coffee Navy Floral")[0]


def test_synonyms_from_the_team():
    a, b = "Vintage Ceramic Mug Set Floral", "Vintage Ceramic Cup Set Floral"
    plain = titles.check(a, b, titles.Model())[0]
    taught = titles.check(a, b, titles.Model(synonyms=["mug, cup"]))[0]
    assert taught > plain and taught == 100


def test_same_listing_judges_two_amazon_titles():
    assert titles.same_listing("Acme 12 Pack Glitter Glue Pens for Kids Art", "Acme Glitter Glue Pens 12 Pack Kids Craft")
    assert not titles.same_listing("Acme 12 Pack Glitter Glue Pens", "Acme 24 Pack Glitter Glue Pens")      # pack changed
    assert not titles.same_listing("Acme Glitter Glue Pens", "Zeta Wooden Garden Trellis Planter")


# ---- in the app
def scrape(c, sku, title, price="$20.00"):
    key = c.get("/api/scraper/key").json()["key"]
    h = {"X-Agent-Key": key}
    run = c.post("/api/agent/start", json={"agent_id": "a", "name": "PC"}, headers=h).json()["run_id"]
    c.post("/api/agent/claim", json={"agent_id": "a", "run_id": run, "n": 50}, headers=h)
    c.post("/api/agent/result", json=dict(agent_id="a", run_id=run, sku=sku, result="ok", price=price, availability="In Stock",
                                         delivery="", run_at="2026-02-01 10:00:00", title=title), headers=h)


def test_queues_separate_changed_listings_from_low_matches(loaded):
    scrape(loaded, "SKU-1", "Acrylic Paint Set 24 Pack")                 # SKU-1 is "Acrylic Paint Set 12 Pack"
    p = row(loaded, "SKU-1")
    assert p["title_conflict"] == 1 and p["title_check"].startswith("Listing changed: Pack size differs")
    counts = loaded.get("/api/queues").json()["counts"]
    assert counts["listing-changed"] == 1 and counts["title-mismatch"] == 0
    skus = loaded.get("/api/sheet/skus", params={"queue": "listing-changed"}).json()["skus"]
    assert skus == ["SKU-1"]


def test_accepted_titles_survive_amazon_rewording_but_not_a_real_change(loaded):
    scrape(loaded, "SKU-1", "Totally Different Wooden Garden Trellis")           # low match
    assert loaded.get("/api/queues").json()["counts"]["title-mismatch"] == 1
    # the team says "this is the same item" by typing OK in the TITLE CHECK cell
    assert cells(loaded, ("SKU-1", "title_check", "OK"))["changed"] == 1
    assert loaded.get("/api/queues").json()["counts"]["title-mismatch"] == 0
    assert row(loaded, "SKU-1")["title_check"] == "Accepted by the team"
    # Amazon rewords the accepted title: still accepted, no new alert
    scrape(loaded, "SKU-1", "Wooden Garden Trellis, Totally Different Design")
    assert loaded.get("/api/queues").json()["counts"]["title-mismatch"] == 0
    # Amazon swaps in an unrelated product: the approval no longer applies
    scrape(loaded, "SKU-1", "Bluetooth Speaker Waterproof Portable Black")
    assert loaded.get("/api/queues").json()["counts"]["title-mismatch"] == 1
    # clearing the cell takes the approval back
    cells(loaded, ("SKU-1", "title_check", "OK"))
    cells(loaded, ("SKU-1", "title_check", ""))
    assert loaded.get("/api/queues").json()["counts"]["title-mismatch"] == 1
    assert cells(loaded, ("SKU-1", "title_check", "maybe"))["error_count"] == 1


def test_editing_the_name_or_amazon_title_rechecks(loaded):
    scrape(loaded, "SKU-1", "Acrylic Paint Set 12 Pack")
    assert row(loaded, "SKU-1")["title_score"] == 100
    cells(loaded, ("SKU-1", "name", "Zebra Striped Garden Gnome Statue"))
    assert row(loaded, "SKU-1")["title_score"] < 20
    cells(loaded, ("SKU-1", "title", "Zebra Striped Garden Gnome Statue Large"))
    assert row(loaded, "SKU-1")["title_score"] == 100


def test_synonym_setting_rejudges_every_title(loaded):
    cells(loaded, ("SKU-1", "name", "Vintage Ceramic Mug Floral Gift"))
    scrape(loaded, "SKU-1", "Retro Ceramic Cup Floral Gift Idea")
    before = row(loaded, "SKU-1")["title_score"]
    r = loaded.post("/api/scraper/settings", json={"title_synonyms": "mug, cup\nvintage, retro"}).json()
    assert r["ok"] and "mug, cup" in r["settings"]["title_synonyms"]
    assert row(loaded, "SKU-1")["title_score"] > before and row(loaded, "SKU-1")["title_score"] == 100
    loaded.post("/api/scraper/settings", json={"title_synonyms": ""})
    assert row(loaded, "SKU-1")["title_score"] < 60                                     # removing the rule takes the verdict back
    db.rebuild_title_model()                                                            # leave the shared model as it was


def test_recompute_keeps_an_accepted_title_when_amazon_only_reworded(loaded):
    scrape(loaded, "SKU-1", "Totally Different Wooden Garden Trellis")
    cells(loaded, ("SKU-1", "title_check", "ok"))
    with db.tx() as c:                                    # simulate a title that was reworded before this version existed
        c.execute("UPDATE products SET title='Wooden Garden Trellis Totally Different Style' WHERE sku='SKU-1'")
        db.recompute_titles(c)
    assert row(loaded, "SKU-1")["title_check"] == "Accepted by the team"
