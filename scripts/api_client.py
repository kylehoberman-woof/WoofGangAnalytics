"""
FranPOS API client: authentication, pagination, data extraction, and caching.
"""

import json
import time
from datetime import datetime, timedelta, date
from collections import defaultdict

import httpx

from config import BASE_URL, PAGE_SIZE, ET_BUFFER_DAYS, INCREMENTAL_DAYS, KNOWN_CLOSURES


def api_get(endpoint, params=None, timeout=60, *, token, base_url=BASE_URL):
    """Make authenticated GET request to FranPOS API."""
    p = params or {}
    p["Token"] = token
    r = httpx.get(f"{base_url}/{endpoint}", params=p, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        return r.json()
    return r.text


def api_post(endpoint, body, params=None, timeout=60, *, token, base_url=BASE_URL):
    """Make authenticated POST request."""
    p = params or {}
    p["Token"] = token
    r = httpx.post(f"{base_url}/{endpoint}", params=p, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def extract_paginated(endpoint_template, start, end, label="data", id_field="OrderItemId",
                      *, token, location_id, base_url=BASE_URL):
    """Extract paginated data one calendar month at a time to prevent boundary gaps."""
    all_data = []
    _seen = set()

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    # Build list of month-start dates
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

    for window_start, window_end in windows:
        fetch_start = window_start - timedelta(days=ET_BUFFER_DAYS)
        fetch_end = window_end + timedelta(days=ET_BUFFER_DAYS)
        days = (fetch_end - fetch_start).days + 1
        from_date = fetch_start.strftime("%Y-%m-%d")
        page = 0
        total_pages = 1

        while page < total_pages:
            endpoint = endpoint_template.format(
                days=days, page=page, from_date=from_date, location_id=location_id
            )
            print(f"  [{label}] {from_date} +{days}d, page {page}...", end="", flush=True)
            try:
                result = api_get(endpoint, token=token, base_url=base_url)
                if isinstance(result, dict):
                    items = result.get("data", [])
                    total_pages = result.get("pages", 1)
                else:
                    items = []
                if not items and page > 1:
                    print(f" 0 items — done with window")
                    break
                added = 0
                for item in items:
                    key = item.get(id_field) or item.get("OrderId") or item.get("Id")
                    if key is not None:
                        if key not in _seen:
                            _seen.add(key)
                            all_data.append(item)
                            added += 1
                    else:
                        all_data.append(item)
                        added += 1
                print(f" {len(items)} items ({added} new) (page {page}/{total_pages})")
            except Exception as e:
                print(f" ERROR: {e}")
                time.sleep(2)
                try:
                    result = api_get(endpoint, token=token, base_url=base_url)
                    if isinstance(result, dict):
                        items = result.get("data", [])
                        total_pages = result.get("pages", 1)
                    else:
                        items = []
                    added = 0
                    for item in items:
                        key = item.get(id_field) or item.get("OrderId") or item.get("Id")
                        if key is not None:
                            if key not in _seen:
                                _seen.add(key)
                                all_data.append(item)
                                added += 1
                        else:
                            all_data.append(item)
                            added += 1
                    print(f"  RETRY OK: {len(items)} items ({added} new)")
                except Exception as e2:
                    print(f"  RETRY FAILED: {e2}")
            page += 1
            time.sleep(0.3)

    print(f"  Total unique records: {len(all_data)}")
    return all_data


def _gap_detection(data, start_date, location_id, token):
    """Detect and patch dates with suspiciously few orders."""
    print("\n[Gap detection] Checking for missing data...")

    daily_orders = defaultdict(set)
    for it in data["order_items"]:
        d = str(it.get("CreatedOn", ""))[:10]
        oid = it.get("OrderId")
        if oid and d >= start_date:
            daily_orders[d].add(oid)

    cur = datetime.strptime(start_date, "%Y-%m-%d").date()
    today = date.today()
    gap_dates = []
    while cur <= today:
        day = str(cur)
        if day not in KNOWN_CLOSURES and len(daily_orders.get(day, set())) < 15:
            gap_dates.append(day)
        cur += timedelta(days=1)

    if not gap_dates:
        print("  No gaps detected.")
        return

    print(f"  Found {len(gap_dates)} dates to patch: {gap_dates}")
    existing_ids = set(i.get("OrderItemId") for i in data["order_items"])
    total_gap_added = 0
    for gap_date in gap_dates:
        gdt = datetime.strptime(gap_date, "%Y-%m-%d")
        fs = (gdt - timedelta(days=2)).strftime("%Y-%m-%d")
        page = 0
        tp = 1
        found = []
        seen = set()
        while page < tp:
            ep = f"api/datadump/v2/orderitems/7/{page}/{fs}/{location_id}"
            try:
                res = api_get(ep, token=token)
                its = res.get("data", []) if isinstance(res, dict) else []
                tp = res.get("pages", 1) if isinstance(res, dict) else 1
                for i in its:
                    k = i.get("OrderItemId")
                    if k and k not in seen:
                        seen.add(k)
                        if str(i.get("CreatedOn", "")).startswith(gap_date):
                            found.append(i)
                page += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    Error on {gap_date}: {e}")
                break
        added = 0
        for i in found:
            if i.get("OrderItemId") not in existing_ids:
                data["order_items"].append(i)
                existing_ids.add(i.get("OrderItemId"))
                added += 1
        total_gap_added += added
        if added > 0:
            print(f"  {gap_date}: recovered {added} items")
    print(f"  Gap detection complete. Total recovered: {total_gap_added}")


def _fetch_stock_levels(data, token, location_id):
    """Fetch stock levels for all SKUs found in order items."""
    print("\n[7/6] Fetching stock levels by SKU...")
    skus = set()
    for item in data["order_items"]:
        sku = item.get("Sku", "").strip()
        if sku and len(sku) > 2:
            skus.add(sku)
    stock_data = {}
    errors = 0
    for sku in sorted(skus):
        try:
            r = httpx.get(f"{BASE_URL}/api/getStockByProductSKU/{sku}/{location_id}",
                          params={"Token": token}, timeout=10)
            if r.status_code == 200:
                stock_data[sku] = float(r.text.strip())
            time.sleep(0.1)
        except Exception:
            errors += 1
    print(f"  Fetched {len(stock_data)} stock levels ({errors} errors)")
    return stock_data


def extract_all_data(store):
    """Extract all needed data from FranPOS API, with disk caching."""
    store.data_dir.mkdir(parents=True, exist_ok=True)
    data = {}
    token = store.token
    location_id = store.location_id

    cache_file = store.data_dir / "all_data.json"

    if cache_file.exists():
        print(f"Cache found - running incremental update (last {INCREMENTAL_DAYS} days)...")
        with open(cache_file) as f:
            cached = json.load(f)

        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        print(f"Cache is {age_hours:.1f}h old - running incremental fetch...")

        inc_start = (datetime.today() - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y-%m-%d")
        print(f"  Incremental window: {inc_start} to today")

        print("\n[1/6] Location info...")
        loc = api_get("api/getLocationsInfo", token=token)
        cached["location"] = loc
        print(f"  Store: {loc['Data'][0]['CompanyName']}")

        print("\n[2/6] Employees...")
        emps = api_get("api/dictionary/employees", token=token)
        cached["employees"] = emps
        print(f"  {len(emps)} employees")

        print("\n[3/6] Order items (incremental)...")
        new_items = extract_paginated(
            "api/datadump/v2/orderitems/{days}/{page}/{from_date}/{location_id}",
            inc_start, store.end_date, "order_items", id_field="OrderItemId",
            token=token, location_id=location_id,
        )
        existing_item_ids = set()
        kept_items = []
        for item in cached.get("order_items", []):
            d = str(item.get("CreatedOn", ""))[:10]
            if d < inc_start:
                kept_items.append(item)
                existing_item_ids.add(item.get("OrderItemId"))
        for item in new_items:
            key = item.get("OrderItemId")
            if key not in existing_item_ids:
                kept_items.append(item)
                existing_item_ids.add(key)
        cached["order_items"] = kept_items

        print("\n[4/6] Orders (incremental)...")
        new_orders = extract_paginated(
            "api/datadump/v1/orders/{days}/{page}/{from_date}/{location_id}",
            inc_start, store.end_date, "orders", id_field="OrderId",
            token=token, location_id=location_id,
        )
        existing_order_ids = set()
        kept_orders = []
        for order in cached.get("orders", []):
            d = str(order.get("CreatedOn", ""))[:10]
            if d < inc_start:
                kept_orders.append(order)
                existing_order_ids.add(order.get("OrderId"))
        for order in new_orders:
            key = order.get("OrderId")
            if key not in existing_order_ids:
                kept_orders.append(order)
                existing_order_ids.add(key)
        cached["orders"] = kept_orders

        print(f"  After merge: {len(kept_items)} items, {len(kept_orders)} orders")

        print("\n[5/6] Time clocks (incremental)...")
        inc_start_dt = datetime.strptime(inc_start, "%Y-%m-%d")
        all_clocks = [c for c in cached.get("time_clocks", [])
                      if str(c.get("TimeIn", ""))[:10] < inc_start]
        current = inc_start_dt
        while current <= datetime.today():
            window_end = min(current + timedelta(days=29), datetime.today())
            sd = current.strftime("%Y-%m-%dT00:00:00")
            ed = window_end.strftime("%Y-%m-%dT23:59:59")
            try:
                tc = api_post("api/report/timeClocks",
                              {"startDate": sd, "endDate": ed, "pageIndex": 0, "pageSize": 200,
                               "locationId": location_id},
                              token=token)
                records = tc if isinstance(tc, list) else tc.get("data", [])
                # Note: CompanyId filter removed — some HV retail staff (Hailey, Christina,
                # Chris) are registered under PW's CompanyId in FranPOS. Commission dashboards
                # correctly scope by employee name maps per store instead.
                all_clocks.extend(records)
                print(f"  Clocks {current.strftime('%b')}: {len(records)} records")
            except Exception as e:
                print(f"  Clocks {current.strftime('%b')}: ERROR {e}")
            current = window_end + timedelta(days=1)
        cached["time_clocks"] = all_clocks

        print("\n[6/6] Categories...")
        cats = api_get("api/getCategoriesByCompany/1", token=token)
        cached["categories"] = cats
        print(f"  {len(cats)} categories")

        with open(cache_file, "w") as f:
            json.dump(cached, f)
        print("Incremental cache saved.")
        return cached

    # Full extraction (no cache)
    print("=" * 60)
    print("EXTRACTING DATA FROM FRANPOS API")
    print("=" * 60)

    print("\n[1/6] Location info...")
    loc = api_get("api/getLocationsInfo", token=token)
    data["location"] = loc
    print(f"  Store: {loc['Data'][0]['CompanyName']}")

    print("\n[2/6] Employees...")
    emps = api_get("api/dictionary/employees", token=token)
    data["employees"] = emps
    print(f"  {len(emps)} employees")

    print("\n[3/6] Order items (line-item detail)...")
    items = extract_paginated(
        "api/datadump/v2/orderitems/{days}/{page}/{from_date}/{location_id}",
        store.start_date, store.end_date, "order_items", id_field="OrderItemId",
        token=token, location_id=location_id,
    )
    data["order_items"] = items
    print(f"  TOTAL: {len(items)} line items")

    print("\n[4/6] Orders (transaction level)...")
    orders = extract_paginated(
        "api/datadump/v1/orders/{days}/{page}/{from_date}/{location_id}",
        store.start_date, store.end_date, "orders", id_field="OrderId",
        token=token, location_id=location_id,
    )
    data["orders"] = orders
    print(f"  TOTAL: {len(orders)} orders")

    # Gap detection
    _gap_detection(data, store.start_date, location_id, token)

    # Time clocks
    print("\n[5/6] Time clocks...")
    all_clocks = []
    current = datetime.strptime(store.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(store.end_date, "%Y-%m-%d")
    while current <= end_dt:
        window_end = min(current + timedelta(days=29), end_dt)
        sd = current.strftime("%Y-%m-%dT00:00:00")
        ed = window_end.strftime("%Y-%m-%dT23:59:59")
        try:
            tc = api_post("api/report/timeClocks",
                          {"startDate": sd, "endDate": ed, "pageIndex": 0, "pageSize": 200,
                           "locationId": location_id},
                          token=token)
            records = tc if isinstance(tc, list) else tc.get("data", [])
            # Note: CompanyId filter removed — see incremental path comment above
            all_clocks.extend(records)
            print(f"  Clocks {current.strftime('%b')}: {len(records)} records")
        except Exception as e:
            print(f"  Clocks {current.strftime('%b')}: ERROR {e}")
        current = window_end + timedelta(days=1)
        time.sleep(0.3)
    data["time_clocks"] = all_clocks
    print(f"  TOTAL: {len(all_clocks)} clock records")

    # Categories
    print("\n[6/6] Categories...")
    cats = api_get("api/getCategoriesByCompany/1", token=token)
    data["categories"] = cats.get("data", []) if isinstance(cats, dict) else cats
    print(f"  {len(data['categories'])} categories")

    # Stock levels
    stock_data = _fetch_stock_levels(data, token, location_id)
    data["stock_levels"] = stock_data
    stock_file = store.data_dir / "stock_levels.json"
    with open(stock_file, "w") as f:
        json.dump(stock_data, f)
    print(f"  Stock levels saved to {stock_file}")

    # Save cache
    with open(cache_file, "w") as f:
        json.dump(data, f)
    print(f"\nData cached to {cache_file}")

    return data


def extract_full_catalog(store):
    """Pull full product catalog from FranPOS including stock levels."""
    print("Fetching full product catalog from FranPOS...")
    token = store.token
    location_id = store.location_id
    all_products = []
    seen = set()
    page = 0
    while True:
        try:
            data = api_get("getProductServicesByCompany", {
                "locationId": location_id,
                "page": page
            }, token=token)
        except Exception as e:
            print(f"  Catalog page {page} error: {e}")
            break
        if not data:
            break
        items = data if isinstance(data, list) else data.get("ProductServices", data.get("Items", data.get("Data", [])))
        if not items:
            break
        new = 0
        for item in items:
            uid = str(item.get("Id", "")) + str(item.get("Sku", ""))
            if uid not in seen:
                seen.add(uid)
                all_products.append(item)
                new += 1
        print(f"  Page {page}: {new} products (total: {len(all_products)})")
        if new == 0 or len(items) < 50:
            break
        page += 1
    out = store.data_dir / "full_catalog.json"
    with open(out, "w") as f:
        json.dump(all_products, f, indent=2)
    print(f"  Catalog saved: {len(all_products)} products")
    return all_products
