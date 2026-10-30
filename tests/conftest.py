import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp(prefix="gt-test-")
os.environ["GT_DATA"] = _tmp
os.environ["GT_DB"] = str(Path(_tmp) / "test.db")
os.environ["GT_NO_AUTO_BACKUP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402

SAMPLE_CSV = """ASIN,SOURCE LINK,SKU,PRODUCT NAME,CATEGORY,SOURCE PRICE,WEIGHT,INVENTORY,LISTED,LISTING STATUS,PRICE,AVAILABILITY,ESTIMATED DELIVERY,UPC,Last Optimization Run (Tapicker Output),SKU NUMBER
B000000001,https://www.amazon.com/dp/B000000001,SKU-1,Acrylic Paint Set 12 Pack,Craft,10,16,TRUE,TRUE,Live,$12.50,In Stock,Arrives Tuesday,111,2026-01-05 10:00:00,430503183213510657
B000000002,https://www.amazon.com/dp/B000000002?th=1,SKU-2,Wooden Frame 8 inch,Home,5,4,TRUE,TRUE,Live,$3.00,Currently unavailable.,,222,2026-01-05 10:00:00,
,https://www.amazon.com/dp/B000000003,SKU-3,No Weight Item,,7,,FALSE,TRUE,Live,$7.00,In Stock,,333,2026-01-05 10:00:00,
B000000004,https://example.com/x,SKU-4,Not Amazon,Craft,3,8,TRUE,FALSE,,,,,,,
"""


@pytest.fixture(scope="session", autouse=True)
def _init():
    db.init()
    yield


@pytest.fixture()
def clean():
    """Empty every table between tests."""
    c = db.conn()
    for t in ("products", "history", "runs", "tasks", "agents", "imports", "michaels_imports", "activity",
              "saved_views", "sessions", "users", "settings", "column_locks", "worklists", "brand_logo", "custom_columns", "versions"):
        c.execute(f"DELETE FROM {t}")
    auth.FAILED.clear()
    from app import protect
    protect.seed()                          # every test starts with the default (Michaels) protections
    yield


@pytest.fixture()
def admin(clean):
    auth.add_user("boss", "The Boss", "password123", "admin")
    cl = TestClient(app)
    assert cl.post("/api/login", json={"username": "boss", "password": "password123"}).status_code == 200
    return cl


@pytest.fixture()
def loaded(admin):
    r = admin.post("/api/import", content=SAMPLE_CSV.encode(), headers={"X-Filename": "sheet.csv"})
    assert r.status_code == 200, r.text
    return admin
