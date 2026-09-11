"""Fetch customer phone numbers from FranPOS's customers datadump endpoint —
and, as a side effect, backfill any customer missing from customer_names.json
entirely (a dog registered after whatever last regenerated that file, e.g. a
new pet added after an older one passed away, would otherwise be invisible
to every tool in this system: Lapse Calls, Pet Dashboard, Booking Anomalies).

customer_names.json (the pet_cid -> {pet, owner} registry other scripts read)
has never carried phone numbers — nothing in this pipeline currently
regenerates that file, and whatever originally built it is gone from the
repo. FranPOS's own customer records do have real phone numbers, just under
"CellPhone" (the plain "Phone" field is consistently empty), and a pet
account has "ParentCustomerId" pointing at its owner's account — the owner's
record is a more reliable place to find a populated phone than the pet's
own record, and is how a pet's display name ("George") gets resolved to an
owner name ("Mirjana Ristic") in the first place.

Output: {store}/data/customer_phones.json
  { "<customer_id>": {"phone": "5165551234", "parent_id": 419012345 or null} }

Also updates {store}/data/customer_names.json — additive only, never
touches or overwrites an existing entry, only adds pet_cids that aren't in
it yet. A record with ParentCustomerId set is a pet account (its own
FirstName is the pet's name, owner resolved from the parent record); one
without is treated as a standalone/owner account.

fetch_pet_visits.py resolves a pet's phone as: the pet's own record's phone,
falling back to its parent (owner) record's phone via parent_id.

Usage:
    python3 scripts/fetch_customer_phones.py
    python3 scripts/fetch_customer_phones.py hicksville
"""

import httpx, json, time, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL, ET_BUFFER_DAYS

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
token = store.token
location_id = store.location_id
data_dir = store.data_dir

out_file = data_dir / "customer_phones.json"

# Load existing cache — customer records rarely change once created, so this
# is a straightforward merge/overwrite-by-id, not append-only like tickets.
cached = {}
if out_file.exists():
    with open(out_file) as f:
        cached = json.load(f)
    print(f"Existing cache: {len(cached)} customers")

ENDPOINTS = [
    "api/datadump/v1/customers/{days}/{page}/{from_date}/{location_id}",
]

start_dt = datetime.strptime(store.start_date, "%Y-%m-%d")
end_dt = datetime.today()

# Monthly windows, same pattern as fetch_appointments.py.
windows = []
current = start_dt.replace(day=1)
while current <= end_dt:
    if current.month == 12:
        next_month = current.replace(year=current.year + 1, month=1, day=1)
    else:
        next_month = current.replace(month=current.month + 1, day=1)
    window_end = min(next_month - timedelta(days=1), end_dt)
    window_start = max(current, start_dt)
    windows.append((window_start, window_end))
    current = next_month

names_file = data_dir / "customer_names.json"
existing_names = {}
if names_file.exists():
    with open(names_file) as f:
        existing_names = json.load(f)
    print(f"Existing customer_names.json: {len(existing_names)} entries")

print(f"Fetching customers for {store_name} ({len(windows)} monthly windows)...")

new_count = 0
working_endpoint = None
raw_customers = {}  # cid -> full FranPOS record, for the customer_names.json backfill below

for window_start, window_end in windows:
    fetch_start = window_start - timedelta(days=ET_BUFFER_DAYS)
    fetch_end = window_end + timedelta(days=ET_BUFFER_DAYS)
    days = (fetch_end - fetch_start).days + 1
    from_date = fetch_start.strftime("%Y-%m-%d")

    page = 0
    total_pages = 1

    while page < total_pages:
        endpoints_to_try = (
            [working_endpoint] + [e for e in ENDPOINTS if e != working_endpoint]
            if working_endpoint else ENDPOINTS
        )

        fetched = False
        for endpoint_template in endpoints_to_try:
            endpoint = endpoint_template.format(
                days=days, page=page, from_date=from_date, location_id=location_id
            )
            try:
                r = httpx.get(f"{BASE_URL}/{endpoint}", params={"Token": token}, timeout=45)
                if r.status_code in (404, 403):
                    continue

                r.raise_for_status()
                result = r.json()
                items = result.get("data", []) if isinstance(result, dict) else (result or [])
                total_pages = result.get("pages", 1) if isinstance(result, dict) else 1

                added = 0
                for item in items:
                    cid = item.get("CustomerId")
                    if cid is None:
                        continue
                    key = str(cid)
                    raw_customers[key] = item
                    phone = (item.get("CellPhone") or item.get("Phone") or "").strip()
                    if key not in cached or (phone and not cached[key].get("phone")):
                        cached[key] = {
                            "phone": phone,
                            "parent_id": item.get("ParentCustomerId"),
                        }
                        added += 1

                if page == 1 and items:
                    working_endpoint = endpoint_template
                print(f"  {from_date} +{days}d: {len(items)} items ({added} new/updated) [page {page}/{total_pages}]")

                new_count += added
                fetched = True
                break

            except Exception as e:
                if endpoint_template == endpoints_to_try[-1]:
                    print(f"  {from_date}: ERROR {e}")
                continue

        if not fetched:
            working_endpoint = None
            break

        page += 1
        time.sleep(0.3)

with open(out_file, "w") as f:
    json.dump(cached, f, indent=2)

with_phone = sum(1 for v in cached.values() if v.get("phone"))
print(f"\nDone! {new_count} new/updated, {len(cached)} total customers cached, {with_phone} with a phone number → {out_file}")

# ── Backfill customer_names.json with anyone missing entirely ────────────────
# Additive only for entries that already have a value — an existing pet/owner
# pairing is never overwritten, since customer_names.json's original builder
# may have used logic this doesn't fully replicate (e.g. the "Bella, Cannoli"
# combined-multi-pet convention). The one exception: an existing entry with a
# blank owner ("" — unambiguously broken, not a plausible different mapping)
# gets repaired if a parent now resolves it. This is exactly what happened to
# George (Mirjana Ristic's dog): a prior run added him with pet="George",
# owner="" because his parent record wasn't in that run's fetched batch yet.
backfilled = 0
repaired = 0
new_pets = []
for cid, item in raw_customers.items():
    first = (item.get("FirstName") or "").strip()
    last = (item.get("LastName") or "").strip()
    parent_id = item.get("ParentCustomerId")

    if cid in existing_names:
        entry = existing_names[cid]
        if parent_id and not entry.get("owner"):
            parent = raw_customers.get(str(parent_id))
            owner = f"{parent.get('FirstName','')} {parent.get('LastName','')}".strip() if parent else ""
            if owner:
                entry["owner"] = owner
                repaired += 1
        continue

    if parent_id:
        parent = raw_customers.get(str(parent_id))
        owner = f"{parent.get('FirstName','')} {parent.get('LastName','')}".strip() if parent else ""
        existing_names[cid] = {"pet": first, "owner": owner}
        if first and owner:
            new_pets.append(f"{first} ({owner})")
    else:
        owner = f"{first} {last}".strip()
        if owner:
            existing_names[cid] = {"pet": "", "owner": owner}
    backfilled += 1

if backfilled or repaired:
    with open(names_file, "w") as f:
        json.dump(existing_names, f, indent=2)
    if backfilled:
        print(f"Backfilled {backfilled} customers missing from customer_names.json ({len(new_pets)} pet accounts)")
        if new_pets:
            print("  New pets:", ", ".join(new_pets[:20]) + (f" ... +{len(new_pets)-20} more" if len(new_pets) > 20 else ""))
    if repaired:
        print(f"Repaired {repaired} existing entries with a blank owner (parent record now resolved)")
else:
    print("No new customers to backfill into customer_names.json")
