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
)

OUT_FILE = PROJ_ROOT / "data" / "retail_staffing.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

# Retail staff + rates at PW (Casey's raise to $20 pending — we track actual history)
PW_RETAIL = {
    'Casey Makowski': {'current_rate': 19.00, 'role': 'retail'},
    'Christine Brower': {'current_rate': 20.00, 'role': 'retail'},
}

PW_OPEN = STORE_OPEN_DATES['port-washington']
CLOSURES = set(KNOWN_CLOSURES)


def parse_hours(ti, to):
    try:
        dt_in = datetime.fromisoformat(ti.replace('Z', ''))
        dt_out = datetime.fromisoformat(to.replace('Z', ''))
        h = (dt_out - dt_in).total_seconds() / 3600
        return h if 0 < h <= 16 else 0
    except Exception:
        return 0


def days_open_in_month(y, m, today):
    first_day = date(y, m, 1)
    last_day = date(y, m, monthrange(y, m)[1])
    if last_day > today:
        last_day = today
    if last_day < PW_OPEN:
        return 0, 0
    start_day = max(first_day, PW_OPEN)
    if start_day > last_day:
        return 0, 0
    open_count = 0
    closure_count = 0
    d = start_day
    while d <= last_day:
        if d.isoformat() in CLOSURES:
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


def build_pw_staffing():
    """Build monthly PW staffing data from June 2025 onward."""
    pw = STORES['port-washington']
    with open(pw.data_dir / 'all_data.json') as f:
        data = json.load(f)

    clocks = data.get('time_clocks', [])
    items = data.get('order_items', [])
    today = date.today()

    START = '2025-06'  # first month Casey ramped up

    # Monthly retail hours per person (PW clocks, filter to PW_RETAIL names)
    monthly_hours = defaultdict(lambda: defaultdict(float))
    for c in clocks:
        emp = (c.get('EmployeeName') or '').strip()
        ti = c.get('TimeIn', '')
        if not ti or emp not in PW_RETAIL:
            continue
        if ti[:7] < START:
            continue
        hrs = parse_hours(ti, c.get('TimeOut', ''))
        monthly_hours[ti[:7]][emp] += hrs

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

    # Assemble monthly rows
    months = sorted(monthly_hours.keys())
    rows = []
    for m in months:
        y, mo = int(m[:4]), int(m[5:7])
        casey_hrs = monthly_hours[m].get('Casey Makowski', 0)
        chris_hrs = monthly_hours[m].get('Christine Brower', 0)
        casey_cost = round(casey_hrs * PW_RETAIL['Casey Makowski']['current_rate'], 2)
        chris_cost = round(chris_hrs * PW_RETAIL['Christine Brower']['current_rate'], 2)
        retail_cost = round(casey_cost + chris_cost, 2)
        cindy_cost = manager_salary_for_month(y, mo, today, include_bonus=False)
        total_labor = round(retail_cost + cindy_cost, 2)
        days_open, closures = days_open_in_month(y, mo, today)
        sales = round(monthly_sales.get(m, 0), 2)
        labor_pct = round((total_labor / sales) * 100, 2) if sales else 0
        cost_per_day = round(total_labor / days_open, 2) if days_open else 0

        rows.append({
            'month': m,
            'month_label': datetime.strptime(m, '%Y-%m').strftime('%b %Y'),
            'casey_hrs': round(casey_hrs, 1),
            'casey_cost': casey_cost,
            'chris_hrs': round(chris_hrs, 1),
            'chris_cost': chris_cost,
            'retail_hrs': round(casey_hrs + chris_hrs, 1),
            'retail_cost': retail_cost,
            'cindy_cost': cindy_cost,
            'total_labor': total_labor,
            'sales': sales,
            'labor_pct_sales': labor_pct,
            'days_open': days_open,
            'closures': closures,
            'cost_per_open_day': cost_per_day,
        })

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

    # Current month snapshot (partial)
    current_row = next((r for r in rows if r['month'] == current_month), None)
    latest_complete = full_rows[-1] if full_rows else None

    return {
        'store': 'port-washington',
        'label': 'Port Washington',
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
        'rates': {
            'casey': PW_RETAIL['Casey Makowski']['current_rate'],
            'chris': PW_RETAIL['Christine Brower']['current_rate'],
            'cindy_annual': MANAGER_SALARY_NEW,
            'cindy_daily': round(MANAGER_SALARY_NEW / 365, 2),
        },
    }


def main():
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')

    print('Building PW retail staffing data...')
    pw_data = build_pw_staffing()

    output = {
        'generated_at': datetime.now(et).isoformat(),
        'today': date.today().isoformat(),
        'stores': {
            'port-washington': pw_data,
        },
    }

    with open(OUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"  PW months tracked: {len(pw_data['rows'])}")
    if pw_data['summary']['latest_complete_month']:
        l = pw_data['summary']['latest_complete_month']
        print(f"  Latest complete: {l['month_label']} — ${l['total_labor']:,.0f} labor ({l['labor_pct_sales']}% of sales)")
    if pw_data['summary']['best_month']:
        b = pw_data['summary']['best_month']
        print(f"  Best labor %: {b['label']} — {b['labor_pct']}%")
    if pw_data['summary']['worst_month']:
        w = pw_data['summary']['worst_month']
        print(f"  Worst labor %: {w['label']} — {w['labor_pct']}%")
    print(f"\nWritten: {OUT_FILE}")


if __name__ == '__main__':
    main()
