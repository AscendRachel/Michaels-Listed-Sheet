import ast
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_sheet import cells, row

ROOT = Path(__file__).resolve().parent.parent


def report(c, sku="SKU-1", **extra):
    """What a scraper PC sends for one product."""
    key = c.get("/api/scraper/key").json()["key"]
    h = {"X-Agent-Key": key}
    run = c.post("/api/agent/start", json={"agent_id": "pc1", "name": "PC-1"}, headers=h).json()["run_id"]
    c.post("/api/agent/claim", json={"agent_id": "pc1", "run_id": run, "n": 50}, headers=h)
    body = dict(agent_id="pc1", run_id=run, sku=sku, result="ok", price="$20.00", availability="In Stock", delivery="",
                run_at="2026-03-04 09:15:30", title="Acrylic Paint Set 12 Pack")
    body.update(extra)
    return c.post("/api/agent/result", json=body, headers=h).json()


def test_the_scraped_variation_is_stored_in_its_own_column(loaded):
    assert row(loaded, "SKU-1")["amazon_variation"] in (None, "")
    report(loaded, variation='Color: Beige/Size: 18" x 18" (Pack of 2)')
    assert row(loaded, "SKU-1")["amazon_variation"] == 'Color: Beige/Size: 18" x 18" (Pack of 2)'
    assert row(loaded, "SKU-1")["variation"] in (None, "")                   # the catalog's own VARIATION column is untouched


def test_old_scrapers_and_unreadable_pages_never_blank_the_stored_value(loaded):
    report(loaded, variation="Style: Ocean Animals")
    report(loaded)                                                           # an older scraper PC: no "variation" key at all
    assert row(loaded, "SKU-1")["amazon_variation"] == "Style: Ocean Animals"
    report(loaded, variation=None)                                           # a scraper that could not read the page
    assert row(loaded, "SKU-1")["amazon_variation"] == "Style: Ocean Animals"
    report(loaded, variation="")                                             # page read fine and has no variations: that is real
    assert row(loaded, "SKU-1")["amazon_variation"] == ""


def test_the_value_is_cleaned_and_limited(loaded):
    report(loaded, variation="Color:  Red  /Size: L (function(){ var x=1 })")
    assert row(loaded, "SKU-1")["amazon_variation"] == "Color: Red /Size: L"
    report(loaded, variation="x" * 1000)
    assert len(row(loaded, "SKU-1")["amazon_variation"]) == 300


def test_filter_sort_and_edit_like_any_column(loaded):
    report(loaded, "SKU-1", variation="Color: Beige")
    report(loaded, "SKU-2", variation="Style: Ocean Animals")
    f = lambda **kw: loaded.get("/api/sheet/skus", params=kw).json()["skus"]
    assert f(cf_amazon_variation="beige") == ["SKU-1"]
    assert f(cf_amazon_variation="(blank)") == ["SKU-3", "SKU-4"]
    assert f(sort="amazon_variation")[:2] == ["SKU-1", "SKU-2"] and f(sort="-amazon_variation")[0] == "SKU-2"
    assert cells(loaded, ("SKU-1", "amazon_variation", "Color: Ivory"))["changed"] == 1
    assert row(loaded, "SKU-1")["amazon_variation"] == "Color: Ivory"


def test_a_new_catalog_variation_and_the_scraped_one_sit_side_by_side(loaded):
    cells(loaded, ("SKU-1", "variation", "Color: Beige/Size: 18\" x 18\" (Pack of 2)"))
    report(loaded, variation='Color: Beige/Size: 20" x 20" (Pack of 2)')
    x = row(loaded, "SKU-1")
    assert x["variation"] != x["amazon_variation"] and x["variation"].startswith("Color: Beige")


@pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")
def test_the_scraper_script_is_valid_javascript():
    """A typo in the variation script would silently stop every scraper PC from reading variations."""
    tree = ast.parse((ROOT / "scraper" / "scraper_app.py").read_text(encoding="utf-8"))
    for name in ("VARIATION_JS", "IMAGE_JS"):
        js = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name)
        code = "const f = (" + js + ");\nif (typeof f !== 'function') throw new Error('not a function');"
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr}"

# ---- the main Amazon image
def test_the_amazon_main_image_is_stored_without_a_size_code(loaded):
    assert row(loaded, "SKU-1")["amazon_image"] in (None, "")
    report(loaded, image="https://m.media-amazon.com/images/I/81IhpPoiBmL._AC_SL1500_.jpg")
    assert row(loaded, "SKU-1")["amazon_image"] == "https://m.media-amazon.com/images/I/81IhpPoiBmL.jpg"
    report(loaded, image="https://m.media-amazon.com/images/I/81R3-9CjjPL._AC_SY300_SX300_QL70_FMwebp_.jpg")
    assert row(loaded, "SKU-1")["amazon_image"] == "https://m.media-amazon.com/images/I/81R3-9CjjPL.jpg"      # ids may contain "-"


def test_a_missing_or_unusable_image_never_blanks_the_stored_one(loaded):
    report(loaded, image="https://m.media-amazon.com/images/I/81IhpPoiBmL._AC_SL1500_.jpg")
    keep = "https://m.media-amazon.com/images/I/81IhpPoiBmL.jpg"
    report(loaded)                                                           # an older scraper PC
    assert row(loaded, "SKU-1")["amazon_image"] == keep
    report(loaded, image="")                                                 # nothing found on the page
    assert row(loaded, "SKU-1")["amazon_image"] == keep
    report(loaded, image="data:image/gif;base64,R0lGOD")                     # a placeholder, not a picture
    assert row(loaded, "SKU-1")["amazon_image"] == keep
    report(loaded, image="http://example.com/x.jpg")                         # not https: ignored
    assert row(loaded, "SKU-1")["amazon_image"] == keep


def test_image_filter_and_the_viewer_data(loaded):
    report(loaded, "SKU-1", image="https://m.media-amazon.com/images/I/81IhpPoiBmL._AC_SL1500_.jpg")
    f = lambda **kw: loaded.get("/api/sheet/skus", params=kw).json()["skus"]
    assert f(cf_amz_img="TRUE") == ["SKU-1"] and f(cf_amz_img="FALSE") == ["SKU-2", "SKU-3", "SKU-4"]
    d = loaded.get("/api/products/SKU-1").json()["product"]                  # the details panel gets it too
    assert d["amazon_image"].endswith("81IhpPoiBmL.jpg")
    assert cells(loaded, ("SKU-1", "amazon_image", "https://m.media-amazon.com/images/I/AAAA.jpg"))["changed"] == 1


def test_amazon_image_address_shapes():
    from app.rules import amazon_image_base as base
    ok = "https://m.media-amazon.com/images/I/41g48gp-ufL.jpg"
    assert base("https://m.media-amazon.com/images/I/41g48gp-ufL._AC_US100_.jpg") == ok
    assert base("https://m.media-amazon.com/images/I/41g48gp-ufL.jpg?x=1") == ok
    assert base("https://m.media-amazon.com/images/I/61abc.PNG") == "https://m.media-amazon.com/images/I/61abc.png"
    assert base("") == "" and base(None) == "" and base("data:image/png;base64,AA") == ""

