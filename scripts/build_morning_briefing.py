"""Generate morning briefing JSON — combined stats for both stores.

Produces data/morning_briefing.json at the repo root with:
- Yesterday's revenue, appointments, transactions per store
- Same day last week comparison
- Week-to-date (Mon → yesterday) + prior week-to-date
- Low stock alerts per store (top 15 most urgent)

Usage:
    python3 scripts/build_morning_briefing.py
"""

import json, sys, os, subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    STORES, PROJ_ROOT, UNTRACKED_SKUS, SUPABASE_URL, SUPABASE_ANON_KEY,
    EXCLUDE_EMPLOYEES, BATHER_NAME_MAP, HICKSVILLE_BATHER_NAME_MAP,
)
from classifier import classify_item

# Per-store bather rosters (PW has bathers, HV currently has none)
_BATHERS_BY_STORE = {
    "port-washington": set(BATHER_NAME_MAP.keys()),
    "hicksville": set(HICKSVILLE_BATHER_NAME_MAP.keys()),
}

# Refresh retail_staffing.json before generating the briefing. Piggybacks here
# because the standalone Retail Staffing step isn't yet wired into the
# GitHub Actions workflow (PAT lacks workflow scope to add it).
_staffing_script = Path(__file__).parent / "build_retail_staffing.py"
if _staffing_script.exists():
    print("Refreshing retail_staffing.json…")
    try:
        subprocess.run([sys.executable, str(_staffing_script)], check=True, timeout=300)
    except Exception as _e:
        print(f"  [warn] retail staffing build failed: {_e}", file=sys.stderr)


def fetch_store_supplies_needed():
    """Pull store supplies from Supabase and return items that need reordering
    (out of stock OR at/below reorder level). Grouped by store.
    Returns dict: { store_key: [ {item_name, current_quantity, reorder_level, unit, status}, ... ] }
    """
    try:
        import httpx as _httpx
        h = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"}
        r = _httpx.get(
            f"{SUPABASE_URL}/rest/v1/store_supplies?select=store,item_name,category,current_quantity,reorder_level,unit&order=item_name",
            headers=h, timeout=15
        )
        if r.status_code != 200:
            print(f"  [supplies] Supabase error {r.status_code}")
            return {}
        rows = r.json()
    except Exception as e:
        print(f"  [supplies] Error fetching: {e}")
        return {}

    by_store = defaultdict(list)
    for row in rows:
        cur = row.get("current_quantity")
        mn  = row.get("reorder_level")
        if cur is None:
            continue
        try:
            cur_f = float(cur)
            mn_f  = float(mn) if mn is not None else 0
        except (ValueError, TypeError):
            continue
        if cur_f == 0:
            status = "out"
        elif mn_f > 0 and cur_f <= mn_f:
            status = "order"
        elif mn_f > 0 and cur_f <= mn_f * 1.5:
            status = "low"
        else:
            continue
        by_store[row["store"]].append({
            "item_name": row.get("item_name") or "Unknown",
            "category": row.get("category") or "",
            "current_quantity": cur_f,
            "reorder_level": mn_f if mn_f > 0 else None,
            "unit": row.get("unit") or "",
            "status": status,
        })
    status_order = {"out": 0, "order": 1, "low": 2}
    for k in by_store:
        by_store[k].sort(key=lambda x: (status_order.get(x["status"], 9), x["item_name"].lower()))
    return dict(by_store)

# Untracked SKU set (bulk treats, cookies, etc — intentionally 0 stock)
_UNTRACKED_SET = {sku for sku, _ in UNTRACKED_SKUS}

OUT_FILE = PROJ_ROOT / "data" / "morning_briefing.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def monday_of_week(d):
    """Return Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def _parse_clock_hours(ti, to):
    """Return decimal hours from a time clock pair, or 0 if malformed."""
    if not ti or not to:
        return 0.0
    try:
        dt_in = datetime.fromisoformat(ti.replace("Z", ""))
        dt_out = datetime.fromisoformat(to.replace("Z", ""))
        h = (dt_out - dt_in).total_seconds() / 3600
        return h if 0 < h <= 16 else 0.0
    except (ValueError, TypeError):
        return 0.0


def compute_capacity_for_range(items, time_clocks, start_iso, end_iso, store_key, closures=None):
    """Capacity stats over [start_iso, end_iso] (inclusive).

    Returns:
      groomer_days: sum across the range of (count of distinct active groomers per day)
      unique_groomers: count of distinct groomers who generated revenue
      groomer_names: sorted list of those names
      groom_rev: total grooming revenue in the range (excludes excluded employees)
      rev_per_groomer_day: groom_rev / groomer_days
      bather_hours: total bather hours from time clocks (PW only — HV has no bathers)
      operating_days: open days in the range (excludes closures)
    """
    closures = closures or set()
    bather_set = _BATHERS_BY_STORE.get(store_key, set())
    daily_groomers = defaultdict(set)
    groom_rev_total = 0.0

    for it in items:
        c = (it.get("CreatedOn") or "")[:10]
        if not c or c < start_iso or c > end_iso:
            continue
        person = (it.get("SalesPerson") or "").strip()
        if not person or person in EXCLUDE_EMPLOYEES:
            continue
        nm = it.get("Name") or ""
        sku = str(it.get("Sku") or "")
        cls = classify_item(nm, sku)
        if not cls.get("is_groom"):
            continue
        try:
            price = float(it.get("Price") or 0) * float(it.get("Quantity") or 1) - float(it.get("Discount") or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        daily_groomers[c].add(person)
        groom_rev_total += price

    groomer_days = sum(len(g) for g in daily_groomers.values())
    distinct_groomers = set()
    for g in daily_groomers.values():
        distinct_groomers.update(g)

    bather_hrs = 0.0
    for c in time_clocks:
        emp = (c.get("EmployeeName") or "").strip()
        if emp not in bather_set:
            continue
        ti = c.get("TimeIn") or ""
        if not ti or ti[:10] < start_iso or ti[:10] > end_iso:
            continue
        bather_hrs += _parse_clock_hours(ti, c.get("TimeOut") or "")

    # Count operating days in the range (excludes closures)
    operating_days = 0
    try:
        d = date.fromisoformat(start_iso)
        end = date.fromisoformat(end_iso)
        while d <= end:
            if d.isoformat() not in closures:
                operating_days += 1
            d += timedelta(days=1)
    except (ValueError, TypeError):
        pass

    return {
        "groomer_days": groomer_days,
        "unique_groomers": len(distinct_groomers),
        "groomer_names": sorted(distinct_groomers),
        "groom_rev": round(groom_rev_total, 2),
        "rev_per_groomer_day": round(groom_rev_total / groomer_days, 2) if groomer_days else 0,
        "bather_hours": round(bather_hrs, 1),
        "operating_days": operating_days,
        "avg_groomers_per_day": round(groomer_days / operating_days, 2) if operating_days else 0,
        "avg_bather_hours_per_day": round(bather_hrs / operating_days, 1) if operating_days else 0,
    }


def fetch_scheduled_capacity(store_key, start_iso, end_iso):
    """Fetch scheduled groomer + bather coverage from Supabase schedule_shifts.

    Returns dict keyed by ISO date: {date: {groomers: int, bathers: int, groomer_hours, bather_hours}}.
    """
    try:
        import httpx as _httpx
        h = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"}
        # Get employees first to map id → role
        emp_resp = _httpx.get(
            f"{SUPABASE_URL}/rest/v1/schedule_employees?select=id,name,role,store",
            headers=h, timeout=15
        )
        emp_resp.raise_for_status()
        emps = {e["id"]: e for e in emp_resp.json()}

        # Get shifts in range
        sh_resp = _httpx.get(
            f"{SUPABASE_URL}/rest/v1/schedule_shifts?store=eq.{store_key}"
            f"&shift_date=gte.{start_iso}&shift_date=lte.{end_iso}"
            f"&select=emp_id,shift_date,start_time,end_time",
            headers=h, timeout=15,
        )
        sh_resp.raise_for_status()
        shifts = sh_resp.json()
    except Exception as e:
        print(f"  [capacity] Supabase unavailable: {e}")
        return {}

    by_day = defaultdict(lambda: {"groomers": set(), "bathers": set(), "groomer_hours": 0.0, "bather_hours": 0.0})
    for s in shifts:
        emp = emps.get(s.get("emp_id"))
        if not emp:
            continue
        role = (emp.get("role") or "").lower()
        d = s.get("shift_date")
        if not d:
            continue
        # Compute shift duration
        try:
            st = s.get("start_time", "")
            en = s.get("end_time", "")
            if st and en:
                # Times like "09:00:00"
                sh, sm = int(st[:2]), int(st[3:5])
                eh, em = int(en[:2]), int(en[3:5])
                hrs = (eh + em / 60) - (sh + sm / 60)
                if hrs < 0:
                    hrs = 0
            else:
                hrs = 0
        except (ValueError, TypeError, IndexError):
            hrs = 0

        if role == "groomer":
            by_day[d]["groomers"].add(emp["id"])
            by_day[d]["groomer_hours"] += hrs
        elif role == "bather":
            by_day[d]["bathers"].add(emp["id"])
            by_day[d]["bather_hours"] += hrs

    return {
        d: {
            "groomers": len(v["groomers"]),
            "bathers": len(v["bathers"]),
            "groomer_hours": round(v["groomer_hours"], 1),
            "bather_hours": round(v["bather_hours"], 1),
        }
        for d, v in by_day.items()
    }


def compute_day_stats(items, target_date_iso):
    """Aggregate revenue/appointments for a single day."""
    order_ids_groom = set()
    order_ids_bath = set()
    order_ids_all = set()
    total_rev = 0.0
    groom_rev = 0.0
    retail_rev = 0.0

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if created != target_date_iso:
            continue
        oid = item.get("OrderId")
        if oid:
            order_ids_all.add(oid)

        name = item.get("Name", "") or ""
        sku = item.get("Sku", "") or ""
        try:
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            price = 0.0
        total_rev += price

        cls = classify_item(name, sku)
        if cls.get("is_groom"):
            groom_rev += price
            if cls.get("groom_category") == "core":
                svc = (cls.get("service_type") or "").lower()
                if "bath" in svc:
                    if oid:
                        order_ids_bath.add(oid)
                else:
                    if oid:
                        order_ids_groom.add(oid)
        elif cls.get("is_retail"):
            retail_rev += price

    txn_count = len(order_ids_all)
    return {
        "total_rev": round(total_rev, 2),
        "groom_rev": round(groom_rev, 2),
        "retail_rev": round(retail_rev, 2),
        "grooms": len(order_ids_groom),
        "baths": len(order_ids_bath),
        "appointments": len(order_ids_groom) + len(order_ids_bath),
        "transactions": txn_count,
        "avg_ticket": round(total_rev / txn_count, 2) if txn_count else 0,
    }


def compute_range_stats(items, start_iso, end_iso):
    """Aggregate stats over a date range (inclusive)."""
    order_ids_groom = set()
    order_ids_bath = set()
    order_ids_all = set()
    total_rev = 0.0
    groom_rev = 0.0
    retail_rev = 0.0

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if not created or created < start_iso or created > end_iso:
            continue
        oid = item.get("OrderId")
        if oid:
            order_ids_all.add(oid)

        name = item.get("Name", "") or ""
        sku = item.get("Sku", "") or ""
        try:
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            price = 0.0
        total_rev += price

        cls = classify_item(name, sku)
        if cls.get("is_groom"):
            groom_rev += price
            if cls.get("groom_category") == "core":
                svc = (cls.get("service_type") or "").lower()
                if "bath" in svc:
                    if oid:
                        order_ids_bath.add(oid)
                else:
                    if oid:
                        order_ids_groom.add(oid)
        elif cls.get("is_retail"):
            retail_rev += price

    return {
        "total_rev": round(total_rev, 2),
        "groom_rev": round(groom_rev, 2),
        "retail_rev": round(retail_rev, 2),
        "grooms": len(order_ids_groom),
        "baths": len(order_ids_bath),
        "appointments": len(order_ids_groom) + len(order_ids_bath),
        "transactions": len(order_ids_all),
    }


def compute_low_stock(items, stock_levels, sku_brands, limit=20):
    """Find SKUs that are low/out of stock. Mirrors inventory_dashboard logic."""
    # Compute monthly velocity from last 90 days
    today = date.today()
    cutoff = (today - timedelta(days=90)).isoformat()

    sku_data = defaultdict(lambda: {"name": "", "units": 0, "months": set(), "vendor": ""})

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if not created or created < cutoff:
            continue
        sku = str(item.get("Sku", "") or "").strip()
        if len(sku) < 3:
            continue
        # Skip untracked SKUs (bulk treats, cookies, etc. — not tracked in inventory)
        if sku in _UNTRACKED_SET:
            continue
        # Skip service SKU prefixes
        if sku.startswith(("321", "987", "765", "543", "432", "986", "985", "984", "983", "982")):
            continue
        name = item.get("Name", "") or ""
        cls = classify_item(name, sku)
        if not cls.get("is_retail"):
            continue
        try:
            qty = float(item.get("Quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        sku_data[sku]["name"] = sku_data[sku]["name"] or name
        sku_data[sku]["units"] += qty
        sku_data[sku]["months"].add(created[:7])
        brand_info = sku_brands.get(sku, {})
        if isinstance(brand_info, dict):
            sku_data[sku]["vendor"] = brand_info.get("brand", "") or ""

    alerts = []
    for sku, d in sku_data.items():
        stock = stock_levels.get(sku)
        if stock is None:
            continue
        vel_monthly = d["units"] / max(len(d["months"]), 1) if d["units"] else 0
        vel_weekly = vel_monthly / 4.33 if vel_monthly else 0
        # Skip very-low-velocity items (< 1 unit/month avg) — they don't need urgent reorder
        if vel_monthly < 1:
            continue
        wos = stock / vel_weekly if vel_weekly > 0 else None

        # Determine status
        if stock <= 0:
            status = "out"
        elif wos is not None and wos < 1:
            status = "critical"
        elif wos is not None and wos < 2:
            status = "low"
        else:
            continue  # OK — don't include

        alerts.append({
            "sku": sku,
            "name": d["name"][:48],
            "stock": round(stock, 1),
            "weeks_of_supply": round(wos, 1) if wos is not None else None,
            "velocity_monthly": round(vel_monthly, 1),
            "status": status,
            "vendor": d["vendor"] or "Other",
        })

    # Sort by: out first, critical second, low third; then by velocity desc
    status_order = {"out": 0, "critical": 1, "low": 2}
    alerts.sort(key=lambda x: (status_order.get(x["status"], 9), -x["velocity_monthly"]))
    return alerts[:limit]


def build_store_briefing(store_key, store_cfg):
    """Build briefing data for a single store."""
    print(f"\nProcessing {store_key}...")
    data_dir = store_cfg.data_dir

    # Load order items
    with open(data_dir / "all_data.json") as f:
        raw = json.load(f)
    items = raw.get("order_items", [])

    # Load stock + brand caches (may not exist)
    stock_levels = {}
    sf = data_dir / "stock_levels.json"
    if sf.exists():
        with open(sf) as f:
            stock_levels = json.load(f)
    sku_brands = {}
    bf = data_dir / "sku_brands.json"
    if bf.exists():
        with open(bf) as f:
            sku_brands = json.load(f)

    # Date ranges
    today = date.today()
    yesterday = today - timedelta(days=1)
    last_week_same_day = yesterday - timedelta(days=7)

    # Week-to-date: Monday → yesterday
    this_monday = monday_of_week(today)
    wtd_end = yesterday if yesterday >= this_monday else this_monday
    # Prior week-to-date: same number of days ending last week
    prev_monday = this_monday - timedelta(days=7)
    prev_wtd_end = prev_monday + (wtd_end - this_monday)

    # Month-to-date: 1st of this month → yesterday
    mtd_start = today.replace(day=1)
    mtd_end = yesterday if yesterday >= mtd_start else mtd_start
    days_into_month = (mtd_end - mtd_start).days  # elapsed days (0-indexed from day 1)
    # Prior month-to-date: 1st of last month → same day offset
    if mtd_start.month == 1:
        prev_mtd_start = mtd_start.replace(year=mtd_start.year - 1, month=12)
    else:
        prev_mtd_start = mtd_start.replace(month=mtd_start.month - 1)
    prev_mtd_end = prev_mtd_start + timedelta(days=days_into_month)
    # Clamp if last month was shorter (e.g. today is Mar 31, last month = Feb)
    last_day_prev_month = (mtd_start - timedelta(days=1))
    if prev_mtd_end > last_day_prev_month:
        prev_mtd_end = last_day_prev_month

    # Year-to-date: Jan 1 → yesterday
    ytd_start = today.replace(month=1, day=1)
    ytd_end = yesterday if yesterday >= ytd_start else ytd_start
    days_into_year = (ytd_end - ytd_start).days
    # Prior year-to-date: Jan 1 last year → same day offset
    prev_ytd_start = ytd_start.replace(year=ytd_start.year - 1)
    prev_ytd_end = prev_ytd_start + timedelta(days=days_into_year)

    # Capacity: who actually generated revenue (active groomers/day) + bather hours
    time_clocks = raw.get("time_clocks", [])
    # Closures for accurate operating-day denominators
    try:
        from fetch_closures import fetch_closures
        store_closures = fetch_closures(store_key)
    except Exception:
        store_closures = set()

    yesterday_cap = compute_capacity_for_range(items, time_clocks, yesterday.isoformat(), yesterday.isoformat(), store_key, store_closures)
    mtd_cap = compute_capacity_for_range(items, time_clocks, mtd_start.isoformat(), mtd_end.isoformat(), store_key, store_closures)
    prev_mtd_cap = compute_capacity_for_range(items, time_clocks, prev_mtd_start.isoformat(), prev_mtd_end.isoformat(), store_key, store_closures)
    # Trailing 30-day baseline (excluding today's partial)
    trailing_start = (yesterday - timedelta(days=29)).isoformat()
    trailing_cap = compute_capacity_for_range(items, time_clocks, trailing_start, yesterday.isoformat(), store_key, store_closures)

    # Forward-looking capacity from schedule_shifts
    sched_today = fetch_scheduled_capacity(store_key, today.isoformat(), today.isoformat()).get(today.isoformat(), {"groomers": 0, "bathers": 0, "groomer_hours": 0, "bather_hours": 0})
    sched_tomorrow_d = (today + timedelta(days=1)).isoformat()
    sched_tomorrow = fetch_scheduled_capacity(store_key, sched_tomorrow_d, sched_tomorrow_d).get(sched_tomorrow_d, {"groomers": 0, "bathers": 0, "groomer_hours": 0, "bather_hours": 0})
    next7_end = (today + timedelta(days=6)).isoformat()
    next7_map = fetch_scheduled_capacity(store_key, today.isoformat(), next7_end)
    next7_groomer_days = sum(v["groomers"] for v in next7_map.values())
    next7_bather_hours = sum(v["bather_hours"] for v in next7_map.values())

    briefing = {
        "label": "Port Washington" if store_key == "port-washington" else "Hicksville",
        "yesterday": compute_day_stats(items, yesterday.isoformat()),
        "last_week_same_day": compute_day_stats(items, last_week_same_day.isoformat()),
        "wtd": compute_range_stats(items, this_monday.isoformat(), wtd_end.isoformat()),
        "prev_wtd": compute_range_stats(items, prev_monday.isoformat(), prev_wtd_end.isoformat()),
        "mtd": compute_range_stats(items, mtd_start.isoformat(), mtd_end.isoformat()),
        "prev_mtd": compute_range_stats(items, prev_mtd_start.isoformat(), prev_mtd_end.isoformat()),
        "mtd_label": f"{mtd_start.strftime('%b %-d')} → {mtd_end.strftime('%b %-d')}",
        "prev_mtd_label": f"{prev_mtd_start.strftime('%b %-d')} → {prev_mtd_end.strftime('%b %-d')}",
        "ytd": compute_range_stats(items, ytd_start.isoformat(), ytd_end.isoformat()),
        "prev_ytd": compute_range_stats(items, prev_ytd_start.isoformat(), prev_ytd_end.isoformat()),
        "ytd_label": f"Jan 1 → {ytd_end.strftime('%b %-d, %Y')}",
        "prev_ytd_label": f"Jan 1 → {prev_ytd_end.strftime('%b %-d, %Y')}",
        "low_stock": compute_low_stock(items, stock_levels, sku_brands),
        # Capacity-aware metrics
        "capacity": {
            "yesterday": {
                "active_groomers": yesterday_cap["unique_groomers"],
                "groomer_names": yesterday_cap["groomer_names"],
                "groom_rev": yesterday_cap["groom_rev"],
                "rev_per_groomer": yesterday_cap["rev_per_groomer_day"],
                "bather_hours": yesterday_cap["bather_hours"],
            },
            "mtd": {
                "groomer_days": mtd_cap["groomer_days"],
                "rev_per_groomer_day": mtd_cap["rev_per_groomer_day"],
                "avg_groomers_per_day": mtd_cap["avg_groomers_per_day"],
                "bather_hours": mtd_cap["bather_hours"],
                "avg_bather_hours_per_day": mtd_cap["avg_bather_hours_per_day"],
            },
            "prev_mtd": {
                "groomer_days": prev_mtd_cap["groomer_days"],
                "rev_per_groomer_day": prev_mtd_cap["rev_per_groomer_day"],
                "avg_groomers_per_day": prev_mtd_cap["avg_groomers_per_day"],
                "bather_hours": prev_mtd_cap["bather_hours"],
                "avg_bather_hours_per_day": prev_mtd_cap["avg_bather_hours_per_day"],
            },
            "trailing_30d": {
                "rev_per_groomer_day": trailing_cap["rev_per_groomer_day"],
                "avg_groomers_per_day": trailing_cap["avg_groomers_per_day"],
                "avg_bather_hours_per_day": trailing_cap["avg_bather_hours_per_day"],
            },
            "today_scheduled": sched_today,
            "tomorrow_scheduled": sched_tomorrow,
            "next_7_days": {
                "groomer_days": next7_groomer_days,
                "bather_hours": round(next7_bather_hours, 1),
            },
        },
    }
    print(f"  Yesterday: ${briefing['yesterday']['total_rev']:,.0f} rev, "
          f"{briefing['yesterday']['appointments']} appts, "
          f"{briefing['yesterday']['transactions']} txns")
    print(f"  Capacity yesterday: {yesterday_cap['unique_groomers']} groomers active "
          f"(${yesterday_cap['rev_per_groomer_day']:,.0f}/groomer), "
          f"{yesterday_cap['bather_hours']:.1f} bather hours")
    print(f"  Trailing 30d baseline: ${trailing_cap['rev_per_groomer_day']:,.0f}/groomer/day "
          f"with {trailing_cap['avg_groomers_per_day']:.1f} avg groomers/day")
    print(f"  Today scheduled: {sched_today['groomers']} groomers, {sched_today['bather_hours']:.1f}h bathers")
    print(f"  WTD: ${briefing['wtd']['total_rev']:,.0f} rev (vs prev: ${briefing['prev_wtd']['total_rev']:,.0f})")
    print(f"  MTD: ${briefing['mtd']['total_rev']:,.0f} rev (vs prev month same period: ${briefing['prev_mtd']['total_rev']:,.0f})")
    print(f"  YTD: ${briefing['ytd']['total_rev']:,.0f} rev (vs prev year same period: ${briefing['prev_ytd']['total_rev']:,.0f})")
    print(f"  Low stock: {len(briefing['low_stock'])} items flagged")
    return briefing


def _trend_line(cur, prev, label, fmt="currency"):
    """Format a one-line trend comparison."""
    if fmt == "currency":
        c = f"${cur:,.0f}"
        p = f"${prev:,.0f}"
    else:
        c = f"{cur:,.0f}"
        p = f"{prev:,.0f}"
    if prev == 0:
        return f"{label}: {c} (no comparable prior)"
    pct = (cur - prev) / prev * 100
    direction = "up" if pct > 1 else "down" if pct < -1 else "flat"
    return f"{label}: {c} vs {p} ({direction} {abs(pct):.0f}%)"


def generate_executive_summary(output):
    """Generate a Bezos-style morning memo using Claude. Returns summary text or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[AI summary] Skipped — no ANTHROPIC_API_KEY env var")
        return None

    try:
        import httpx as _httpx
    except ImportError:
        print("[AI summary] Skipped — httpx not available")
        return None

    # Build a compact data brief for the LLM
    lines = []
    today_date = datetime.fromisoformat(output["today"]).strftime("%A, %B %d, %Y")
    yesterday_label = datetime.fromisoformat(output["yesterday"]).strftime("%A")
    lines.append(f"Today: {today_date}")
    lines.append(f"Data represents {yesterday_label}'s performance (yesterday).")
    lines.append("")

    for store_key, store in output.get("stores", {}).items():
        if "error" in store:
            continue
        lines.append(f"## {store['label']}")
        y = store["yesterday"]
        prev = store["last_week_same_day"]
        wtd = store["wtd"]
        pwtd = store["prev_wtd"]
        lines.append(f"Yesterday:")
        lines.append(f"  - {_trend_line(y['total_rev'], prev['total_rev'], 'Total revenue')}")
        lines.append(f"  - {_trend_line(y['groom_rev'], prev['groom_rev'], 'Grooming revenue')}")
        lines.append(f"  - {_trend_line(y['retail_rev'], prev['retail_rev'], 'Retail revenue')}")
        lines.append(f"  - {_trend_line(y['transactions'], prev['transactions'], 'Transactions', fmt='int')}")
        lines.append(f"  - Grooms: {y['grooms']}, Baths: {y['baths']}, Avg ticket: ${y['avg_ticket']:.0f}")
        lines.append(f"Week-to-date (Mon through yesterday):")
        lines.append(f"  - {_trend_line(wtd['total_rev'], pwtd['total_rev'], 'Revenue')}")
        lines.append(f"  - {_trend_line(wtd['appointments'], pwtd['appointments'], 'Appointments', fmt='int')}")
        mtd = store.get("mtd", {})
        pmtd = store.get("prev_mtd", {})
        if mtd:
            lines.append(f"Month-to-date ({store.get('mtd_label','this month')}) vs same period last month ({store.get('prev_mtd_label','')}):")
            lines.append(f"  - {_trend_line(mtd['total_rev'], pmtd['total_rev'], 'Revenue')}")
            lines.append(f"  - {_trend_line(mtd['appointments'], pmtd['appointments'], 'Appointments', fmt='int')}")
        ytd = store.get("ytd", {})
        pytd = store.get("prev_ytd", {})
        if ytd:
            lines.append(f"Year-to-date ({store.get('ytd_label','YTD')}) vs same period last year ({store.get('prev_ytd_label','')}):")
            lines.append(f"  - {_trend_line(ytd['total_rev'], pytd['total_rev'], 'Revenue')}")
            lines.append(f"  - {_trend_line(ytd['appointments'], pytd['appointments'], 'Appointments', fmt='int')}")
        # Capacity & coverage — the master driver. Frame revenue in terms of staffing.
        cap = store.get("capacity", {})
        if cap:
            cy = cap.get("yesterday", {}) or {}
            ct = cap.get("trailing_30d", {}) or {}
            cs = cap.get("today_scheduled", {}) or {}
            ctm = cap.get("tomorrow_scheduled", {}) or {}
            cm = cap.get("mtd", {}) or {}
            cpm = cap.get("prev_mtd", {}) or {}
            n7 = cap.get("next_7_days", {}) or {}
            lines.append(f"Capacity & coverage (groomers per day is THE driver):")
            if cy.get("active_groomers"):
                lines.append(f"  - Yesterday: {cy['active_groomers']} active groomer(s) producing ${cy['rev_per_groomer']:,.0f}/groomer; trailing-30d baseline ${ct.get('rev_per_groomer_day',0):,.0f}/groomer/day")
                if cy.get("groomer_names"):
                    lines.append(f"    On the floor: {', '.join(cy['groomer_names'])}")
            if cy.get("bather_hours") is not None:
                lines.append(f"  - Bather hours yesterday: {cy['bather_hours']:.1f}h (trailing-30d avg {ct.get('avg_bather_hours_per_day',0):.1f}h/day)")
            if cm.get("groomer_days") and cpm.get("groomer_days"):
                lines.append(f"  - MTD groomer-days: {cm['groomer_days']} ({cm['avg_groomers_per_day']:.1f}/day) vs prev-mo {cpm['groomer_days']} ({cpm['avg_groomers_per_day']:.1f}/day)")
                lines.append(f"  - MTD $/groomer-day: ${cm['rev_per_groomer_day']:,.0f} vs prev-mo ${cpm['rev_per_groomer_day']:,.0f}")
            lines.append(f"  - Today scheduled: {cs.get('groomers',0)} groomer(s), {cs.get('bather_hours',0):.1f}h bathers")
            lines.append(f"  - Tomorrow scheduled: {ctm.get('groomers',0)} groomer(s), {ctm.get('bather_hours',0):.1f}h bathers")
            lines.append(f"  - Next 7 days scheduled: {n7.get('groomer_days',0)} groomer-days, {n7.get('bather_hours',0):.1f}h bathers")
        low = store.get("low_stock", [])
        out = sum(1 for x in low if x["status"] == "out")
        crit = sum(1 for x in low if x["status"] == "critical")
        lines.append(f"Inventory: {out} items out of stock, {crit} critical (< 1 wk supply)")
        # Top 3 low-stock items with highest velocity
        if low:
            top = low[:3]
            lines.append(f"Top reorder priorities: " + ", ".join(f"{a['name']} ({a['velocity_monthly']:.0f}/mo, {a['stock']} left)" for a in top))
        supplies = store.get("supplies_needed", [])
        if supplies:
            sup_out = sum(1 for x in supplies if x["status"] == "out")
            sup_order = sum(1 for x in supplies if x["status"] == "order")
            lines.append(f"Store supplies (cleaning/grooming/packaging): {sup_out} out of stock, {sup_order} at reorder level")
            top_sup = [x["item_name"] for x in supplies[:4] if x["status"] in ("out","order")]
            if top_sup:
                lines.append(f"  Supplies to reorder: {', '.join(top_sup)}")
        lines.append("")

    data_brief = "\n".join(lines)

    prompt = f"""You are the Chief of Staff to Kyle, CEO and President of Woof Gang Bakery & Grooming — a 2-location pet grooming and retail business (Port Washington #264 and Hicksville #265 in Long Island, NY).

Write his morning briefing. Model your style on the way a trusted chief of staff would brief Jeff Bezos: direct, specific, strategic, efficient. No fluff. Lead with what matters most. Highlight one thing he should be proud of and one thing he should pay attention to. If you notice a pattern across both stores, name it. If something is concerning, say so plainly. If something is going well, say so.

CRITICAL FRAMING: The number of groomers working each day is THE driver of revenue at this business. Always interpret revenue numbers through the lens of capacity. If yesterday's revenue was down but only 2 groomers worked vs the typical 4, that is a capacity issue, not a performance issue. If revenue per active groomer is above the trailing-30d baseline, the team is running hot regardless of total dollars. Use $/groomer-day as your primary productivity yardstick. When you reference today's or tomorrow's outlook, mention how many groomers are scheduled — Kyle wants to know if today is staffed for a strong number.

End the briefing with a single explicit "Capacity & coverage" sentence (just one sentence) calling out today's and tomorrow's scheduled groomer count and bather hours, plus next 7 days groomer-days. This sentence is required even on slow news days.

Write 3-4 short paragraphs, prose only — no bullet points, no headers, no markdown. Keep it under 220 words total. Address him by name (Kyle) once at the start.

Here is the data:

{data_brief}

Write the briefing now."""

    try:
        print("\n[AI summary] Calling Claude...")
        r = _httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        if r.status_code != 200:
            print(f"[AI summary] API error {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        text = text.strip()
        print(f"[AI summary] Generated {len(text)} chars")
        return text
    except Exception as e:
        print(f"[AI summary] Error: {e}")
        return None


def main():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    output = {
        "generated_at": datetime.now(et).isoformat(),
        "today": date.today().isoformat(),
        "yesterday": (date.today() - timedelta(days=1)).isoformat(),
        "week_start": monday_of_week(date.today()).isoformat(),
        "stores": {},
    }

    for store_key, store_cfg in STORES.items():
        try:
            output["stores"][store_key] = build_store_briefing(store_key, store_cfg)
        except Exception as e:
            print(f"  ERROR for {store_key}: {e}")
            output["stores"][store_key] = {"error": str(e)}

    # Attach store supplies needed (from Supabase store_supplies table)
    print("\nFetching store supplies from Supabase...")
    supplies_by_store = fetch_store_supplies_needed()
    for store_key, items in supplies_by_store.items():
        if store_key in output["stores"] and "error" not in output["stores"][store_key]:
            output["stores"][store_key]["supplies_needed"] = items
            print(f"  {store_key}: {len(items)} supplies flagged")

    # Attach upcoming closures (next 14 days) per store
    print("\nFetching upcoming store closures...")
    try:
        from fetch_closures import fetch_closures_with_reasons
        today_d = date.today()
        horizon = today_d + timedelta(days=14)
        for store_key in output["stores"]:
            if "error" in output["stores"][store_key]:
                continue
            cls_map = fetch_closures_with_reasons(store_key)
            upcoming = []
            for iso, reason in cls_map.items():
                try:
                    cd = date.fromisoformat(iso)
                except (ValueError, TypeError):
                    continue
                if today_d <= cd <= horizon:
                    upcoming.append({"date": iso, "reason": reason})
            upcoming.sort(key=lambda x: x["date"])
            output["stores"][store_key]["upcoming_closures"] = upcoming
            if upcoming:
                print(f"  {store_key}: {len(upcoming)} upcoming closure(s)")
    except Exception as _ce:
        print(f"  [closures] error: {_ce}")

    # Generate executive summary via Claude
    output["summary_text"] = generate_executive_summary(output)

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWritten: {OUT_FILE}")


if __name__ == "__main__":
    main()
