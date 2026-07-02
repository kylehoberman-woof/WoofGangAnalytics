"""Build retail staffing financial data for the owner dashboard.

Produces data/retail_staffing.json with per-month breakdowns of:
- Casey / Chris / replacement hours + cost
- Manager (Cindy) salary allocated to PW
- Total labor per month
- Days open (excluding known closures)
- Retail sales per month
- Labor as % of sales
- Cost per open day

Focus: Port Washington (the user's main concern).

Usage:
    python3 scripts/build_retail_staffing.py
"""

import json, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict
from calendar import monthrange

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    STORES, PROJ_ROOT, KNOWN_CLOSURES,
    MANAGER_SALARY_OLD, MANAGER_SALARY_NEW, MANAGER_RAISE_DATE,
    MANAGER_START, STORE_OPEN_DATES,
    SUPABASE_URL, SUPABASE_ANON_KEY,
    get_retail_rate,
    STORE_REGISTRY, get_store_display,
)
from fetch_employees import fetch_employees

try:
    import httpx as _httpx
except ImportError:
    _httpx = None

SB_HEADERS = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"}

# Flag any day where |actual - scheduled| exceeds this many hours
VARIANCE_THRESHOLD_HRS = 0.5  # 30 minutes
# Additional flag: clock-in earlier than scheduled by more than N minutes,
# or clock-out later than scheduled by more than N minutes
PUNCH_FLAG_MINUTES = 5
VARIANCE_LOOKBACK_DAYS = 30
FORWARD_PROJECTION_DAYS = 62  # about 2 months out


def sb_get(path):
    if _httpx is None:
        return []
    try:
        r = _httpx.get(f"{SUPABASE_URL}/rest/v1{path}", headers=SB_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [Supabase] error fetching {path[:60]}: {e}")
        return []


def shift_hours(start_time, end_time):
    """Compute hours from HH:MM:SS strings."""
    if not start_time or not end_time:
        return 0
    try:
        st = start_time[:5]; et = end_time[:5]
        sh, sm = int(st[:2]), int(st[3:5])
        eh, em = int(et[:2]), int(et[3:5])
        h = (eh * 60 + em - sh * 60 - sm) / 60
        return h if 0 < h <= 16 else 0
    except Exception:
        return 0


OUT_FILE = PROJ_ROOT / "data" / "retail_staffing.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def _build_retail_cfg(store_key):
    """Build retail config dict from Supabase (active_only=False for history).
    Falls back to hardcoded defaults if Supabase is unavailable."""
    emps = fetch_employees(store=store_key, role='retail', active_only=False)
    if emps:
        return {
            e['full_name']: {'current_rate': float(e['hourly_rate']), 'role': 'retail'}
            for e in emps if e.get('full_name') and e.get('hourly_rate')
        }
    # Hardcoded fallback — only used if Supabase is down
    fallbacks = {
        'port-washington': {
            'Casey Makowski':  {'current_rate': 21.00, 'role': 'retail'},
            'Christine Brower':{'current_rate': 20.00, 'role': 'retail'},
            'Trinity  Rivera': {'current_rate': 21.00, 'role': 'retail'},
            'Alize James':     {'current_rate': 19.00, 'role': 'retail'},
            'Sitara Nagrani':  {'current_rate': 19.00, 'role': 'retail'},
            'Giana Golden':    {'current_rate': 19.00, 'role': 'retail'},
            'Parker Spooner':  {'current_rate': 19.00, 'role': 'retail'},
        },
        'hicksville': {
            'Hailey Imhof':         {'current_rate': 21.00, 'role': 'retail'},
            'Kayla Moses':          {'current_rate': 19.00, 'role': 'retail'},
            'Christine Brower':     {'current_rate': 20.00, 'role': 'retail'},
            'Sophia Kurkowski':     {'current_rate': 19.00, 'role': 'retail'},
            'Naomi Dutes':          {'current_rate': 19.00, 'role': 'retail'},
            'Nicole Alarcon':       {'current_rate': 19.00, 'role': 'retail'},
            'Christina Ramkissoon': {'current_rate': 19.00, 'role': 'retail'},
        },
    }
    print(f"  [warn] Supabase unavailable for {store_key} retail — using hardcoded fallback")
    return fallbacks.get(store_key, {})


# Per-store config — built from STORE_REGISTRY so adding a new store to
# data/stores.json automatically adds it here.
# retail_staffing_start_month in stores.json controls start_month per store.
def _open_date_from_registry(sk):
    """Parse open_date string from registry into a date object."""
    from datetime import date as _date
    od = STORE_REGISTRY.get(sk, {}).get("open_date", "")
    try:
        return _date.fromisoformat(od)
    except ValueError:
        return STORE_OPEN_DATES.get(sk, _date(2024, 9, 26))

STORE_CFG = {
    sk: {
        'label': get_store_display(sk),
        'open_date': _open_date_from_registry(sk),
        'include_manager': STORE_REGISTRY[sk].get("include_manager", False),
        'start_month': STORE_REGISTRY[sk].get("retail_staffing_start_month",
                       _open_date_from_registry(sk).strftime("%Y-%m")),
    }
    for sk in STORE_REGISTRY
}

try:
    from fetch_closures import fetch_closures
except ImportError:
    fetch_closures = None

# CLOSURES is now per-store, populated lazily inside build_store_staffing.
# Falls back to KNOWN_CLOSURES if Supabase is unavailable.
CLOSURES = set(KNOWN_CLOSURES)


def parse_hours(ti, to):
    try:
        dt_in = datetime.fromisoformat(ti.replace('Z', ''))
        dt_out = datetime.fromisoformat(to.replace('Z', ''))
        h = (dt_out - dt_in).total_seconds() / 3600
        return h if 0 < h <= 16 else 0
    except Exception:
        return 0


def days_open_in_month(y, m, today, open_date, closures=None):
    """Count days the store was open vs. closed in a given month.

    closures: optional set of ISO date strings. Falls back to module-level
    CLOSURES (KNOWN_CLOSURES) if not provided — used for backward compat.
    """
    if closures is None:
        closures = CLOSURES
    first_day = date(y, m, 1)
    last_day = date(y, m, monthrange(y, m)[1])
    if last_day > today:
        last_day = today
    if last_day < open_date:
        return 0, 0
    start_day = max(first_day, open_date)
    if start_day > last_day:
        return 0, 0
    open_count = 0
    closure_count = 0
    d = start_day
    while d <= last_day:
        if d.isoformat() in closures:
            closure_count += 1
        else:
            open_count += 1
        d += timedelta(days=1)
    return open_count, closure_count


def manager_salary_for_month(y, m, today, include_bonus=False):
    """Cindy's salary for the given calendar month. Excludes bonus by default."""
    from config import MANAGER_BONUS, MANAGER_BONUS_DATE

    first_day = date(y, m, 1)
    last_day = date(y, m, monthrange(y, m)[1])
    if last_day > today:
        last_day = today
    if last_day < MANAGER_START:
        return 0.0
    start_day = max(first_day, MANAGER_START)
    if start_day > last_day:
        return 0.0
    total = 0.0
    d = start_day
    while d <= last_day:
        rate = MANAGER_SALARY_NEW/365 if d >= MANAGER_RAISE_DATE else MANAGER_SALARY_OLD/365
        total += rate
        if include_bonus and d == MANAGER_BONUS_DATE:
            total += MANAGER_BONUS
        d += timedelta(days=1)
    return round(total, 2)


def build_store_staffing(store_key):
    """Build monthly staffing data for either PW or HV."""
    cfg = STORE_CFG[store_key]
    retail_cfg = _build_retail_cfg(store_key)
    open_date = cfg['open_date']
    include_manager = cfg['include_manager']
    START = cfg['start_month']

    store = STORES[store_key]
    with open(store.data_dir / 'all_data.json') as f:
        data = json.load(f)

    # Per-store closures from Supabase (merges KNOWN_CLOSURES as fallback)
    store_closures = fetch_closures(store_key) if fetch_closures else CLOSURES

    clocks = data.get('time_clocks', [])
    items = data.get('order_items', [])

    # Cap "today" at the latest day with actual sales data. The nightly
    # data dump runs after close, so before the store opens on day N+1 the
    # data only contains transactions through day N. Counting day N+1
    # before any business has happened would inflate manager salary days
    # and days_open by 1 (and show a row for a day that hasn't occurred).
    today = date.today()
    _max_data_date = max(((it.get('CreatedOn') or '')[:10] for it in items), default='')
    if _max_data_date:
        today = min(today, date.fromisoformat(_max_data_date))

    # Monthly retail hours + costs per person (costs use date-aware rates)
    monthly_hours = defaultdict(lambda: defaultdict(float))
    monthly_costs = defaultdict(lambda: defaultdict(float))
    for c in clocks:
        emp = (c.get('EmployeeName') or '').strip()
        ti = c.get('TimeIn', '')
        if not ti or emp not in retail_cfg:
            continue
        if ti[:7] < START:
            continue
        hrs = parse_hours(ti, c.get('TimeOut', ''))
        fallback = {nm: info['current_rate'] for nm, info in retail_cfg.items()}
        rate = get_retail_rate(emp, ti[:10], fallback)
        monthly_hours[ti[:7]][emp] += hrs
        monthly_costs[ti[:7]][emp] += hrs * rate

    # Monthly sales (total net revenue)
    monthly_sales = defaultdict(float)
    for item in items:
        created = (item.get('CreatedOn') or '')[:10]
        if not created or created[:7] < START:
            continue
        try:
            price = float(item.get('Price') or 0) * float(item.get('Quantity') or 1)
            disc = float(item.get('Discount') or 0)
        except (ValueError, TypeError):
            continue
        monthly_sales[created[:7]] += (price - disc)

    # Assemble monthly rows. Include any month with EITHER hours or sales so
    # the current month appears even before retail staff first clock in
    # (e.g., early-month days when only the salaried manager / bathers worked).
    months = sorted(set(monthly_hours.keys()) | set(monthly_sales.keys()))
    rows = []
    for m in months:
        y, mo = int(m[:4]), int(m[5:7])

        # Per-person hours + costs (costs from monthly_costs which use date-aware rates)
        people = []
        retail_hrs = 0
        retail_cost = 0.0
        for nm, info in retail_cfg.items():
            hrs = monthly_hours[m].get(nm, 0)
            if hrs <= 0:
                continue
            cost = round(monthly_costs[m].get(nm, 0), 2)
            effective_rate = round(cost / hrs, 2) if hrs else info['current_rate']
            retail_hrs += hrs
            retail_cost += cost
            people.append({'name': nm, 'hrs': round(hrs, 1), 'rate': effective_rate, 'cost': cost})
        retail_cost = round(retail_cost, 2)

        mgr_cost = manager_salary_for_month(y, mo, today, include_bonus=False) if include_manager else 0.0
        total_labor = round(retail_cost + mgr_cost, 2)
        days_open, closures = days_open_in_month(y, mo, today, open_date, store_closures)
        sales = round(monthly_sales.get(m, 0), 2)
        labor_pct = round((total_labor / sales) * 100, 2) if sales else 0
        cost_per_day = round(total_labor / days_open, 2) if days_open else 0

        row = {
            'month': m,
            'month_label': datetime.strptime(m, '%Y-%m').strftime('%b %Y'),
            'people': sorted(people, key=lambda x: -x['cost']),
            'retail_hrs': round(retail_hrs, 1),
            'retail_cost': retail_cost,
            'mgr_cost': round(mgr_cost, 2),
            'total_labor': total_labor,
            'sales': sales,
            'labor_pct_sales': labor_pct,
            'days_open': days_open,
            'closures': closures,
            'cost_per_open_day': cost_per_day,
        }
        # Back-compat for existing PW-only JS: keep Casey/Chris/Cindy aliases
        if store_key == 'port-washington':
            row['casey_hrs']  = round(monthly_hours[m].get('Casey Makowski', 0), 1)
            row['casey_cost'] = round(monthly_costs[m].get('Casey Makowski', 0), 2)
            row['chris_hrs']  = round(monthly_hours[m].get('Christine Brower', 0), 1)
            row['chris_cost'] = round(monthly_costs[m].get('Christine Brower', 0), 2)
            row['cindy_cost'] = row['mgr_cost']
        rows.append(row)

    # Summary stats (excluding partial current month)
    current_month = today.strftime('%Y-%m')
    full_rows = [r for r in rows if r['month'] != current_month]

    if full_rows:
        best = min(full_rows, key=lambda r: r['labor_pct_sales'] if r['sales'] > 0 else 999)
        worst = max(full_rows, key=lambda r: r['labor_pct_sales'] if r['sales'] > 0 else -1)
        ytd_labor = sum(r['total_labor'] for r in full_rows if r['month'] >= '2026-01')
        ytd_sales = sum(r['sales'] for r in full_rows if r['month'] >= '2026-01')
        ytd_pct = round((ytd_labor / ytd_sales) * 100, 2) if ytd_sales else 0
    else:
        best = worst = None
        ytd_labor = ytd_sales = ytd_pct = 0

    current_row = next((r for r in rows if r['month'] == current_month), None)
    latest_complete = full_rows[-1] if full_rows else None

    rates_block = {nm: info['current_rate'] for nm, info in retail_cfg.items()}
    if include_manager:
        rates_block['cindy_annual'] = MANAGER_SALARY_NEW
        rates_block['cindy_daily']  = round(MANAGER_SALARY_NEW / 365, 2)
    # Back-compat aliases for PW (derive from retail_cfg so hardcoded names aren't needed)
    if store_key == 'port-washington':
        for full_name, info in retail_cfg.items():
            if 'Casey' in full_name:
                rates_block['casey'] = info['current_rate']
            elif 'Christine' in full_name:
                rates_block['chris'] = info['current_rate']

    return {
        'store': store_key,
        'label': cfg['label'],
        'has_manager': include_manager,
        'rows': rows,
        'summary': {
            'best_month': {
                'month': best['month'],
                'label': best['month_label'],
                'labor_pct': best['labor_pct_sales'],
                'labor': best['total_labor'],
                'sales': best['sales'],
            } if best else None,
            'worst_month': {
                'month': worst['month'],
                'label': worst['month_label'],
                'labor_pct': worst['labor_pct_sales'],
                'labor': worst['total_labor'],
                'sales': worst['sales'],
            } if worst else None,
            'latest_complete_month': latest_complete,
            'current_month': current_row,
            'ytd_2026': {
                'labor': round(ytd_labor, 2),
                'sales': round(ytd_sales, 2),
                'labor_pct': ytd_pct,
            },
        },
        'rates': rates_block,
    }


def build_pw_staffing():
    """Back-compat wrapper — PW only."""
    return build_store_staffing('port-washington')


def _hhmm_to_mins(hhmm):
    """Convert 'HH:MM' or 'HH:MM:SS' string to minutes since midnight. Returns None if invalid."""
    if not hhmm:
        return None
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
        return h * 60 + m
    except Exception:
        return None


def _iso_to_mins(iso_ts):
    """From '2026-04-15T08:45:00', extract hour:minute → mins since midnight."""
    if not iso_ts or len(iso_ts) < 16:
        return None
    try:
        return int(iso_ts[11:13]) * 60 + int(iso_ts[14:16])
    except Exception:
        return None


def _mins_to_hhmm(mins):
    if mins is None:
        return ''
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def compute_variances(today, store_key='port-washington'):
    """Compare actual hours (from time_clocks) vs scheduled hours (from Supabase)
    for the last N days. Flag any day where:
      - Total hours variance exceeds VARIANCE_THRESHOLD_HRS, OR
      - Clock-in was earlier than scheduled start by > PUNCH_FLAG_MINUTES, OR
      - Clock-out was later than scheduled end by > PUNCH_FLAG_MINUTES."""
    if _httpx is None:
        return []

    start_iso = (today - timedelta(days=VARIANCE_LOOKBACK_DAYS)).isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()

    # Fetch active retail + manager employees
    emps = sb_get(f"/schedule_employees?is_active=eq.true&role=in.(retail,manager)&select=id,name,full_name,role,store,hourly_rate")
    if not emps:
        return []

    name_by_id = {e['id']: (e.get('full_name') or e.get('name') or '').strip() for e in emps}
    rate_by_name = {}
    role_by_name = {}
    for e in emps:
        nm = (e.get('full_name') or e.get('name') or '').strip()
        if nm:
            rate_by_name[nm] = float(e.get('hourly_rate') or 0)
            role_by_name[nm] = e.get('role', 'retail')
    track_names = {nm for nm, role in role_by_name.items() if role == 'retail'}  # skip Cindy (salaried)

    # Fetch scheduled shifts for this store (last 30 days)
    shifts = sb_get(f"/schedule_shifts?store=eq.{store_key}&shift_date=gte.{start_iso}&shift_date=lte.{yesterday_iso}&select=emp_id,shift_date,start_time,end_time")

    # For each (name, date): total scheduled hrs, earliest start, latest end
    scheduled_hrs = defaultdict(lambda: defaultdict(float))
    scheduled_start = defaultdict(dict)  # (name, date) -> earliest start mins
    scheduled_end = defaultdict(dict)    # (name, date) -> latest end mins
    for s in shifts:
        nm = name_by_id.get(s.get('emp_id'))
        if not nm or nm not in track_names:
            continue
        d = s.get('shift_date')
        st_mins = _hhmm_to_mins(s.get('start_time', ''))
        et_mins = _hhmm_to_mins(s.get('end_time', ''))
        if st_mins is None or et_mins is None or et_mins <= st_mins:
            continue
        scheduled_hrs[nm][d] += (et_mins - st_mins) / 60
        cur_start = scheduled_start[nm].get(d)
        scheduled_start[nm][d] = st_mins if cur_start is None else min(cur_start, st_mins)
        cur_end = scheduled_end[nm].get(d)
        scheduled_end[nm][d] = et_mins if cur_end is None else max(cur_end, et_mins)

    # For each (name, date): total actual hrs, earliest TimeIn, latest TimeOut
    store = STORES[store_key]
    with open(store.data_dir / 'all_data.json') as f:
        data = json.load(f)
    clocks = data.get('time_clocks', [])

    actual_hrs = defaultdict(lambda: defaultdict(float))
    actual_start = defaultdict(dict)
    actual_end = defaultdict(dict)
    for c in clocks:
        emp = (c.get('EmployeeName') or '').strip()
        if emp not in track_names:
            continue
        ti = c.get('TimeIn', '')
        to = c.get('TimeOut', '')
        if not ti or ti[:10] < start_iso or ti[:10] > yesterday_iso:
            continue
        d = ti[:10]
        hrs = parse_hours(ti, to)
        if hrs <= 0:
            continue
        actual_hrs[emp][d] += hrs
        ti_mins = _iso_to_mins(ti)
        to_mins = _iso_to_mins(to)
        if ti_mins is not None:
            cur_in = actual_start[emp].get(d)
            actual_start[emp][d] = ti_mins if cur_in is None else min(cur_in, ti_mins)
        if to_mins is not None:
            cur_out = actual_end[emp].get(d)
            actual_end[emp][d] = to_mins if cur_out is None else max(cur_out, to_mins)

    # Build variance records
    variances = []
    for nm in track_names:
        all_dates = set(list(actual_hrs.get(nm, {}).keys()) + list(scheduled_hrs.get(nm, {}).keys()))
        for d in sorted(all_dates, reverse=True):
            act = actual_hrs.get(nm, {}).get(d, 0)
            sch = scheduled_hrs.get(nm, {}).get(d, 0)
            delta = act - sch

            # Punch-level metrics: compare earliest clock-in to scheduled start
            # and latest clock-out to scheduled end
            sch_start = scheduled_start.get(nm, {}).get(d)
            sch_end = scheduled_end.get(nm, {}).get(d)
            act_start = actual_start.get(nm, {}).get(d)
            act_end = actual_end.get(nm, {}).get(d)

            # early_in_mins: positive means clocked in BEFORE scheduled start
            early_in_mins = (sch_start - act_start) if (sch_start is not None and act_start is not None) else 0
            # late_out_mins: positive means clocked out AFTER scheduled end
            late_out_mins = (act_end - sch_end) if (sch_end is not None and act_end is not None) else 0

            # Apply flag rules
            total_variance_flag = abs(delta) >= VARIANCE_THRESHOLD_HRS
            early_in_flag = early_in_mins > PUNCH_FLAG_MINUTES
            late_out_flag = late_out_mins > PUNCH_FLAG_MINUTES
            if not (total_variance_flag or early_in_flag or late_out_flag):
                continue

            rate = rate_by_name.get(nm, 0)
            # Cost of early-in/late-out minutes specifically
            early_cost = (early_in_mins / 60) * rate if early_in_mins > 0 else 0
            late_cost = (late_out_mins / 60) * rate if late_out_mins > 0 else 0

            variances.append({
                'date': d,
                'day_name': datetime.strptime(d, '%Y-%m-%d').strftime('%a'),
                'employee': nm,
                'scheduled_hrs': round(sch, 2),
                'actual_hrs': round(act, 2),
                'variance_hrs': round(delta, 2),
                'variance_cost': round(delta * rate, 2),
                'status': 'over' if delta > 0.01 else ('under' if delta < -0.01 else 'punch'),
                # Punch-level details
                'scheduled_start': _mins_to_hhmm(sch_start),
                'actual_start': _mins_to_hhmm(act_start),
                'scheduled_end': _mins_to_hhmm(sch_end),
                'actual_end': _mins_to_hhmm(act_end),
                'early_in_mins': max(0, early_in_mins),
                'late_out_mins': max(0, late_out_mins),
                'early_in_cost': round(early_cost, 2),
                'late_out_cost': round(late_cost, 2),
                'punch_bonus_mins': max(0, early_in_mins) + max(0, late_out_mins),
                'punch_bonus_cost': round(early_cost + late_cost, 2),
            })

    variances.sort(key=lambda v: v['date'], reverse=True)
    return variances


def compute_forward_projection(today, store_key='port-washington'):
    """Project upcoming retail labor cost based on scheduled shifts in Supabase.
    Looks at the rest of this month + next month."""
    if _httpx is None:
        return {'months': [], 'detail': []}

    start_iso = today.isoformat()
    end_iso = (today + timedelta(days=FORWARD_PROJECTION_DAYS)).isoformat()

    # Active employees with rates
    emps = sb_get(f"/schedule_employees?is_active=eq.true&role=in.(retail,manager)&select=id,name,full_name,role,hourly_rate")
    if not emps:
        return {'months': [], 'detail': []}

    name_by_id = {e['id']: (e.get('full_name') or e.get('name') or '').strip() for e in emps}
    rate_by_name = {}
    role_by_name = {}
    for e in emps:
        nm = (e.get('full_name') or e.get('name') or '').strip()
        if nm:
            rate_by_name[nm] = float(e.get('hourly_rate') or 0)
            role_by_name[nm] = e.get('role', 'retail')
    retail_names = {nm for nm, role in role_by_name.items() if role == 'retail'}

    # Fetch scheduled shifts for this store
    shifts = sb_get(f"/schedule_shifts?store=eq.{store_key}&shift_date=gte.{start_iso}&shift_date=lte.{end_iso}&select=emp_id,shift_date,start_time,end_time")

    # Aggregate by month → name → hours
    monthly = defaultdict(lambda: defaultdict(float))
    for s in shifts:
        nm = name_by_id.get(s.get('emp_id'))
        if not nm or nm not in retail_names:
            continue
        hrs = shift_hours(s.get('start_time'), s.get('end_time'))
        if hrs > 0:
            monthly[s.get('shift_date', '')[:7]][nm] += hrs

    results = []
    for m in sorted(monthly.keys()):
        y, mo = int(m[:4]), int(m[5:7])
        month_label = datetime(y, mo, 1).strftime('%b %Y')

        # Retail cost from scheduled hours + known rates
        people = []
        retail_cost = 0.0
        retail_hrs = 0.0
        for nm, hrs in monthly[m].items():
            rate = rate_by_name.get(nm, 0)
            cost = hrs * rate
            retail_cost += cost
            retail_hrs += hrs
            people.append({'name': nm, 'hrs': round(hrs, 1), 'rate': rate, 'cost': round(cost, 2)})

        # Manager cost for that month (excl. bonus) — only for PW (Cindy 100% there)
        include_mgr = STORE_CFG[store_key]['include_manager']
        mgr_cost = manager_salary_for_month(y, mo, date(y, mo, monthrange(y, mo)[1]), include_bonus=False) if include_mgr else 0.0
        total = retail_cost + mgr_cost

        results.append({
            'month': m,
            'month_label': month_label,
            'people': sorted(people, key=lambda x: -x['cost']),
            'retail_hrs': round(retail_hrs, 1),
            'retail_cost': round(retail_cost, 2),
            'mgr_cost': round(mgr_cost, 2),
            'total_labor': round(total, 2),
        })

    return {'months': results, 'generated_at': today.isoformat()}


def main():
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
    today = date.today()

    stores_output = {}
    for store_key in ['port-washington', 'hicksville']:
        print(f'\n=== Building {STORE_CFG[store_key]["label"]} retail staffing data ===')
        store_data = build_store_staffing(store_key)

        print(f'  Computing variances (actual vs scheduled)...')
        variances = compute_variances(today, store_key)
        print(f"    {len(variances)} variance flags in last {VARIANCE_LOOKBACK_DAYS} days")
        for v in variances[:3]:
            print(f"      {v['date']} {v['employee']}: sched {v['scheduled_hrs']}h, actual {v['actual_hrs']}h → {v['variance_hrs']:+.1f}h (${v['variance_cost']:+.0f})")

        print(f'  Building forward schedule projection...')
        forward = compute_forward_projection(today, store_key)
        print(f"    {len(forward['months'])} upcoming months projected")
        for m in forward['months']:
            print(f"      {m['month_label']}: {m['retail_hrs']}h retail ${m['retail_cost']:,.0f} + mgr ${m['mgr_cost']:,.0f} = ${m['total_labor']:,.0f}")

        store_data['variances'] = variances
        store_data['forward_projection'] = forward
        stores_output[store_key] = store_data

        print(f"  Months tracked: {len(store_data['rows'])}")
        if store_data['summary']['latest_complete_month']:
            l = store_data['summary']['latest_complete_month']
            print(f"  Latest complete: {l['month_label']} — ${l['total_labor']:,.0f} labor ({l['labor_pct_sales']}% of sales)")

    output = {
        'generated_at': datetime.now(et).isoformat(),
        'today': today.isoformat(),
        'stores': stores_output,
    }

    with open(OUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nWritten: {OUT_FILE}")


if __name__ == '__main__':
    main()
