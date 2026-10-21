"""Fill a throwaway data folder with demo products so the interface can be tried out.

    GT_DATA=demo-data python tests/seed_demo.py     (login: demo / demopass123)
"""
import csv
import io
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import auth, db, imports  # noqa: E402

random.seed(7)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
CATS = ["Home Decor", "Craft Supplies", "Party", "Kitchen", "Garden", "Office", "Toys", "Seasonal"]
WORDS = ["Acrylic", "Wooden", "Ceramic", "Glitter", "Felt", "Canvas", "Ribbon", "Candle", "Frame", "Paint", "Brush", "Beads", "Yarn", "Vase", "Wreath", "Sticker"]
AVAIL = ["In Stock", "In Stock", "In Stock", "In Stock", "Currently unavailable.", "Only 3 left in stock - order soon.", "Availability not found"]

out = io.StringIO()
w = csv.writer(out)
w.writerow(["ASIN", "SOURCE LINK", "SKU", "PRODUCT NAME", "CATEGORY", "SOURCE PRICE", "WEIGHT", "INVENTORY", "LISTED", "LISTING STATUS",
            "PRICE", "AVAILABILITY", "ESTIMATED DELIVERY", "UPC", "Last Optimization Run (Tapicker Output)", "PRODUCT SOLD 2025", "PRODUCT SOLD 2026"])
now = datetime.now()
for i in range(N):
    asin = f"B0{i:08d}"
    src = round(random.uniform(2, 60), 2)
    price = round(src * random.uniform(0.8, 1.25), 2)
    deliv = (now + timedelta(days=random.choice([2, 3, 4, 9, 14]))).strftime("Delivery %B %d")
    name = " ".join(random.sample(WORDS, 3)) + f" Set {random.choice([6, 12, 24, 50])} Pack"
    w.writerow([asin, f"https://www.amazon.com/dp/{asin}", f"GT-{i:05d}", name, random.choice(CATS) if random.random() > 0.03 else "",
                src, random.choice([3, 6, 10, 14, 20, 40, 70]) if random.random() > 0.03 else "", random.choice(["TRUE"] * 5 + ["FALSE"]), "TRUE",
                random.choice(["Live"] * 8 + ["Deactivated", "OOS"]), f"${price}", random.choice(AVAIL), deliv, f"{800000000000 + i}",
                (now - timedelta(hours=random.randint(1, 60))).strftime("%Y-%m-%d %H:%M:%S"), random.randint(0, 40), random.randint(0, 15)])
db.init()
print(imports.import_products("demo.csv", out.getvalue().encode(), "seed"))
if not auth.user_count():
    auth.add_user("demo", "Demo Admin", "demopass123", "admin")
print("login: demo / demopass123")
