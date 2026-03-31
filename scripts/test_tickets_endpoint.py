"""Test FranPOS tickets endpoint to discover payment type fields.

Run from GitHub Actions (IP restricted locally):
    python3 scripts/test_tickets_endpoint.py

This is a one-time diagnostic script — not part of the nightly build.
"""

import httpx, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL

store = get_store("port-washington")
token = store.token
location_id = store.location_id

# Try all 3 versions of the tickets endpoint
for endpoint in ['api/report/v2/tickets', 'api/report/v1/tickets', 'api/report/tickets']:
    print(f"\n{'='*60}")
    print(f"Testing: {endpoint}")
    print(f"{'='*60}")
    try:
        r = httpx.post(
            f"{BASE_URL}/{endpoint}",
            params={"Token": token},
            json={
                "startDate": "2026-03-28T00:00:00",
                "endDate": "2026-03-28T23:59:59",
                "pageIndex": 0,
                "pageSize": 3,
                "locationId": location_id
            },
            timeout=45,
        )
        print(f"  Status: {r.status_code}")
        print(f"  Content-Type: {r.headers.get('content-type', '?')}")

        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                data = r.json()
                if isinstance(data, list):
                    print(f"  Array with {len(data)} items")
                    if data:
                        print(f"  Keys: {sorted(data[0].keys())}")
                        print(f"\n  FULL FIRST RECORD:")
                        print(json.dumps(data[0], indent=2))
                elif isinstance(data, dict):
                    print(f"  Dict keys: {sorted(data.keys())}")
                    # Try common wrapper keys
                    for key in ['data', 'Data', 'tickets', 'Tickets', 'Items']:
                        if key in data and isinstance(data[key], list) and data[key]:
                            print(f"\n  Items in '{key}': {len(data[key])}")
                            print(f"  Keys: {sorted(data[key][0].keys())}")
                            print(f"\n  FULL FIRST RECORD:")
                            print(json.dumps(data[key][0], indent=2))
                            break
                    else:
                        print(json.dumps(data, indent=2)[:1000])
            else:
                # CSV or other format
                print(f"  Response (first 500 chars):")
                print(r.text[:500])
        else:
            print(f"  Body: {r.text[:200]}")

    except Exception as e:
        print(f"  ERROR: {e}")

print("\n\nDone! Check output for payment/tender fields.")
