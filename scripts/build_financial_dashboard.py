"""Financial P&L Dashboard — Woof Gang Port Washington

Generates a standalone HTML dashboard with monthly and YTD P&L:
  Revenue (grooming + retail) → COGS → Gross Profit → OpEx → Net Margin
"""
import json, sys, os
from pathlib import Path
from collections import defaultdict
from datetime import date, timedelta
import calendar as _cal

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    get_store, GUARANTEES, COMMISSION_RATE, EXCLUDE_EMPLOYEES as EXCLUDE,
    BATHER_RATE, RETAIL_RATES, RETAIL_NAME_MAP, BATHER_NAME_MAP,
    HICKSVILLE_RETAIL_RATES, HICKSVILLE_RETAIL_NAME_MAP, HICKSVILLE_BATHER_NAME_MAP,
    MANAGER_SALARY_OLD, MANAGER_SALARY_NEW, MANAGER_RAISE_DATE,
    MANAGER_BONUS_DATE, MANAGER_BONUS, MANAGER_START,
    get_royalty_rate, get_monthly_rent,
)
from formatting import fc

SCRIPTS_DIR = Path(__file__).parent
_store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = "Port Washington" if _store_name == "port-washington" else "Hicksville"
_home_url = "../index.html"
_other_store = "Hicksville" if _store_name == "port-washington" else "Port Washington"
_other_dir = "../hicksville" if _store_name == "port-washington" else "../port-washington"
_other_fn = "Hicksville" if _store_name == "port-washington" else "PortWashington"
_switch_url = f"{_other_dir}/WoofGang_{_other_fn}_Financial_Dashboard.html"
DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir
TODAY = date.today()

# Store-specific financial constants
from config import STORE_RENT, STORE_OPEN_DATES
MONTHLY_RENT = STORE_RENT.get(_store_name, 7700.0)
STORE_OPEN = STORE_OPEN_DATES.get(_store_name, date(2024, 9, 26))

# Store-specific employee maps
if _store_name == "hicksville":
    RETAIL_NAME_MAP = HICKSVILLE_RETAIL_NAME_MAP
    RETAIL_RATES = HICKSVILLE_RETAIL_RATES
    BATHER_NAME_MAP = HICKSVILLE_BATHER_NAME_MAP
DAILY_RENT = MONTHLY_RENT * 12 / 365

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_hours_from_clocks(clocks, name_map, period_start, period_end):
    hours = {}
    for c in clocks:
        full_name = c.get("EmployeeName", "")
        short_name = name_map.get(full_name)
        if not short_name:
            continue
        time_in = (c.get("TimeIn") or "")[:10]
        if period_start <= time_in <= period_end:
            hrs = c.get("TotalTimeClockHoursDecimal") or 0
            hours[short_name] = hours.get(short_name, 0) + hrs
    return {k: round(v, 2) for k, v in hours.items()}


def manager_salary_for_range(start, end):
    if end < MANAGER_START:
        return 0.0
    effective_start = max(start, MANAGER_START)
    total = 0.0
    bonus = MANAGER_BONUS if start <= MANAGER_BONUS_DATE <= end else 0.0
    for n in range((end - effective_start).days + 1):
        d = effective_start + timedelta(days=n)
        if d.weekday() >= 5:
            continue
        rate = MANAGER_SALARY_NEW if d >= MANAGER_RAISE_DATE else MANAGER_SALARY_OLD
        total += rate / 260
    return round(total + bonus, 2)


def get_guarantee(groomer, day_str):
    g = GUARANTEES.get(groomer)
    if not g:
        return 0
    rate, start, end = g
    if start <= day_str <= end:
        return rate
    return 0


# ── Load data ────────────────────────────────────────────────────────────────
with open(DATA_DIR / "all_data.json") as f:
    data = json.load(f)

# Classify items and build daily aggregates
groom_rev_by_day = defaultdict(float)
groom_disc_by_day_total = defaultdict(float)
retail_rev_by_day = defaultdict(float)
retail_cogs_by_day = defaultdict(float)
groom_by_groomer_day = defaultdict(lambda: defaultdict(float))
tips_by_day_total = defaultdict(float)

for item in data["order_items"]:
    sku = str(item.get("Sku", ""))
    person = item.get("SalesPerson", "") or "Unknown"
    price = float(item.get("Price") or 0)
    qty = float(item.get("Quantity") or 0)
    disc = float(item.get("Discount") or 0)
    cost = float(item.get("Cost") or 0)
    day = (item.get("CreatedOn") or "")[:10]
    net = price * qty - disc

    is_groom = (
        sku.startswith("987") or sku.startswith("543") or sku.startswith("765")
        or sku.startswith("432") or sku.startswith("321")
        or sku.startswith("INTERNET-703") or sku == "002" or sku == "991674465"
    )

    if is_groom:
        groom_rev_by_day[day] += price * qty
        if disc > 0:
            groom_disc_by_day_total[day] += disc
        if person not in EXCLUDE:
            groom_by_groomer_day[person][day] += price * qty
        elif person in {"Jessica G", "Angela R"}:
            pass  # bather revenue included in groom_rev_by_day
    elif qty > 0 and net > 0:
        # Retail item
        retail_rev_by_day[day] += net
        item_cogs = cost * qty if cost > 0 else net * 0.50
        retail_cogs_by_day[day] += item_cogs

# Tips from orders
for o in data["orders"]:
    day = (o.get("CreatedOn") or "")[:10]
    tip = float(o.get("Tips") or 0)
    if tip > 0:
        tips_by_day_total[day] += tip

# Groomer commission (same logic as commission dashboard)
groomers = sorted(g for g in groom_by_groomer_day.keys() if not g.startswith("_"))


def groomer_commission_for_range(start_str, end_str):
    """Total commission paid to groomers in date range (including guarantees)."""
    total_paid = 0.0
    for g in groomers:
        for day, rev in groom_by_groomer_day[g].items():
            if start_str <= day <= end_str:
                comm = rev * COMMISSION_RATE
                guar = get_guarantee(g, day)
                paid = max(comm, guar) if guar else comm
                total_paid += paid
    return round(total_paid, 2)


# ── Monthly P&L ──────────────────────────────────────────────────────────────
all_rev_days = set(list(groom_rev_by_day.keys()) + list(retail_rev_by_day.keys()))
monthly = []
m_start = date(2024, 9, 1) if STORE_OPEN.year <= 2024 else STORE_OPEN.replace(day=1)

while m_start <= TODAY:
    m_end = date(m_start.year, m_start.month, _cal.monthrange(m_start.year, m_start.month)[1])
    m_end = min(m_end, TODAY)
    s, e = m_start.isoformat(), m_end.isoformat()

    groom_rev = round(sum(v for d, v in groom_rev_by_day.items() if s <= d <= e), 2)
    groom_disc = round(sum(v for d, v in groom_disc_by_day_total.items() if s <= d <= e), 2)
    retail_rev = round(sum(v for d, v in retail_rev_by_day.items() if s <= d <= e), 2)
    retail_cogs = round(sum(v for d, v in retail_cogs_by_day.items() if s <= d <= e), 2)
    total_rev = groom_rev + retail_rev
    net_rev = total_rev - groom_disc
    tips = round(sum(v for d, v in tips_by_day_total.items() if s <= d <= e), 2)

    comm_paid = groomer_commission_for_range(s, e)
    mgr = manager_salary_for_range(m_start, m_end) if _store_name == "port-washington" else 0.0
    bather_hrs = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, s, e)
    bather_pay = round(sum(h * BATHER_RATE for h in bather_hrs.values()), 2)
    retail_hrs = get_hours_from_clocks(data.get("time_clocks", []), RETAIL_NAME_MAP, s, e)
    retail_pay = round(sum(h * RETAIL_RATES.get(name, 0) for name, h in retail_hrs.items()), 2)
    _royalty_rate = get_royalty_rate(_store_name, m_start)
    royalties = round(total_rev * _royalty_rate, 2)
    rent = get_monthly_rent(_store_name, m_start)

    gross_profit = round(net_rev - retail_cogs - comm_paid, 2)
    total_opex = round(mgr + bather_pay + retail_pay + royalties + rent, 2)
    net_margin = round(gross_profit - total_opex, 2)
    net_margin_pct = round(net_margin / total_rev * 100, 1) if total_rev else 0

    # Derived ratios per month
    m_comm_pct = round(comm_paid / groom_rev * 100, 1) if groom_rev else 0
    m_retail_margin_pct = round((retail_rev - retail_cogs) / retail_rev * 100, 1) if retail_rev else 0
    m_labor = comm_paid + mgr + bather_pay + retail_pay
    m_labor_pct = round(m_labor / total_rev * 100, 1) if total_rev else 0
    m_rent_pct = round(rent / total_rev * 100, 1) if total_rev else 0
    m_gross_margin_pct = round(gross_profit / total_rev * 100, 1) if total_rev else 0
    # Days open in this month
    m_days_open = len([d for d in all_rev_days if s <= d <= e])

    monthly.append({
        "label": m_start.strftime("%b %Y"),
        "year": m_start.year,
        "groom_rev": groom_rev,
        "retail_rev": retail_rev,
        "total_rev": total_rev,
        "groom_disc": groom_disc,
        "net_rev": net_rev,
        "retail_cogs": retail_cogs,
        "comm_paid": comm_paid,
        "gross_profit": gross_profit,
        "mgr": mgr,
        "bather_pay": bather_pay,
        "retail_pay": retail_pay,
        "royalties": royalties,
        "rent": rent,
        "total_opex": total_opex,
        "net_margin": net_margin,
        "net_margin_pct": net_margin_pct,
        "tips": tips,
        "comm_pct": m_comm_pct,
        "retail_margin_pct": m_retail_margin_pct,
        "labor_pct": m_labor_pct,
        "rent_pct": m_rent_pct,
        "gross_margin_pct": m_gross_margin_pct,
        "days_open": m_days_open,
        "rev_per_day": round(total_rev / m_days_open, 2) if m_days_open else 0,
    })

    if m_start.month == 12:
        m_start = date(m_start.year + 1, 1, 1)
    else:
        m_start = date(m_start.year, m_start.month + 1, 1)

# ── YTD aggregation ──────────────────────────────────────────────────────────
ytd = [m for m in monthly if m["year"] == 2026]
ytd_sums = {}
for key in ["groom_rev", "retail_rev", "total_rev", "groom_disc", "net_rev",
            "retail_cogs", "comm_paid", "gross_profit", "mgr", "bather_pay",
            "retail_pay", "royalties", "rent", "total_opex", "net_margin", "tips"]:
    ytd_sums[key] = round(sum(m[key] for m in ytd), 2)
ytd_sums["net_margin_pct"] = round(ytd_sums["net_margin"] / ytd_sums["total_rev"] * 100, 1) if ytd_sums["total_rev"] else 0
ytd_sums["gross_margin_pct"] = round(ytd_sums["gross_profit"] / ytd_sums["total_rev"] * 100, 1) if ytd_sums["total_rev"] else 0

# Derived ratios
ytd_sums["comm_pct"] = round(ytd_sums["comm_paid"] / ytd_sums["groom_rev"] * 100, 1) if ytd_sums["groom_rev"] else 0
ytd_sums["retail_margin_pct"] = round((ytd_sums["retail_rev"] - ytd_sums["retail_cogs"]) / ytd_sums["retail_rev"] * 100, 1) if ytd_sums["retail_rev"] else 0
total_labor = ytd_sums["comm_paid"] + ytd_sums["mgr"] + ytd_sums["bather_pay"] + ytd_sums["retail_pay"]
ytd_sums["labor_pct"] = round(total_labor / ytd_sums["total_rev"] * 100, 1) if ytd_sums["total_rev"] else 0
ytd_sums["rent_pct"] = round(ytd_sums["rent"] / ytd_sums["total_rev"] * 100, 1) if ytd_sums["total_rev"] else 0
# Count days open YTD
ytd_days_open = len([d for d in all_rev_days if "2026-01-01" <= d <= TODAY.isoformat()])
ytd_sums["rev_per_day"] = round(ytd_sums["total_rev"] / ytd_days_open, 2) if ytd_days_open else 0

# ── Insights ─────────────────────────────────────────────────────────────────
insights = []
# Need at least 4 months of data for meaningful comparisons
if len(monthly) >= 4:
    recent = monthly[-1]  # most recent month (may be partial)
    # Use second-to-last if current month has < 20 days of data
    if recent == monthly[-1] and len(monthly) >= 2:
        m_end_check = date(TODAY.year, TODAY.month, 1)
        if (TODAY - m_end_check).days < 20 and len(monthly) >= 2:
            recent = monthly[-2]
    # 3-month average (excluding current partial month)
    avg_months = monthly[-4:-1] if len(monthly) >= 4 else monthly[:-1]
    if avg_months:
        avg = {}
        for key in ["groom_rev", "retail_rev", "total_rev", "retail_cogs", "comm_paid",
                     "mgr", "bather_pay", "retail_pay", "royalties", "rent", "net_margin"]:
            avg[key] = round(sum(m[key] for m in avg_months) / len(avg_months), 2)

        # Cost change insights
        cost_items = [
            ("comm_paid", "Groomer Commission"),
            ("retail_cogs", "Retail COGS"),
            ("bather_pay", "Bather Pay"),
            ("retail_pay", "Retail Staff Pay"),
            ("mgr", "Manager Salary"),
            ("royalties", "Royalties"),
        ]
        for key, label in cost_items:
            if avg[key] > 0:
                pct_change = round((recent[key] - avg[key]) / avg[key] * 100, 1)
                if pct_change > 10:
                    insights.append(("red", f"{label} up {pct_change}% vs 3-month avg ({fc(avg[key])} → {fc(recent[key])})"))
                elif pct_change < -10:
                    insights.append(("green", f"{label} down {abs(pct_change)}% vs 3-month avg ({fc(avg[key])} → {fc(recent[key])})"))

        # Revenue insights
        if avg["total_rev"] > 0:
            rev_change = round((recent["total_rev"] - avg["total_rev"]) / avg["total_rev"] * 100, 1)
            if rev_change > 5:
                insights.append(("green", f"Revenue up {rev_change}% vs 3-month avg ({fc(avg['total_rev'])} → {fc(recent['total_rev'])})"))
            elif rev_change < -5:
                insights.append(("red", f"Revenue down {abs(rev_change)}% vs 3-month avg ({fc(avg['total_rev'])} → {fc(recent['total_rev'])})"))

        # Margin trend
        if avg["net_margin"] > 0 and recent["net_margin"] > avg["net_margin"] * 1.1:
            insights.append(("green", f"Net margin improving — {fc(recent['net_margin'])} vs {fc(avg['net_margin'])} avg"))

    # Ratio-based opportunities
    r = recent
    if r["groom_rev"] > 0:
        comm_ratio = r["comm_paid"] / r["groom_rev"] * 100
        if comm_ratio > 55:
            insights.append(("blue", f"Commission ratio at {comm_ratio:.0f}% of grooming revenue — check guarantee usage"))
    if r["retail_rev"] > 0:
        retail_margin = (r["retail_rev"] - r["retail_cogs"]) / r["retail_rev"] * 100
        if retail_margin < 40:
            insights.append(("blue", f"Retail margin at {retail_margin:.0f}% — review product pricing or vendor costs"))
    if r["total_rev"] > 0:
        rent_ratio = r["rent"] / r["total_rev"] * 100
        if rent_ratio > 15:
            insights.append(("blue", f"Rent is {rent_ratio:.0f}% of revenue — grow revenue to improve fixed cost leverage"))
        labor_total = r["comm_paid"] + r["mgr"] + r["bather_pay"] + r["retail_pay"]
        labor_ratio = labor_total / r["total_rev"] * 100
        if labor_ratio > 60:
            insights.append(("blue", f"Total labor at {labor_ratio:.0f}% of revenue — review staffing efficiency"))

# Build insights HTML
insights_html = ""
if insights:
    for color, text in insights:
        bg = {"red": "#fce4ec", "green": "#e8f5e9", "blue": "#e3f2fd"}[color]
        fg = {"red": "#c62828", "green": "#2E7D32", "blue": "#1565C0"}[color]
        icon = {"red": "📈", "green": "📉", "blue": "💡"}[color]
        insights_html += f'<div style="background:{bg};color:{fg};padding:10px 16px;border-radius:10px;font-size:0.88rem;font-weight:500;display:flex;align-items:center;gap:8px">{icon} {text}</div>\n'
else:
    insights_html = '<div style="color:#999;font-size:0.88rem">Not enough data for trend analysis yet.</div>'

# ── JSON for JS ──────────────────────────────────────────────────────────────
import json as _j
monthly_json = _j.dumps(monthly)

# ── HTML ─────────────────────────────────────────────────────────────────────
from datetime import datetime as _dt
NOW_STR = _dt.now().strftime("%B %d, %Y at %I:%M %p ET").replace(" 0", " ")

def kpi(label, value, color="#C4276E", sub=""):
    sub_html = f'<div style="font-size:0.78rem;color:{color};margin-top:3px;font-weight:600">{sub}</div>' if sub else ""
    return f'<div class="kpi" style="border-color:{color}"><div class="kpi-val" style="color:{color}">{value}</div><div class="kpi-label">{label}</div>{sub_html}</div>'

def fp(n):
    """Format percentage."""
    return f"{n:.1f}%"

# Monthly table rows
table_rows = ""
for m in monthly:
    mc = "#e53935" if m["net_margin"] < 0 else "#2E7D32"
    table_rows += f'''<tr style="border-bottom:1px solid #f0ede8">
      <td style="font-weight:600;padding:8px 10px">{m["label"]}</td>
      <td class="n">{fc(m["groom_rev"])}</td>
      <td class="n">{fc(m["retail_rev"])}</td>
      <td class="n" style="font-weight:600">{fc(m["total_rev"])}</td>
      <td class="n" style="color:#e53935">-{fc(m["groom_disc"])}</td>
      <td class="n">{fc(m["retail_cogs"])}</td>
      <td class="n">{fc(m["comm_paid"])}</td>
      <td class="n" style="font-weight:600">{fc(m["gross_profit"])}</td>
      <td class="n">{fc(m["mgr"])}</td>
      <td class="n">{fc(m["bather_pay"])}</td>
      <td class="n">{fc(m["retail_pay"])}</td>
      <td class="n">{fc(m["royalties"])}</td>
      <td class="n">{fc(m["rent"])}</td>
      <td class="n" style="font-weight:700;color:{mc}">{fc(m["net_margin"])}<br><span style="font-size:0.72rem;font-weight:400">{m["net_margin_pct"]}%</span></td>
    </tr>'''

# Month options for comparison dropdowns
month_options_a = "\n".join(f'<option value="{i}">{m["label"]}</option>' for i, m in enumerate(monthly))
month_options_b = "\n".join(f'<option value="{i}"{"selected" if i == len(monthly)-1 else ""}>{m["label"]}</option>' for i, m in enumerate(monthly))
default_a = max(0, len(monthly) - 2)

# Comparison KPI definitions
CMP_KPIS = [
    ("total_rev", "Total Revenue", True),
    ("groom_rev", "Grooming Revenue", True),
    ("retail_rev", "Retail Revenue", True),
    ("groom_disc", "Discounts", False),
    ("retail_cogs", "Retail COGS", False),
    ("comm_paid", "Commission", False),
    ("gross_profit", "Gross Profit", True),
    ("mgr", "Manager", False),
    ("bather_pay", "Bather Pay", False),
    ("retail_pay", "Retail Staff", False),
    ("royalties", "Royalties", False),
    ("rent", "Rent", False),
    ("net_margin", "Net Margin", True),
]

# Build comparison KPI HTML slots
cmp_kpis_html = ""
for key, label, up_is_good in CMP_KPIS:
    cmp_kpis_html += f'''<div class="cmp-row" id="cmp-{key}">
      <div class="cmp-label">{label}</div>
      <div class="cmp-vals">
        <div class="cmp-a" id="cmp-{key}-a"></div>
        <div class="cmp-delta" id="cmp-{key}-d"></div>
        <div class="cmp-b" id="cmp-{key}-b"></div>
      </div>
    </div>\n'''

html = f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Woof Gang {_store_display} — Financial Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f4f0;color:#1a1a2e}}
.topbar{{background:#C4276E;padding:18px 32px;color:white;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3);display:flex;justify-content:space-between;align-items:center}}
.topbar-title{{font-size:1.1rem;font-weight:700}}
.topbar-sub{{color:rgba(255,255,255,0.75);font-size:0.82rem;margin-top:2px}}
.topbar-time{{font-size:0.78rem;opacity:0.8}}
.tabs{{background:white;border-bottom:2px solid #eee;position:sticky;top:56px;z-index:99;display:flex;padding:0 24px;flex-wrap:wrap}}
.tab{{padding:14px 20px;border:none;background:transparent;color:#999;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;font-family:inherit;white-space:nowrap}}
.tab.active{{color:#C4276E;border-bottom-color:#C4276E}}
.page{{max-width:1400px;margin:0 auto;padding:28px 24px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{background:white;border-radius:12px;padding:16px;text-align:center;border-top:3px solid #C4276E;box-shadow:0 1px 4px rgba(0,0,0,0.05)}}
.kpi-val{{font-size:1.5rem;font-weight:700}}
.kpi-label{{font-size:0.73rem;color:#999;margin-top:5px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em}}
.card{{background:white;border-radius:14px;padding:22px;margin-bottom:22px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.stitle{{font-size:1.05rem;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.stitle::before{{content:'';display:inline-block;width:4px;height:18px;background:#C4276E;border-radius:2px}}
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:0.82rem}}
th{{text-align:right;padding:8px 10px;color:#888;font-weight:600;border-bottom:2px solid #eee;white-space:nowrap}}
th:first-child{{text-align:left}}
td.n{{text-align:right;padding:8px 10px;font-family:'DM Mono',monospace;font-size:0.8rem}}
tr:hover td{{background:#fdf0f5}}
thead th{{position:sticky;top:0;background:#f8f7f4;z-index:5}}
.panel{{display:none}}.panel.active{{display:block}}
.home-link{{color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.82rem;font-weight:600}}
.home-link:hover{{color:white}}
/* Comparison styles */
.cmp-controls{{display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
.cmp-controls select{{padding:8px 12px;border:1px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.84rem}}
.cmp-controls label{{font-size:0.73rem;color:#999;font-weight:600;text-transform:uppercase}}
.quick-btns{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.quick-btn{{padding:6px 14px;border:1.5px solid #C4276E;border-radius:20px;background:transparent;color:#C4276E;font-size:0.8rem;font-weight:600;cursor:pointer;transition:all 0.2s;font-family:inherit}}
.quick-btn:hover{{background:#C4276E;color:white}}
.cmp-row{{display:flex;align-items:center;padding:10px 0;border-bottom:1px solid #f0ede8}}
.cmp-label{{width:140px;font-size:0.82rem;font-weight:600;color:#555;flex-shrink:0}}
.cmp-vals{{display:flex;flex:1;align-items:center;gap:8px}}
.cmp-a,.cmp-b{{flex:1;text-align:center;font-family:'DM Mono',monospace;font-size:0.95rem;font-weight:600}}
.cmp-delta{{flex:1;text-align:center;font-size:0.82rem;font-weight:700;border-radius:8px;padding:4px 8px}}
.cmp-delta.pos{{color:#2E7D32;background:#e8f5e9}}.cmp-delta.neg{{color:#e53935;background:#fce4ec}}
.cmp-hdr{{display:flex;align-items:center;padding:8px 0;border-bottom:2px solid #eee;margin-bottom:4px}}
.cmp-hdr span{{flex:1;text-align:center;font-size:0.73rem;color:#999;font-weight:600;text-transform:uppercase}}
.cmp-hdr span:first-child{{width:140px;flex:none}}
/* Header */
.header{{background:linear-gradient(135deg,#1B6B6B 0%,#6B3520 100%);color:white;padding:40px 0 30px;text-align:center;position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(196,39,110,0.15) 0%,transparent 50%)}}
.header h1{{font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px}}
.header .subtitle{{font-size:1rem;font-weight:400;opacity:0.9;position:relative}}
.header .brand-tag{{display:inline-block;background:#C4276E;color:white;padding:4px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-top:12px;position:relative}}
.header-timestamp{{position:absolute;top:12px;right:20px;font-size:0.78rem;opacity:0.85;font-weight:400;z-index:1}}
/* Insights */
.insight-grid{{display:flex;flex-direction:column;gap:8px}}
</style>
</head><body>

<div class="header">
    <div class="header-timestamp">Updated {NOW_STR}<br><a href="{_switch_url}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.78rem">&#x21C4; {_other_store}</a></div>
    <h1>Woof Gang {_store_display}</h1>
    <div class="subtitle">Financial Dashboard</div>
    <div class="brand-tag">Woof Gang Bakery &amp; Grooming</div>
</div>

<div class="topbar">
  <a href="{_home_url}" class="home-link">&larr; Home</a>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('ytd',this)">2026 YTD</button>
  <button class="tab" onclick="showTab('monthly',this)">Monthly Detail</button>
  <button class="tab" onclick="showTab('compare',this)">Monthly Compare</button>
</div>

<div class="page">

<!-- ── YTD ── -->
<div class="panel active" id="panel-ytd">
  <div class="kpi-grid">
    {kpi("Grooming Revenue", fc(ytd_sums["groom_rev"]), "#C4276E")}
    {kpi("Retail Revenue", fc(ytd_sums["retail_rev"]), "#1B6B6B")}
    {kpi("Total Revenue", fc(ytd_sums["total_rev"]), "#1a1a2e")}
    {kpi("Grooming Discounts", f'-{fc(ytd_sums["groom_disc"])}', "#e53935")}
    {kpi("Retail COGS", fc(ytd_sums["retail_cogs"]), "#e65100")}
    {kpi("Groomer Commission", fc(ytd_sums["comm_paid"]), "#1565C0")}
    {kpi("Gross Profit", fc(ytd_sums["gross_profit"]), "#2E7D32", f'{ytd_sums["gross_margin_pct"]}%')}
    {kpi("Manager Salary", fc(ytd_sums["mgr"]), "#5C6BC0")}
    {kpi("Bather Pay", fc(ytd_sums["bather_pay"]), "#00796B")}
    {kpi("Retail Staff Pay", fc(ytd_sums["retail_pay"]), "#6A1B9A")}
    {kpi("Royalties + Marketing", fc(ytd_sums["royalties"]), "#AD1457")}
    {kpi("Rent", fc(ytd_sums["rent"]), "#6D4C41")}
    {kpi("Net Margin", fc(ytd_sums["net_margin"]), "#2E7D32" if ytd_sums["net_margin"] >= 0 else "#e53935", f'{ytd_sums["net_margin_pct"]}%')}
    {kpi("Tips Collected", fc(ytd_sums["tips"]), "#F57C00")}
  </div>

  <div class="card">
    <div class="stitle">Key Ratios</div>
    <div class="kpi-grid">
      {kpi("Commission %", fp(ytd_sums["comm_pct"]), "#1565C0", "of grooming rev")}
      {kpi("Retail Margin %", fp(ytd_sums["retail_margin_pct"]), "#1B6B6B", "after COGS")}
      {kpi("Labor Cost %", fp(ytd_sums["labor_pct"]), "#7B1FA2", "of total rev")}
      {kpi("Rent % of Rev", fp(ytd_sums["rent_pct"]), "#6D4C41", "fixed cost")}
      {kpi("Rev per Day", fc(ytd_sums["rev_per_day"]), "#C4276E", f'{ytd_days_open} days open')}
      {kpi("Gross Margin %", fp(ytd_sums["gross_margin_pct"]), "#2E7D32", "before OpEx")}
    </div>
  </div>

  <div class="card">
    <div class="stitle">Trends &amp; Insights</div>
    <div class="insight-grid">
      {insights_html}
    </div>
  </div>

  <div class="card">
    <div class="stitle">Cost Breakdown</div>
    <div style="max-width:500px;margin:0 auto"><canvas id="chart-costs"></canvas></div>
  </div>

  <div class="card">
    <div class="stitle">P&amp;L Waterfall</div>
    <div style="max-width:800px;margin:0 auto"><canvas id="chart-waterfall" height="300"></canvas></div>
  </div>
</div>

<!-- ── Monthly Detail ── -->
<div class="panel" id="panel-monthly">
  <div class="card">
    <div class="stitle">Monthly Snapshot</div>
    <div style="margin-bottom:16px">
      <label style="font-size:0.73rem;color:#999;font-weight:600;text-transform:uppercase">Select Month</label><br>
      <select id="month-detail-sel" onchange="updateMonthDetail()" style="padding:8px 12px;border:1px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.84rem;margin-top:4px">
        {month_options_b}
      </select>
    </div>
    <div class="kpi-grid" id="month-detail-kpis"></div>
    <div class="stitle" style="margin-top:8px">Key Ratios</div>
    <div class="kpi-grid" id="month-detail-ratios"></div>
  </div>

  <div class="card">
    <div class="stitle">Monthly P&amp;L</div>
    <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th style="text-align:left">Month</th>
        <th>Groom Rev</th><th>Retail Rev</th><th>Total Rev</th>
        <th>Discounts</th><th>Retail COGS</th><th>Commission</th><th>Gross Profit</th>
        <th>Manager</th><th>Bather</th><th>Retail Staff</th><th>Royalties</th><th>Rent</th>
        <th>Net Margin</th>
      </tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <div class="stitle">Revenue vs Net Margin by Month</div>
    <canvas id="chart-monthly" height="280"></canvas>
  </div>
</div>

<!-- ── Monthly Compare ── -->
<div class="panel" id="panel-compare">
  <div class="card">
    <div class="stitle">Month-to-Month P&amp;L Comparison</div>
    <div class="quick-btns">
      <button class="quick-btn" onclick="qcPrev()">vs Last Month</button>
      <button class="quick-btn" onclick="qcYoY()">vs Same Month Last Year</button>
    </div>
    <div class="cmp-controls">
      <div><label>Period A</label><br><select id="cmp-sel-a" onchange="updateCompare()">{month_options_a}</select></div>
      <div style="font-weight:700;color:#999;padding-top:14px">vs</div>
      <div><label>Period B</label><br><select id="cmp-sel-b" onchange="updateCompare()">{month_options_b}</select></div>
    </div>
    <div class="cmp-hdr">
      <span style="text-align:left">Metric</span>
      <span id="cmp-hdr-a">Period A</span>
      <span>Change</span>
      <span id="cmp-hdr-b">Period B</span>
    </div>
    {cmp_kpis_html}
  </div>

  <div class="card">
    <div class="stitle">Side-by-Side Comparison</div>
    <canvas id="chart-compare" height="300"></canvas>
  </div>
</div>

</div>

<div style="text-align:center;padding:24px;font-size:0.8rem;color:#999">
  <strong style="color:#C4276E">Woof Gang Bakery &amp; Grooming</strong> — {_store_display}, NY &middot; Financial Dashboard
</div>

<script>
var DATA = {monthly_json};

function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.remove('active'); }});
  document.getElementById('panel-' + id).classList.add('active');
  btn.classList.add('active');
  if (id === 'compare') updateCompare();
  if (id === 'monthly') updateMonthDetail();
}}

function fc(n) {{ return '$' + Math.round(n).toLocaleString(); }}

// ── Comparison logic ──
var CMP_KEYS = {_j.dumps([(k, label, up_is_good) for k, label, up_is_good in CMP_KPIS])};
var cmpChartRef = null;

function updateCompare() {{
  var iA = parseInt(document.getElementById('cmp-sel-a').value);
  var iB = parseInt(document.getElementById('cmp-sel-b').value);
  var a = DATA[iA], b = DATA[iB];
  if (!a || !b) return;
  document.getElementById('cmp-hdr-a').textContent = a.label;
  document.getElementById('cmp-hdr-b').textContent = b.label;

  CMP_KEYS.forEach(function(def) {{
    var key = def[0], upGood = def[2];
    var vA = a[key] || 0, vB = b[key] || 0;
    var delta = vB - vA;
    var pct = vA !== 0 ? ((vB - vA) / Math.abs(vA) * 100) : 0;
    document.getElementById('cmp-' + key + '-a').textContent = fc(vA);
    document.getElementById('cmp-' + key + '-b').textContent = fc(vB);
    var dEl = document.getElementById('cmp-' + key + '-d');
    var sign = delta >= 0 ? '+' : '';
    dEl.textContent = sign + fc(delta) + ' (' + sign + pct.toFixed(1) + '%)';
    // For costs, down is good. For revenue/profit, up is good.
    var isGood = upGood ? delta >= 0 : delta <= 0;
    dEl.className = 'cmp-delta ' + (delta === 0 ? '' : isGood ? 'pos' : 'neg');
  }});

  // Bar chart
  if (cmpChartRef) cmpChartRef.destroy();
  var chartKeys = ['total_rev','groom_rev','retail_rev','gross_profit','net_margin'];
  var chartLabels = ['Total Rev','Groom Rev','Retail Rev','Gross Profit','Net Margin'];
  cmpChartRef = new Chart(document.getElementById('chart-compare'), {{
    type: 'bar',
    data: {{
      labels: chartLabels,
      datasets: [
        {{ label: a.label, data: chartKeys.map(function(k) {{ return a[k] || 0; }}), backgroundColor: 'rgba(196,39,110,0.7)' }},
        {{ label: b.label, data: chartKeys.map(function(k) {{ return b[k] || 0; }}), backgroundColor: 'rgba(27,107,107,0.7)' }}
      ]
    }},
    options: {{
      plugins: {{
        legend: {{ labels: {{ font: {{ family: "'DM Sans'" }} }} }},
        tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.dataset.label + ': ' + fc(ctx.raw); }} }} }}
      }},
      scales: {{
        x: {{ grid: {{ display: false }} }},
        y: {{ ticks: {{ callback: function(v) {{ return fc(v); }} }} }}
      }}
    }}
  }});
}}

function qcPrev() {{
  var sel = document.getElementById('cmp-sel-b');
  var i = parseInt(sel.value);
  if (i > 0) {{
    document.getElementById('cmp-sel-a').value = i - 1;
    updateCompare();
  }}
}}

function qcYoY() {{
  var sel = document.getElementById('cmp-sel-b');
  var i = parseInt(sel.value);
  var target = DATA[i].label.replace(/\\d{{4}}/, function(y) {{ return parseInt(y) - 1; }});
  for (var j = 0; j < DATA.length; j++) {{
    if (DATA[j].label === target) {{
      document.getElementById('cmp-sel-a').value = j;
      updateCompare();
      return;
    }}
  }}
}}

// Set default selection
document.getElementById('cmp-sel-a').value = {default_a};

// ── Monthly Detail KPIs ──
function updateMonthDetail() {{
  var i = parseInt(document.getElementById('month-detail-sel').value);
  var m = DATA[i];
  if (!m) return;

  function kpiHtml(label, val, color, sub) {{
    var subH = sub ? '<div style="font-size:0.78rem;color:'+color+';margin-top:3px;font-weight:600">'+sub+'</div>' : '';
    return '<div class="kpi" style="border-color:'+color+'"><div class="kpi-val" style="color:'+color+'">'+val+'</div><div class="kpi-label">'+label+'</div>'+subH+'</div>';
  }}
  function fp(n) {{ return n.toFixed(1) + '%'; }}
  var mc = m.net_margin >= 0 ? '#2E7D32' : '#e53935';

  document.getElementById('month-detail-kpis').innerHTML =
    kpiHtml('Grooming Revenue', fc(m.groom_rev), '#C4276E', '') +
    kpiHtml('Retail Revenue', fc(m.retail_rev), '#1B6B6B', '') +
    kpiHtml('Total Revenue', fc(m.total_rev), '#1a1a2e', '') +
    kpiHtml('Retail COGS', fc(m.retail_cogs), '#e65100', '') +
    kpiHtml('Commission', fc(m.comm_paid), '#1565C0', '') +
    kpiHtml('Gross Profit', fc(m.gross_profit), '#2E7D32', fp(m.gross_margin_pct)) +
    kpiHtml('Total OpEx', fc(m.total_opex), '#7B1FA2', '') +
    kpiHtml('Net Margin', fc(m.net_margin), mc, fp(m.net_margin_pct)) +
    kpiHtml('Tips', fc(m.tips), '#F57C00', '');

  document.getElementById('month-detail-ratios').innerHTML =
    kpiHtml('Commission %', fp(m.comm_pct), '#1565C0', 'of groom rev') +
    kpiHtml('Retail Margin %', fp(m.retail_margin_pct), '#1B6B6B', 'after COGS') +
    kpiHtml('Labor Cost %', fp(m.labor_pct), '#7B1FA2', 'of total rev') +
    kpiHtml('Rent % of Rev', fp(m.rent_pct), '#6D4C41', 'fixed cost') +
    kpiHtml('Rev per Day', fc(m.rev_per_day), '#C4276E', (m.days_open || 0) + ' days') +
    kpiHtml('Gross Margin %', fp(m.gross_margin_pct), '#2E7D32', 'before OpEx');
}}

// Initialize monthly detail on load
updateMonthDetail();

// ── Cost breakdown donut ──
var costData = [
  {ytd_sums["comm_paid"]},
  {ytd_sums["retail_cogs"]},
  {ytd_sums["mgr"]},
  {ytd_sums["bather_pay"]},
  {ytd_sums["retail_pay"]},
  {ytd_sums["royalties"]},
  {ytd_sums["rent"]},
  {ytd_sums["groom_disc"]}
];
new Chart(document.getElementById('chart-costs'), {{
  type: 'doughnut',
  data: {{
    labels: ['Groomer Commission','Retail COGS','Manager','Bather Pay','Retail Staff','Royalties','Rent','Discounts'],
    datasets: [{{
      data: costData,
      backgroundColor: ['#1565C0','#e65100','#5C6BC0','#00796B','#6A1B9A','#AD1457','#6D4C41','#e53935']
    }}]
  }},
  options: {{
    plugins: {{
      legend: {{ position: 'right', labels: {{ font: {{ family: "'DM Sans'" }} }} }},
      tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.label + ': ' + fc(ctx.raw); }} }} }}
    }}
  }}
}});

// ── P&L waterfall ──
var wf = [
  {{ label: 'Groom Rev', val: {ytd_sums["groom_rev"]} }},
  {{ label: 'Retail Rev', val: {ytd_sums["retail_rev"]} }},
  {{ label: 'Discounts', val: -{ytd_sums["groom_disc"]} }},
  {{ label: 'Retail COGS', val: -{ytd_sums["retail_cogs"]} }},
  {{ label: 'Commission', val: -{ytd_sums["comm_paid"]} }},
  {{ label: 'Manager', val: -{ytd_sums["mgr"]} }},
  {{ label: 'Bather', val: -{ytd_sums["bather_pay"]} }},
  {{ label: 'Retail Staff', val: -{ytd_sums["retail_pay"]} }},
  {{ label: 'Royalties', val: -{ytd_sums["royalties"]} }},
  {{ label: 'Rent', val: -{ytd_sums["rent"]} }},
];
var running = 0;
var wfBases = [], wfVals = [], wfColors = [];
wf.forEach(function(w) {{
  if (w.val >= 0) {{
    wfBases.push(running);
    wfVals.push(w.val);
    wfColors.push('#2E7D32');
    running += w.val;
  }} else {{
    running += w.val;
    wfBases.push(running);
    wfVals.push(Math.abs(w.val));
    wfColors.push('#e53935');
  }}
}});
wfBases.push(0);
wfVals.push(running);
wfColors.push(running >= 0 ? '#1B6B6B' : '#e53935');
wf.push({{ label: 'Net Margin' }});

new Chart(document.getElementById('chart-waterfall'), {{
  type: 'bar',
  data: {{
    labels: wf.map(function(w) {{ return w.label; }}),
    datasets: [
      {{ data: wfBases, backgroundColor: 'transparent', stack: 's' }},
      {{ data: wfVals, backgroundColor: wfColors, stack: 's' }}
    ]
  }},
  options: {{
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: function(ctx) {{
            if (ctx.datasetIndex === 0) return '';
            var i = ctx.dataIndex;
            var total = wfBases[i] + wfVals[i];
            return ctx.label + ': ' + fc(wf[i] && wf[i].val !== undefined ? wf[i].val : total);
          }}
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ family: "'DM Sans'", size: 11 }} }} }},
      y: {{ ticks: {{ callback: function(v) {{ return fc(v); }}, font: {{ family: "'DM Mono'" }} }} }}
    }}
  }}
}});

// ── Monthly revenue vs margin chart ──
var d2025 = DATA.filter(function(m) {{ return m.year >= 2025; }});
new Chart(document.getElementById('chart-monthly'), {{
  type: 'bar',
  data: {{
    labels: d2025.map(function(m) {{ return m.label; }}),
    datasets: [
      {{ label: 'Total Revenue', data: d2025.map(function(m) {{ return m.total_rev; }}), backgroundColor: 'rgba(196,39,110,0.7)', order: 1 }},
      {{ label: 'Net Margin', data: d2025.map(function(m) {{ return m.net_margin; }}), backgroundColor: d2025.map(function(m) {{ return m.net_margin >= 0 ? 'rgba(27,107,107,0.8)' : 'rgba(229,57,53,0.8)'; }}), order: 0 }}
    ]
  }},
  options: {{
    plugins: {{
      legend: {{ labels: {{ font: {{ family: "'DM Sans'" }} }} }},
      tooltip: {{ callbacks: {{ label: function(ctx) {{ return ctx.dataset.label + ': ' + fc(ctx.raw); }} }} }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ family: "'DM Sans'", size: 11 }} }} }},
      y: {{ ticks: {{ callback: function(v) {{ return fc(v); }}, font: {{ family: "'DM Mono'" }} }} }}
    }}
  }}
}});
</script>
</body></html>
'''

_fn_store = _store_display.replace(" ", "")
out_path = OUTPUT_DIR / f"WoofGang_{_fn_store}_Financial_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"Months: {len(monthly)} | YTD Rev: {fc(ytd_sums['total_rev'])} | Net Margin: {fc(ytd_sums['net_margin'])} ({ytd_sums['net_margin_pct']}%)")
