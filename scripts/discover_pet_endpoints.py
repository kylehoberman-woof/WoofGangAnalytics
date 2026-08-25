"""Probe FranPOS API for pet-level appointment data.

Runs in GitHub Actions (IP-restricted). Saves findings to data/pet_endpoint_discovery.json.

Tests:
  1. walkin/booking/appointments — may return daily schedule with per-pet detail
  2. walkin/queue — current walk-in queue (pet + groomer + service)
  3. customers/history/{pet_cid}/json — per-pet transaction history
  4. company/formfields — custom pet fields Woof Gang configured
  5. datadump/v1/customers — check if customer records have pet sub-objects

Usage:
    python3 scripts/discover_pet_endpoints.py
    python3 scripts/discover_pet_endpoints.py hicksville
"""

import httpx, json, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, BASE_URL

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
token = store.token
location_id = store.location_id
data_dir = store.data_dir

findings = {}

def probe(label, url, params=None, method="GET", body=None):
    try:
        p = {"Token": token, **(params or {})}
        if method == "GET":
            r = httpx.get(url, params=p, timeout=20)
        else:
            r = httpx.post(url, params=p, json=body or {}, timeout=20)
        findings[label] = {
            "status": r.status_code,
            "url": str(r.url),
        }
        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list):
                    findings[label]["type"] = "array"
                    findings[label]["count"] = len(data)
                    if data:
                        findings[label]["sample_keys"] = sorted(data[0].keys()) if isinstance(data[0], dict) else None
                        findings[label]["sample"] = data[:2]
                elif isinstance(data, dict):
                    findings[label]["type"] = "dict"
                    findings[label]["keys"] = sorted(data.keys())
                    # Dig into nested arrays
                    for k in data:
                        if isinstance(data[k], list) and data[k]:
                            findings[label][f"nested_{k}_count"] = len(data[k])
                            if isinstance(data[k][0], dict):
                                findings[label][f"nested_{k}_keys"] = sorted(data[k][0].keys())
                            findings[label][f"nested_{k}_sample"] = data[k][:2]
                    findings[label]["sample"] = data
            except Exception as e:
                findings[label]["parse_error"] = str(e)
                findings[label]["raw"] = r.text[:500]
        else:
            findings[label]["body"] = r.text[:300]
        print(f"  {label}: {r.status_code}")
    except Exception as e:
        findings[label] = {"error": str(e)}
        print(f"  {label}: ERROR {e}")

today = date.today().isoformat()
yesterday = (date.today() - timedelta(days=1)).isoformat()
company_id = store.location_id  # may differ — try both

print(f"Probing FranPOS for pet-level data ({store_name})...")

# 1. Walk-in booking appointments — API doc says companyId + dateStr
# Try companyId = location_id (they may be the same value)
probe("walkin_booking_appointments_today",
      f"{BASE_URL}/api/walkin/booking/appointments",
      params={"companyId": location_id, "dateStr": today})

probe("walkin_booking_appointments_tomorrow",
      f"{BASE_URL}/api/walkin/booking/appointments",
      params={"companyId": location_id, "dateStr": (date.today() + timedelta(days=1)).isoformat()})

# Also try with locationId in case companyId != locationId
probe("walkin_booking_appointments_locationid",
      f"{BASE_URL}/api/walkin/booking/appointments",
      params={"locationId": location_id, "dateStr": today})

# 2. Walk-in queue
probe("walkin_queue",
      f"{BASE_URL}/api/walkin/queue",
      params={"locationId": location_id})

# 3. Walk-in settings (to understand how Woof Gang has it configured)
probe("walkin_settings",
      f"{BASE_URL}/api/walkin/{location_id}/settings")

# 4. Walk-in simple services (service catalog with pet size options)
probe("walkin_simple_services",
      f"{BASE_URL}/api/walkin/{location_id}/simpleServices",
      method="POST", body={"locationId": location_id})

# 5. Walk-in services
probe("walkin_services",
      f"{BASE_URL}/api/walkin/{location_id}/services",
      method="POST", body={"locationId": location_id})

# 6. Company form fields (custom pet fields)
probe("company_formfields",
      f"{BASE_URL}/api/company/formfields",
      params={"locationId": location_id})

# 7. Booking status (might list current/recent appointments)
probe("booking_getstatus",
      f"{BASE_URL}/api/booking/getstatus",
      params={"locationId": location_id})

# Employee schedules — who is working on a given date (companyId + dateStr)
probe("walkin_employee_schedules_today",
      f"{BASE_URL}/api/walkin/employee/schedules",
      params={"companyId": location_id, "dateStr": today})

probe("walkin_employee_schedules_tomorrow",
      f"{BASE_URL}/api/walkin/employee/schedules",
      params={"companyId": location_id, "dateStr": (date.today() + timedelta(days=1)).isoformat()})

# 8. Customer history for a few known pet CIDs
# Load customer_names to find some pet CIDs (pets have owner set, no lastname)
names_file = data_dir / "customer_names.json"
if names_file.exists():
    with open(names_file) as f:
        names = json.load(f)
    # Find pet accounts (have owner name, have pet name)
    pet_accounts = [(cid, info) for cid, info in names.items()
                    if info.get("pet") and info.get("owner") and "," not in info.get("pet","")]
    print(f"  Found {len(pet_accounts)} single-pet accounts to probe")
    for cid, info in pet_accounts[:3]:
        name = info['pet'].replace(' ','_')
        # JSON version (already working)
        probe(f"customer_history_{name}",
              f"{BASE_URL}/api/customers/history/{cid}/json")
        # CSV version — may have more fields including OrderId
        probe(f"customer_history_{name}_csv",
              f"{BASE_URL}/api/customers/history/{cid}/csv")
        # Full customer record — may include linked order IDs
        probe(f"customer_record_{name}",
              f"{BASE_URL}/api/Customers/{cid}",
              params={"locationId": location_id})

# 9. Datadump customers — check if they have pet sub-objects
probe("datadump_customers_sample",
      f"{BASE_URL}/api/datadump/v1/customers/7/0/{yesterday}/{location_id}")

# Save all findings
out_file = data_dir / "pet_endpoint_discovery.json"
with open(out_file, "w") as f:
    json.dump(findings, f, indent=2)

print(f"\nFindings saved to {out_file}")
for label, result in findings.items():
    status = result.get("status", "ERR")
    if status == 200:
        count = result.get("count", result.get("nested_data_count", "?"))
        keys = result.get("sample_keys", result.get("keys", []))
        print(f"  ✓ {label}: {count} records | keys: {keys[:8]}")
    else:
        print(f"  ✗ {label}: {status}")
