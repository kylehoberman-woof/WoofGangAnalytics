#!/usr/bin/env python3
"""
Reset untracked/bulk SKUs to 0 stock in FranPOS.
These are weighed/bulk items that can't be inventory-tracked via POS.
Run manually or add to daily workflow after confirming SKUs are correct.
"""
import httpx, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import get_store, BASE_URL, UNTRACKED_SKUS

store = get_store("port-washington")

DRY_RUN = True  # Set to False to actually update FranPOS

# ── Reset ─────────────────────────────────────────────────────────────────────
print(f"{'DRY RUN — ' if DRY_RUN else ''}Resetting {len(UNTRACKED_SKUS)} untracked SKUs to 0...\n")

success, failed = [], []

for sku, name in UNTRACKED_SKUS.items():
    # Check current stock first
    r = httpx.get(f"{BASE_URL}/api/getStockByProductSKU/{sku}/{store.location_id}",
                  params={"Token": store.token}, timeout=10)
    current = float(r.text.strip()) if r.status_code == 200 else None
    current_str = f"{current:.1f}" if current is not None else "unknown"

    if DRY_RUN:
        print(f"  [DRY RUN] {name:40} SKU:{sku:8} current:{current_str:>10} → would reset to 0")
        success.append(sku)
        continue

    if current == 0.0:
        print(f"  [SKIP]    {name:40} SKU:{sku:8} already at 0")
        success.append(sku)
        continue

    # Reset to 0
    r2 = httpx.post(
        f"{BASE_URL}/api/updateStockByProductSKU",
        params={"sku": sku, "stock": 0, "addToStock": False, "Token": store.token},
        timeout=10
    )
    if r2.status_code == 200:
        print(f"  [RESET]   {name:40} SKU:{sku:8} {current_str} → 0  ✓")
        success.append(sku)
    else:
        print(f"  [ERROR]   {name:40} SKU:{sku:8} status={r2.status_code} {r2.text[:80]}")
        failed.append(sku)

print(f"\n{'DRY RUN complete' if DRY_RUN else 'Done'}. {len(success)} OK, {len(failed)} failed.")
if DRY_RUN:
    print("\nSet DRY_RUN = False and re-run to actually update FranPOS.")
