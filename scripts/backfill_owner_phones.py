"""One-off local backfill: fill in owner_phone on existing pet_visits.json
records from the already-fetched customer_phones.json, with no FranPOS API
calls. The incremental refetch in fetch_pet_visits.py only re-derives
owner_phone for pets whose cache is 7+ days stale or recently active, so an
inactive pet cached shortly before the phone feature shipped can sit with a
stale blank owner_phone for up to 7 days even though the phone data already
exists locally. This closes that gap immediately.

Usage:
    python3 scripts/backfill_owner_phones.py
    python3 scripts/backfill_owner_phones.py hicksville
"""

import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
data_dir = store.data_dir

visits_file = data_dir / "pet_visits.json"
phones_file = data_dir / "customer_phones.json"

with open(phones_file) as f:
    phones = json.load(f)


def resolve_phone(cid):
    rec = phones.get(str(cid))
    if not rec:
        return ""
    if rec.get("phone"):
        return rec["phone"]
    parent_id = rec.get("parent_id")
    if parent_id:
        parent = phones.get(str(parent_id))
        if parent and parent.get("phone"):
            return parent["phone"]
    return ""


with open(visits_file) as f:
    records = json.load(f)

filled = 0
for rec in records:
    if rec.get("owner_phone"):
        continue
    phone = resolve_phone(rec.get("pet_cid"))
    if phone:
        rec["owner_phone"] = phone
        filled += 1

with open(visits_file, "w") as f:
    json.dump(records, f, indent=2)

print(f"{store_name}: filled {filled} blank owner_phone fields from existing customer_phones.json")
