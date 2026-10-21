"""Manual check (not part of pytest): build a database with the ORIGINAL server code, then open it with the new app.

    python tests/compat_check.py <path to old GreentekOptimizer folder>
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

old = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp(prefix="gt-compat-"))
import re

csv_text = re.search(r'SAMPLE_CSV = """(.*?)"""', Path(__file__).with_name("conftest.py").read_text(), re.S).group(1)
(tmp / "sample.csv").write_text(csv_text)

maker = f"""
import sys
sys.path.insert(0, r"{old}")
import server
print(server.import_products("s.csv", open(r"{tmp / 'sample.csv'}", "rb").read()))
server.add_user("rachel", "Rachel", "password123", "admin")
"""
env = {**os.environ, "GT_DB": str(tmp / "old.db"), "GT_NO_AUTO_BACKUP": "1"}
subprocess.run([sys.executable, "-c", maker], env=env, check=True)

root = str(Path(__file__).resolve().parent.parent)
env["GT_DATA"] = str(tmp)
checker = f"""
import sys
sys.path.insert(0, r"{root}")
from app import auth, db
db.init()
for r in db.conn().execute("select sku, status, fee, auto_price, price_num from products order by row_order"):
    print(dict(r))
print("old login still works:", auth.check_login("rachel", "password123") is not None)
"""
subprocess.run([sys.executable, "-c", checker], env=env, check=True)
