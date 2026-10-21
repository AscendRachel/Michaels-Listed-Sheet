"""One-off repair: re-import the Listed Sheet (restores Michaels columns) and drop a bogus Michaels import record.
    GT_DATA=<data folder> python tests/repair_demo.py "<sheet.csv>"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db, imports  # noqa: E402

db.init()
path = Path(sys.argv[1])
print(imports.import_products(path.name, path.read_bytes(), "Claude (repair)"))
with db.tx() as c:
    c.execute("DELETE FROM michaels_imports WHERE filename='dl.csv' AND user='Demo Admin'")
    db.log_activity(c, "Claude (repair)", "repaired data", "",
                    "A test upload (dl.csv) wrongly cleared Michaels price/qty/SKU number; restored by re-importing the Listed Sheet and removing that test import record.")
r = db.conn().execute("""SELECT COUNT(*) total, SUM(michaels_price IS NOT NULL) with_price, SUM(michaels_qty IS NOT NULL) with_qty,
    SUM(COALESCE(michaels_sku_number,'')<>'') with_skunum FROM products WHERE active=1""").fetchone()
print(dict(r), "michaels_imports rows:", db.conn().execute("SELECT COUNT(*) FROM michaels_imports").fetchone()[0])
