"""Fetch per-pet visit history from FranPOS customer history API.

For each pet account (no LastName in FranPOS), calls:
  GET api/customers/history/{pet_cid}/json

Returns: Date, Stylist, Items (Service/BreedGroup/Size), SalesPerson, Store

Smart incremental: skips pets with no visit in 90+ days to keep API calls manageable.
On first run fetches all pets. Subsequent runs only re-fetch active pets.

Output: {store}/data/pet_visits.json
  [
    {
      "pet_cid": 420161601,
      "pet_name": "Cookie",
      "owner_name": "Jennifer Valencia",
      "owner_cid": 123456,
      "owner_phone": "5168489383",
      "visits": [
        {"date": "2026-07-22", "stylist": "Julia B", "service": "Lux Bath",
         "breed_group": "General", "size": "SM", "items_raw": "Lux Bath / General / SM",
         "salesperson": "Kayla Moses", "store": "Woof Gang Hicksville, NY (#265)"}
      ],
      "total_visits": 3,
      "last_visit": "2026-07-22",
      "last_fetched": "2026-08-24"
    }
  ]

Usage:
    python3 scripts/fetch_pet_visits.py
    python3 scripts/fetch_pet_visits.py hicksville
    python3 scripts/fetch_pet_visits.py port-washington --full   # force full re-fetch
"""

import httpx, json, sys, time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
force_full = "--full" in sys.argv
store = get_store(store_name)
token = store.token
location_id = store.location_id
data_dir = store.data_dir

out_file = data_dir / "pet_visits.json"
names_file = data_dir / "customer_names.json"
ACTIVE_CUTOFF_DAYS = 90  # skip pets with no visit in this many days
TODAY = date.today().isoformat()

# ── Load customer names (owner + pet accounts) ───────────────────────────────
if not names_file.exists():
    print(f"ERROR: {names_file} not found — run fetch data first")
    sys.exit(1)

with open(names_file) as f:
    names = json.load(f)

# Build owner phone → owner info map for linking pets to owners
phone_to_owner = {}
for cid, info in names.items():
    if info.get("owner") and "," in info.get("owner", ""):
        # Owner accounts have LastName (stored as "Last, First" or just full name)
        phone_to_owner[info.get("phone", "")] = {"name": info["owner"], "cid": cid}

# Pet accounts: have pet name set, no last name (owner is set to the owner's name)
pet_accounts = []
for cid, info in names.items():
    if info.get("pet") and info.get("owner"):
        pet_accounts.append({
            "pet_cid": int(cid),
            "pet_name": info["pet"].strip(),
            "owner_name": info["owner"],
            "owner_phone": info.get("phone", ""),
        })

print(f"Found {len(pet_accounts)} pet accounts for {store_name}")

# ── Load existing cache ───────────────────────────────────────────────────────
existing = {}
if out_file.exists():
    with open(out_file) as f:
        cached_list = json.load(f)
    for rec in cached_list:
        existing[rec["pet_cid"]] = rec
    print(f"Existing cache: {len(existing)} pets")

cutoff = (date.today() - timedelta(days=ACTIVE_CUTOFF_DAYS)).isoformat()

# ── Determine which pets to fetch ────────────────────────────────────────────
to_fetch = []
skipped = 0
for pet in pet_accounts:
    cid = pet["pet_cid"]
    cached = existing.get(cid)
    if force_full or not cached:
        to_fetch.append(pet)
        continue
    last_visit = cached.get("last_visit", "")
    last_fetched = cached.get("last_fetched", "")
    # Re-fetch if: active pet (recent visit) OR fetched long ago
    if last_visit >= cutoff or last_fetched < (date.today() - timedelta(days=7)).isoformat():
        to_fetch.append(pet)
    else:
        skipped += 1

print(f"Fetching {len(to_fetch)} pets ({skipped} skipped — inactive/fresh cache)...")

def parse_items(items_raw):
    """Parse 'Lux Bath / General / SM' → {service, breed_group, size}."""
    parts = [p.strip() for p in items_raw.split("/")]
    return {
        "service": parts[0] if len(parts) > 0 else items_raw,
        "breed_group": parts[1] if len(parts) > 1 else "",
        "size": parts[2] if len(parts) > 2 else "",
    }

errors = 0
fetched = 0
for i, pet in enumerate(to_fetch):
    cid = pet["pet_cid"]
    try:
        r = httpx.get(
            f"{BASE_URL}/api/customers/history/{cid}/json",
            params={"Token": token},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            visits_raw = data.get("data", [])
            visits = []
            for v in visits_raw:
                parsed = parse_items(v.get("Items", ""))
                dt = v.get("Date", "")
                visits.append({
                    "date": dt[:10] if dt else "",
                    "datetime": dt,
                    "stylist": v.get("Stylist", ""),
                    "salesperson": v.get("SalesPerson", ""),
                    "service": parsed["service"],
                    "breed_group": parsed["breed_group"],
                    "size": parsed["size"],
                    "items_raw": v.get("Items", ""),
                    "store": v.get("Store", ""),
                })
            # Sort by date desc
            visits.sort(key=lambda x: x["date"], reverse=True)
            rec = {
                **pet,
                "visits": visits,
                "total_visits": data.get("totalVisit", len(visits)),
                "last_visit": visits[0]["date"] if visits else "",
                "last_fetched": TODAY,
            }
            existing[cid] = rec
            fetched += 1
        elif r.status_code == 404:
            # Pet has no history — still cache it
            if cid not in existing:
                existing[cid] = {**pet, "visits": [], "total_visits": 0,
                                  "last_visit": "", "last_fetched": TODAY}
        else:
            errors += 1
            if errors <= 5:
                print(f"  {pet['pet_name']} ({cid}): {r.status_code}")
    except Exception as e:
        errors += 1
        if errors <= 5:
            print(f"  {pet['pet_name']} ({cid}): ERROR {e}")

    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(to_fetch)} fetched...")
    time.sleep(0.15)  # gentle rate limiting

# ── Save ──────────────────────────────────────────────────────────────────────
all_records = list(existing.values())
all_records.sort(key=lambda x: x.get("last_visit", ""), reverse=True)

with open(out_file, "w") as f:
    json.dump(all_records, f, indent=2)

pets_with_visits = sum(1 for r in all_records if r.get("last_visit"))
print(f"\nDone! {fetched} fetched, {errors} errors, {skipped} skipped")
print(f"Total: {len(all_records)} pets, {pets_with_visits} with visit history → {out_file}")
