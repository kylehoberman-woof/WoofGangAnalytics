import httpx, json, time, os
from pathlib import Path

TOKEN = os.environ["FRANPOS_TOKEN"]
BASE_URL = "https://publicapi.franpos.com"
LOCATION_ID = 203698
data_dir = Path(__file__).parent.parent / "port-washington" / "data"

all_data = json.load(open(data_dir / "all_data.json"))
skus = set(i.get("Sku","").strip() for i in all_data["order_items"] if len(i.get("Sku","").strip()) > 2)
stock = {}
for sku in sorted(skus):
    try:
        r = httpx.get(f"{BASE_URL}/api/getStockByProductSKU/{sku}/{LOCATION_ID}", params={"Token": TOKEN}, timeout=10)
        if r.status_code == 200:
            stock[sku] = float(r.text.strip())
        time.sleep(0.05)
    except: pass
json.dump(stock, open(data_dir / "stock_levels.json", "w"))
print(f"Fetched {len(stock)} stock levels")
