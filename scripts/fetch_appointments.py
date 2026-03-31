"""Fetch bookings/appointments from FranPOS API.

Appointments are booked under the PET's CustomerID (not the owner's),
which allows us to map transactions to individual pets for multi-dog households.

Usage:
    python3 scripts/fetch_appointments.py              # Port Washington
    python3 scripts/fetch_appointments.py hicksville   # Hicksville
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

out_file = data_dir / "appointments.json"

# Load existing cache for merge
cached = []
seen = set()
if out_file.exists():
    with open(out_file) as f:
        cached = json.load(f)
    for a in cached:
        uid = a.get("UniqueID")
        if uid is not None:
            seen.add(uid)
    print(f"Existing cache: {len(cached)} appointments")

# Fetch bookings using the datadump pattern (same as orderitems/orders)
# Try v2 first, fall back to v1
ENDPOINTS = [
    "api/datadump/v2/bookings/{days}/{page}/{from_date}/{location_id}",
    "api/datadump/v1/bookings/{days}/{page}/{from_date}/{location_id}",
]

start_dt = datetime.strptime(store.start_date, "%Y-%m-%d")
end_dt = datetime.today()

# Build monthly windows (same pattern as api_client.extract_paginated)
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

print(f"Fetching appointments for {store_name} ({len(windows)} monthly windows)...")

new_appointments = []
working_endpoint = None

for window_start, window_end in windows:
    fetch_start = window_start - timedelta(days=ET_BUFFER_DAYS)
    fetch_end = window_end + timedelta(days=ET_BUFFER_DAYS)
    days = (fetch_end - fetch_start).days + 1
    from_date = fetch_start.strftime("%Y-%m-%d")

    page = 0
    total_pages = 1

    while page < total_pages:
        # Try endpoints until one works
        endpoints_to_try = [working_endpoint] if working_endpoint else ENDPOINTS

        fetched = False
        for endpoint_template in endpoints_to_try:
            endpoint = endpoint_template.format(
                days=days, page=page, from_date=from_date, location_id=location_id
            )
            try:
                r = httpx.get(
                    f"{BASE_URL}/{endpoint}",
                    params={"Token": token},
                    timeout=45,
                )
                if r.status_code == 404:
                    continue  # Try next endpoint
                if r.status_code == 403:
                    print(f"  403 Access Denied on {endpoint_template.split('/')[2]} — skipping")
                    continue
                r.raise_for_status()

                result = r.json()
                if isinstance(result, dict):
                    items = result.get("data", [])
                    total_pages = result.get("pages", 1)
                elif isinstance(result, list):
                    items = result
                    total_pages = 1
                else:
                    items = []

                added = 0
                for item in items:
                    uid = item.get("UniqueID")
                    if uid is not None and uid not in seen:
                        seen.add(uid)
                        new_appointments.append(item)
                        added += 1

                if page == 1 and items:
                    working_endpoint = endpoint_template
                    print(f"  {from_date} +{days}d: {len(items)} items ({added} new) [page {page}/{total_pages}]")
                elif items:
                    print(f"    page {page}/{total_pages}: {len(items)} items ({added} new)")

                fetched = True
                break  # Success, don't try other endpoints

            except Exception as e:
                if endpoint_template == endpoints_to_try[-1]:
                    print(f"  {from_date}: ERROR {e}")
                continue

        if not fetched:
            if not working_endpoint:
                # No endpoint works — try the Bookings endpoint directly
                try:
                    r = httpx.get(
                        f"{BASE_URL}/api/Bookings",
                        params={"Token": token, "startDate": from_date,
                                "endDate": window_end.strftime("%Y-%m-%d"),
                                "locationId": location_id},
                        timeout=45,
                    )
                    if r.status_code == 200:
                        items = r.json()
                        if isinstance(items, dict):
                            items = items.get("data", [])
                        added = 0
                        for item in items:
                            uid = item.get("UniqueID")
                            if uid is not None and uid not in seen:
                                seen.add(uid)
                                new_appointments.append(item)
                                added += 1
                        print(f"  {from_date}: {len(items)} items ({added} new) [via /api/Bookings]")
                        working_endpoint = "API_BOOKINGS"
                    else:
                        print(f"  {from_date}: /api/Bookings returned {r.status_code}")
                except Exception as e:
                    print(f"  {from_date}: /api/Bookings ERROR {e}")
            break  # No working endpoint found for this window

        page += 1
        time.sleep(0.3)

# Merge: keep all cached + new
all_appointments = cached + new_appointments

# Save
with open(out_file, "w") as f:
    json.dump(all_appointments, f, indent=2)

print(f"\nDone! {len(new_appointments)} new + {len(cached)} cached = {len(all_appointments)} total → {out_file}")
