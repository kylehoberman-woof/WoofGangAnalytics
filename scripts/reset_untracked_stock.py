#!/usr/bin/env python3
"""
Reset untracked/bulk SKUs to 0 stock in FranPOS.
These are weighed/bulk items that can't be inventory-tracked via POS.
Run manually or add to daily workflow after confirming SKUs are correct.
"""
import httpx, json
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = open(Path(__file__).parent / "run.py").read().split('TOKEN = "')[1].split('"')[0]
BASE = "https://publicapi.franpos.com"
LOC = 203698

# SKUs to reset to 0 — bulk/weighed items that FranPOS can't track
UNTRACKED_SKUS = {
    "1002":    "Cookie Large",
    "1004":    "Cookie Small",
    "1005":    "Cookie Medium",
    "1006":    "Cookie XL",
    "TREAT01": "Bakery Bay Bulk Treats per ounce",
    "TREAT02": "Farmers Market Bulk",
    "2002":    "Bulk Treat (2002)",
    "82101":   "Bakery Item 82101",
    "81909":   "Bakery Item 81909",
    "000":     "Olive & Bacon Purse / Misc",
}

DRY_RUN = True  # Set to False to actually update FranPOS

# ── Reset ─────────────────────────────────────────────────────────────────────
print(f"{'DRY RUN — ' if DRY_RUN else ''}Resetting {len(UNTRACKED_SKUS)} untracked SKUs to 0...\n")

success, failed = [], []

for sku, name in UNTRACKED_SKUS.items():
    # Check current stock first
    r = httpx.get(f"{BASE}/api/getStockByProductSKU/{sku}/{LOC}",
                  params={"Token": TOKEN}, timeout=10)
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
        f"{BASE}/api/updateStockByProductSKU",
        params={"sku": sku, "stock": 0, "addToStock": False, "Token": TOKEN},
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
