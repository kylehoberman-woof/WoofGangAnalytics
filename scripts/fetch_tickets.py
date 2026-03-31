"""Fetch ticket/payment data from FranPOS tickets API.

Each ticket includes EZPAY field (payment method: amex, visa, mastercard, cash, etc.)
and OrderId that links to our order_items. This lets us calculate CC processing fees.

Usage:
    python3 scripts/fetch_tickets.py              # Port Washington
    python3 scripts/fetch_tickets.py hicksville   # Hicksville
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

out_file = data_dir / "tickets.json"

# Load existing cache
cached = {}
if out_file.exists():
    with open(out_file) as f:
        cached = json.load(f)
    print(f"Existing cache: {len(cached)} tickets")

# Fetch tickets using monthly windows
start_dt = datetime.strptime(store.start_date, "%Y-%m-%d")
end_dt = datetime.today()

# Build monthly windows
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

print(f"Fetching tickets for {store_name} ({len(windows)} monthly windows)...")

new_count = 0
PAGE_SIZE = 200

for window_start, window_end in windows:
    sd = window_start.strftime("%Y-%m-%dT00:00:00")
    ed = window_end.strftime("%Y-%m-%dT23:59:59")

    page = 0
    total_pages = 1

    while page < total_pages:
        try:
            r = httpx.post(
                f"{BASE_URL}/api/report/v2/tickets",
                params={"Token": token},
                json={
                    "startDate": sd,
                    "endDate": ed,
                    "pageIndex": page,
                    "pageSize": PAGE_SIZE,
                    "locationId": location_id,
                },
                timeout=45,
            )
            if r.status_code != 200:
                print(f"  {window_start.strftime('%Y-%m')}: HTTP {r.status_code}")
                break

            data = r.json()
            items = data.get("data", [])
            total_pages = data.get("pages", 1)

            added = 0
            for ticket in items:
                oid = str(ticket.get("OrderId", ""))
                if not oid or oid in cached:
                    continue

                # Extract only what we need: payment type, totals, date
                cached[oid] = {
                    "order_id": ticket.get("OrderId"),
                    "date": (ticket.get("DateCreated") or "")[:10],
                    "payment_method": (ticket.get("EZPAY") or "").strip().lower(),
                    "transaction_id": ticket.get("TransactionID") or "",
                    "subtotal": ticket.get("SubTotal", 0),
                    "tax": ticket.get("TaxTotal", 0),
                    "total": ticket.get("OrderTotal", 0),
                    "discount": ticket.get("DiscountTotal", 0),
                    "customer_id": ticket.get("CustomerId"),
                }
                added += 1

            new_count += added

            if page == 0:
                print(f"  {window_start.strftime('%Y-%m')}: {len(items)} tickets ({added} new) [pages: {total_pages}]")
            elif added > 0:
                print(f"    page {page}: {len(items)} ({added} new)")

            if not items:
                break

        except Exception as e:
            print(f"  {window_start.strftime('%Y-%m')}: ERROR {e}")
            time.sleep(2)
            break

        page += 1
        time.sleep(0.3)

# Save
with open(out_file, "w") as f:
    json.dump(cached, f, indent=2)

# Summary
methods = {}
for t in cached.values():
    m = t.get("payment_method", "unknown") or "unknown"
    methods[m] = methods.get(m, 0) + 1

print(f"\nDone! {new_count} new tickets. Total: {len(cached)}")
print("Payment methods:")
for m, c in sorted(methods.items(), key=lambda x: -x[1]):
    print(f"  {m}: {c}")
