import httpx, json, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import get_store, BASE_URL

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
data_dir = store.data_dir

all_data = json.load(open(data_dir / "all_data.json"))
skus = set(i.get("Sku","").strip() for i in all_data["order_items"] if len(i.get("Sku","").strip()) > 2)
stock = {}
for sku in sorted(skus):
    try:
        r = httpx.get(f"{BASE_URL}/api/getStockByProductSKU/{sku}/{store.location_id}",
                      params={"Token": store.token}, timeout=10)
        if r.status_code == 200:
            stock[sku] = float(r.text.strip())
        time.sleep(0.05)
    except: pass
json.dump(stock, open(data_dir / "stock_levels.json", "w"))
print(f"Fetched {len(stock)} stock levels")
