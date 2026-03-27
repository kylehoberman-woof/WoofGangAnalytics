"""Fetch real brand names from FranPOS product detail API.

For each unique retail SKU, calls getProductServiceBySku to get the BrandName field.
Caches results in sku_brands.json so we don't re-fetch known SKUs.
"""
import httpx, json, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL

store = get_store("port-washington")
data_dir = store.data_dir
token = store.token
location_id = store.location_id

# Load existing cache
cache_path = data_dir / "sku_brands.json"
if cache_path.exists():
    with open(cache_path) as f:
        cache = json.load(f)
else:
    cache = {}

# Get all unique SKUs from order data
all_data = json.load(open(data_dir / "all_data.json"))
skus = set()
for item in all_data["order_items"]:
    sku = str(item.get("Sku", "")).strip()
    if len(sku) > 2:
        skus.add(sku)

# Filter to SKUs not yet in cache
new_skus = [s for s in sorted(skus) if s not in cache]
print(f"Total SKUs: {len(skus)} | Cached: {len(cache)} | To fetch: {len(new_skus)}")

# Fetch brand for each new SKU
fetched = 0
errors = 0
for i, sku in enumerate(new_skus):
    try:
        r = httpx.get(
            f"{BASE_URL}/api/getProductServiceBySku/{sku}/{location_id}",
            params={"Token": token},
            timeout=10,
        )
        if r.status_code == 200:
            prod = r.json()
            if isinstance(prod, dict):
                brand = prod.get("BrandName") or ""
                category = prod.get("CategoryBreadCrumb") or ""
                cache[sku] = {
                    "brand": brand,
                    "category": category,
                    "manufacturer_id": prod.get("ManufacturerId"),
                    "brand_id": prod.get("BrandId"),
                }
                fetched += 1
            else:
                cache[sku] = {"brand": "", "category": ""}
        else:
            cache[sku] = {"brand": "", "category": ""}
            errors += 1
    except Exception as e:
        cache[sku] = {"brand": "", "category": ""}
        errors += 1

    # Progress every 50
    if (i + 1) % 50 == 0:
        print(f"  Progress: {i+1}/{len(new_skus)} (fetched: {fetched}, errors: {errors})")

    # Save cache periodically
    if (i + 1) % 100 == 0:
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2)

    time.sleep(0.05)  # Rate limit

# Final save
with open(cache_path, "w") as f:
    json.dump(cache, f, indent=2)

brands_found = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("brand"))
print(f"Done! Fetched {fetched} new, {errors} errors. Total cached: {len(cache)} ({brands_found} with brands)")
