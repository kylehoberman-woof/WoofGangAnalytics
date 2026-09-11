"""One-off diagnostic: dump every raw FranPOS customer record (unfiltered,
unprocessed) so we can search by name directly instead of trusting whatever
resolution logic customer_names.json/fetch_customer_phones.py apply.

Output: {store}/data/raw_customers_debug.json — list of raw records as
FranPOS returns them (FirstName, LastName, CustomerId, ParentCustomerId,
Phone, CellPhone, CustomFieldValues, etc.)

Usage:
    python3 scripts/dump_raw_customers.py
    python3 scripts/dump_raw_customers.py hicksville
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

out_file = data_dir / "raw_customers_debug.json"

ENDPOINTS = ["api/datadump/v1/customers/{days}/{page}/{from_date}/{location_id}"]

start_dt = datetime.strptime(store.start_date, "%Y-%m-%d")
end_dt = datetime.today()

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

print(f"Fetching ALL raw customers for {store_name} ({len(windows)} monthly windows)...")

all_records = {}
working_endpoint = None

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
                for item in items:
                    cid = item.get("CustomerId")
                    if cid is not None:
                        all_records[str(cid)] = item
                if page == 1 and items:
                    working_endpoint = endpoint_template
                print(f"  {from_date} +{days}d: {len(items)} items [page {page}/{total_pages}] (total so far: {len(all_records)})")
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
    json.dump(all_records, f, indent=2)

print(f"\nDone! {len(all_records)} raw customer records → {out_file}")

# Quick inline search for anything George/Ristic-ish so it shows in the log
# even before the file is pulled down.
hits = [
    v for v in all_records.values()
    if "george" in (v.get("FirstName") or "").lower()
    or "ristic" in (v.get("LastName") or "").lower()
    or "ristic" in (v.get("FirstName") or "").lower()
]
print(f"\nGeorge/Ristic matches ({len(hits)}):")
for h in hits:
    print(" ", json.dumps(h))
