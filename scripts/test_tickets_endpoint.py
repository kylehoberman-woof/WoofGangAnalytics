"""Test FranPOS tickets + booking endpoints to discover available data.

Run from GitHub Actions (IP restricted locally):
    python3 scripts/test_tickets_endpoint.py
"""

import httpx, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL

store = get_store("port-washington")
token = store.token
location_id = store.location_id

# ═══════════════════════════════════════════════════════════════
# 1. TICKETS — try both GET and POST, v1 and v2
# ═══════════════════════════════════════════════════════════════
for method in ['GET', 'POST']:
    for endpoint in ['api/report/v2/tickets', 'api/report/v1/tickets']:
        print(f"\n{'='*60}")
        print(f"{method} {endpoint}")
        print(f"{'='*60}")
        try:
            params = {"Token": token, "startDate": "2026-03-28", "endDate": "2026-03-28",
                      "locationId": location_id, "pageIndex": 0, "pageSize": 3}
            if method == 'GET':
                r = httpx.get(f"{BASE_URL}/{endpoint}", params=params, timeout=30)
            else:
                r = httpx.post(f"{BASE_URL}/{endpoint}", params={"Token": token},
                               json={"startDate": "2026-03-28T00:00:00", "endDate": "2026-03-28T23:59:59",
                                     "pageIndex": 0, "pageSize": 3, "locationId": location_id}, timeout=30)
            print(f"  Status: {r.status_code}")
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    data = r.json()
                    if isinstance(data, list) and data:
                        print(f"  Array: {len(data)} items")
                        print(f"  Keys: {sorted(data[0].keys())}")
                        print(f"\n  FIRST RECORD:\n{json.dumps(data[0], indent=2)[:1500]}")
                    elif isinstance(data, dict):
                        print(f"  Dict keys: {sorted(data.keys())}")
                        for key in ['data', 'Data', 'tickets', 'Tickets', 'Items']:
                            if key in data and isinstance(data[key], list) and data[key]:
                                print(f"  Items in '{key}': {len(data[key])}")
                                print(f"  Keys: {sorted(data[key][0].keys())}")
                                print(f"\n  FIRST RECORD:\n{json.dumps(data[key][0], indent=2)[:1500]}")
                                break
                        else:
                            print(json.dumps(data, indent=2)[:1000])
                    else:
                        print(f"  Empty or unexpected: {type(data)}")
                else:
                    print(f"  Content-Type: {ct}")
                    print(f"  Body[:500]: {r.text[:500]}")
                break  # Found working method, skip other
            else:
                print(f"  Body: {r.text[:200]}")
        except Exception as e:
            print(f"  ERROR: {e}")

# ═══════════════════════════════════════════════════════════════
# 2. BOOKING SERVICES — get service catalog (service ID → name)
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"GET api/booking/getservices?locationId={location_id}")
print(f"{'='*60}")
try:
    r = httpx.get(f"{BASE_URL}/api/booking/getservices",
                  params={"Token": token, "locationId": location_id}, timeout=30)
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list):
            print(f"  {len(data)} services found")
            for s in data[:10]:
                print(f"    ID={s.get('Id', s.get('ServiceId','?'))}  {s.get('Name', s.get('ServiceName','?'))}  ${s.get('Price',0)}")
        elif isinstance(data, dict):
            print(f"  Keys: {sorted(data.keys())}")
            items = data.get('data', data.get('Data', data.get('Services', [])))
            if items:
                print(f"  {len(items)} services")
                for s in items[:10]:
                    print(f"    {json.dumps(s)[:200]}")
            else:
                print(f"  {json.dumps(data, indent=2)[:500]}")
    else:
        print(f"  Body: {r.text[:200]}")
except Exception as e:
    print(f"  ERROR: {e}")

# ═══════════════════════════════════════════════════════════════
# 3. Try getBookings-style endpoints
# ═══════════════════════════════════════════════════════════════
for endpoint in [
    f"api/booking/getserviceproviders/26649769?locationId={location_id}",
    f"api/booking/searchservicetime/2026-04-01/26649769/0?locationId={location_id}",
]:
    print(f"\n{'='*60}")
    print(f"GET {endpoint}")
    print(f"{'='*60}")
    try:
        r = httpx.get(f"{BASE_URL}/{endpoint}", params={"Token": token}, timeout=15)
        print(f"  Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"  {json.dumps(data, indent=2)[:500]}")
        else:
            print(f"  Body: {r.text[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n\nDone!")
