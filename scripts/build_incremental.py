path = "/Users/julieschorr/Desktop/store-analysis/scripts/run.py"
with open(path) as f:
    content = f.read()

old_cache_check = """    # Check cache
    cache_file = DATA_DIR / "all_data.json"
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            print(f"Using cached data ({age_hours:.1f}h old)")
            with open(cache_file) as f:
                return json.load(f)"""

new_cache_check = """    # Incremental fetch logic
    cache_file = DATA_DIR / "all_data.json"
    INCREMENTAL_DAYS = 60

    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 2:
            print(f"Cache is only {age_hours:.1f}h old - skipping fetch")
            with open(cache_file) as f:
                return json.load(f)

        print(f"Cache found - running incremental update (last {INCREMENTAL_DAYS} days)...")
        with open(cache_file) as f:
            cached = json.load(f)

        inc_start = (datetime.today() - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y-%m-%d")

        print(f"  Incremental window: {inc_start} to today")

        print("\\n[1/6] Location info...")
        loc = api_get("api/getLocationsInfo")
        cached["location"] = loc
        print(f"  Store: {loc['Data'][0]['CompanyName']}")

        print("\\n[2/6] Employees...")
        emps = api_get("api/dictionary/employees")
        cached["employees"] = emps
        print(f"  {len(emps)} employees")

        print("\\n[3/6] Order items (incremental)...")
        new_items = extract_paginated(
            "api/datadump/v2/orderitems/{days}/{page}/{from_date}/{location_id}",
            inc_start, END_DATE, "order_items", id_field="OrderItemId"
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

        print("\\n[4/6] Orders (incremental)...")
        new_orders = extract_paginated(
            "api/datadump/v1/orders/{days}/{page}/{from_date}/{location_id}",
            inc_start, END_DATE, "orders", id_field="OrderId"
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

        print("\\n[5/6] Time clocks (incremental)...")
        inc_start_dt = datetime.strptime(inc_start, "%Y-%m-%d")
        all_clocks = [c for c in cached.get("time_clocks", [])
                      if str(c.get("TimeIn",""))[:10] < inc_start]
        current = inc_start_dt
        while current <= datetime.today():
            window_end = min(current + timedelta(days=29), datetime.today())
            sd = current.strftime("%Y-%m-%dT00:00:00")
            ed = window_end.strftime("%Y-%m-%dT23:59:59")
            try:
                tc = api_post("api/report/timeClocks", {"startDate": sd, "endDate": ed, "pageIndex": 0, "pageSize": 200})
                records = tc if isinstance(tc, list) else tc.get("data", [])
                all_clocks.extend(records)
                print(f"  Clocks {current.strftime('%b')}: {len(records)} records")
            except Exception as e:
                print(f"  Clocks {current.strftime('%b')}: ERROR {e}")
            current = window_end + timedelta(days=1)
        cached["time_clocks"] = all_clocks

        print("\\n[6/6] Categories...")
        cats = api_get("api/getCategoriesByCompany/1")
        cached["categories"] = cats
        print(f"  {len(cats)} categories")

        with open(cache_file, "w") as f:
            json.dump(cached, f)
        print("Incremental cache saved.")
        return cached"""

if old_cache_check in content:
    content = content.replace(old_cache_check, new_cache_check)
    with open(path, "w") as f:
        f.write(content)
    print("SUCCESS - incremental fetch logic inserted!")
else:
    print("PATTERN NOT FOUND - check spacing")
