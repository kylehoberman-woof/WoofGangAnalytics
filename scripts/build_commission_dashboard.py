# v2.1
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    get_store, COMMISSION_RATE, EXCLUDE_EMPLOYEES as EXCLUDE,
    BATHER_RATE,
    MANAGER_SALARY_OLD, MANAGER_SALARY_NEW, MANAGER_RAISE_DATE,
    MANAGER_BONUS_DATE, MANAGER_BONUS, MANAGER_START, MANAGER_NAME,
    MONTHLY_RENT as _DEFAULT_RENT, ANCHOR_START, STORE_OPEN,
    STORE_RENT, PAY_PERIOD_CONFIG,
    STORE_OPEN_DATES, get_royalty_rate, get_monthly_rent,
    SUPABASE_URL, SUPABASE_ANON_KEY, PORTAL_BACK_JS,
)
from formatting import fc
from fetch_employees import get_store_pay_data

SCRIPTS_DIR = Path(__file__).parent
_store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = "Port Washington" if _store_name == "port-washington" else "Hicksville"
_home_url = "../index.html"
_other_store = "Hicksville" if _store_name == "port-washington" else "Port Washington"
_other_dir = "../hicksville" if _store_name == "port-washington" else "../port-washington"
_other_fn = "Hicksville" if _store_name == "port-washington" else "PortWashington"
_switch_url = f"{_other_dir}/WoofGang_{_other_fn}_Commission_Dashboard.html"
MONTHLY_RENT = STORE_RENT.get(_store_name, _DEFAULT_RENT)

# Load employee maps/rates from Supabase (falls back to config.py if not yet populated)
RETAIL_NAME_MAP, RETAIL_RATES, BATHER_NAME_MAP, BATHER_RATE_MAP, GUARANTEES = get_store_pay_data(_store_name)

DATA_DIR   = _store.data_dir
OUTPUT_DIR = _store.output_dir

def get_hours_from_clocks(clocks, name_map, period_start, period_end):
    """Calculate hours worked from time clock records for a pay period.
    Returns (total_hours_dict, daily_dict) where daily_dict is {name: [{date, hours}]}"""
    hours = {}
    daily = {}
    for c in clocks:
        full_name = c.get("EmployeeName", "")
        short_name = name_map.get(full_name)
        if not short_name: continue
        time_in = (c.get("TimeIn") or "")[:10]
        if period_start <= time_in <= period_end:
            hrs = c.get("TotalTimeClockHoursDecimal") or 0
            hours[short_name] = hours.get(short_name, 0) + hrs
            if short_name not in daily: daily[short_name] = []
            daily[short_name].append({"date": time_in, "hours": round(hrs, 2)})
    total = {k: round(v, 2) for k, v in hours.items()}
    for name in daily:
        daily[name] = sorted(daily[name], key=lambda x: x["date"])
    return total, daily


# ── Load all data ─────────────────────────────────────────────────────────────
groom_by_day  = defaultdict(lambda: defaultdict(float))
groom_disc_by_day = defaultdict(lambda: defaultdict(float))  # grooming discounts
order_groomer = {}
order_date    = {}
tips_by_order = {}
order_groomer_rev = {}

with open(DATA_DIR / "all_data.json") as f:
    data = json.load(f)
if True:
    for item in data["order_items"]:
        sku    = str(item.get("Sku",""))
        person = item.get("SalesPerson","") or "Unknown"
        price  = float(item.get("Price") or 0)
        qty    = float(item.get("Quantity") or 0)
        disc   = float(item.get("Discount") or 0)
        day    = (item.get("CreatedOn") or "")[:10]
        oid    = item.get("OrderId")
        is_groom_sku = (
            sku.startswith("987")    # core grooming
            or sku.startswith("543") # add-ons
            or sku.startswith("765") # spa upgrades
            or sku.startswith("432") # walk-in services
            or sku.startswith("321") # fees
            or sku.startswith("INTERNET-703") # online add-ons
            or sku == "002"          # service
            or sku == "991674465"    # mini groom flat price
            or sku == "617956234"    # Cat Grooming (HV)
            or sku == "341112490"    # Cat Groom haircut only (PW)
            or sku == "830648726"    # Cat Nail Trim (PW)
        )
        if is_groom_sku:
            if person not in EXCLUDE:
                groom_by_day[person][day] += price * qty
                if disc > 0:
                    groom_disc_by_day[person][day] += disc
                if oid:
                    order_groomer[oid] = person  # fallback
                    if oid not in order_groomer_rev: order_groomer_rev[oid] = {}
                    order_groomer_rev[oid][person] = order_groomer_rev[oid].get(person, 0) + price * qty
            elif person in {"Jessica G", "Angela R"}:
                # Bather revenue counts toward total but no commission
                groom_by_day["_bather_revenue"][day] += price * qty
                # Include bathers in tip split so tips are proportional
                if oid:
                    if oid not in order_groomer_rev: order_groomer_rev[oid] = {}
                    order_groomer_rev[oid][person] = order_groomer_rev[oid].get(person, 0) + price * qty
    order_customer = {}
    for o in data["orders"]:
        oid = o.get("OrderId")
        if oid:
            order_date[oid] = (o.get("CreatedOn") or "")[:10]
            tip = float(o.get("Tips") or 0)
            if tip > 0: tips_by_order[oid] = tip
            cid = o.get("CustomerId")
            if cid:
                order_customer[oid] = cid

# For orders missing from orders table, use order_item date as fallback
for oid in tips_by_order:
    if oid not in order_date:
        for item in data["order_items"]:
            if item.get("OrderId") == oid:
                order_date[oid] = (item.get("CreatedOn") or "")[:10]
                break

# Build (CustomerId, date) → groomer rev map for tip-only order matching
customer_day_rev = defaultdict(lambda: defaultdict(float))
for oid, rev_map in order_groomer_rev.items():
    cid = order_customer.get(oid)
    day = order_date.get(oid, "")
    if cid and day and rev_map:
        for groomer, rev in rev_map.items():
            customer_day_rev[(cid, day)][groomer] += rev

# Tips → groomer by day (split proportionally for multi-groomer orders)
tips_by_day = defaultdict(lambda: defaultdict(float))
for oid, tip in tips_by_order.items():
    day = order_date.get(oid, "")
    if not day: continue
    rev_map = order_groomer_rev.get(oid, {})
    if not rev_map:
        groomer = order_groomer.get(oid)
        if groomer:
            tips_by_day[groomer][day] += tip
        else:
            # Tip-only order (e.g. manual tip on separate transaction)
            # Match back to original grooming order by CustomerId + same day
            cid = order_customer.get(oid)
            if cid:
                rev_map = customer_day_rev.get((cid, day), {})
    if not rev_map:
        continue
    if len(rev_map) == 1:
        tips_by_day[list(rev_map.keys())[0]][day] += tip
    else:
        total_rev = sum(rev_map.values())
        if total_rev > 0:
            for groomer, rev in rev_map.items():
                tips_by_day[groomer][day] += round(tip * rev / total_rev, 2)

groomers = sorted(g for g in groom_by_day.keys() if not g.startswith("_"))

# ── Pay periods: store-specific (PW=bi-weekly Mon–Sun, HV=weekly Sat–Fri) ───
TODAY        = date.today()

def build_pay_periods():
    pp_cfg = PAY_PERIOD_CONFIG.get(_store_name, PAY_PERIOD_CONFIG["port-washington"])
    length = pp_cfg["length_days"]
    anchor = pp_cfg["anchor"]
    store_open = STORE_OPEN_DATES.get(_store_name, STORE_OPEN)

    periods = []
    start = anchor
    # Go back to store open
    while start > store_open:
        start -= timedelta(days=length)
    # Build forward
    while start <= TODAY:
        end = start + timedelta(days=length - 1)
        if end >= store_open and start <= TODAY:
            periods.append((start, end))
        start += timedelta(days=length)
    return list(reversed(periods))  # most recent first

pay_periods = build_pay_periods()

# ── Commission helpers ────────────────────────────────────────────────────────
def get_guarantee(groomer, day):
    """Return daily guarantee amount if groomer has an active guarantee on this day, else 0."""
    g = GUARANTEES.get(groomer)
    if not g:
        return 0
    rate, start, end = g
    if start <= day <= end:
        return rate
    return 0

def day_pay(groomer, day):
    rev  = groom_by_day[groomer].get(day, 0)
    disc = groom_disc_by_day[groomer].get(day, 0)
    tips = tips_by_day[groomer].get(day, 0)
    comm = rev * COMMISSION_RATE
    guar = get_guarantee(groomer, day)
    paid = max(comm, guar) if guar else comm
    return {"rev": rev, "disc": round(disc, 2), "comm": comm, "paid": paid, "tips": tips,
            "total": paid + tips, "guar_applied": guar > 0 and comm < guar}

def period_summary(groomer, start, end):
    days = [d for d in sorted(groom_by_day[groomer].keys())
            if start.isoformat() <= d <= end.isoformat()]
    # Also include days with only tips
    tip_days = [d for d in sorted(tips_by_day[groomer].keys())
                if start.isoformat() <= d <= end.isoformat() and d not in days]
    all_days = sorted(set(days + tip_days))
    
    t = {"rev":0,"disc":0,"comm":0,"paid":0,"tips":0,"total":0,"working_days":0,"guar_days":0,"daily":[]}
    for day in all_days:
        d = day_pay(groomer, day)
        if d["rev"] > 0 or d["tips"] > 0:
            t["rev"]   += d["rev"]
            t["disc"]  += d["disc"]
            t["comm"]  += d["comm"]
            t["paid"]  += d["paid"]
            t["tips"]  += d["tips"]
            t["total"] += d["total"]
            t["working_days"] += 1
            if d["guar_applied"]: t["guar_days"] += 1
            t["daily"].append({"date": day, **d})
    return t

def period_label(s, e):
    return f"{s.strftime('%b %-d')} – {e.strftime('%b %-d, %Y')}"

# ── Build pay period data for all periods ────────────────────────────────────
COLORS = ["#C4276E","#1976D2","#388E3C","#F57C00","#7B1FA2","#00838F",
          "#D32F2F","#455A64","#827717","#4527A0","#00695C","#BF360C",
          "#1565C0","#558B2F","#6A1B9A","#AD1457"]
groomer_color = {g: COLORS[i % len(COLORS)] for i, g in enumerate(groomers)}

# YTD 2026
ytd_start = date(2026, 1, 1)
ytd_data = {g: period_summary(g, ytd_start, TODAY) for g in groomers}
ytd_total = {k: sum(ytd_data[g][k] for g in groomers)
             for k in ["rev","disc","comm","paid","tips","total","working_days","guar_days"]}
# Add bather revenue to total revenue (no commission)
ytd_bather_rev = sum(v for d, v in groom_by_day.get("_bather_revenue", {}).items()
                     if ytd_start.isoformat() <= d <= TODAY.isoformat())
ytd_total["rev"] += ytd_bather_rev

ytd_days_count = (TODAY - ytd_start).days + 1
ytd_months = TODAY.month  # number of months in YTD (Jan=1, Feb=2, etc.)
# ytd_rent computed after monthly_data is built (uses tiered rates)

def manager_salary_for_range(start, end):
    """Returns (total_pay, old_days, new_days, daily_rows, bonus) accounting for salary change and bonus."""
    if end < MANAGER_START:
        return 0.0, 0, 0, [], 0.0
    effective_start = max(start, MANAGER_START)
    total = 0.0
    old_days = 0
    new_days = 0
    daily_rows = []
    bonus = MANAGER_BONUS if start <= MANAGER_BONUS_DATE <= end else 0.0
    for n in range((end - effective_start).days + 1):
        d = effective_start + timedelta(days=n)
        if d.weekday() >= 5: continue  # skip weekends
        rate = MANAGER_SALARY_OLD if d < MANAGER_RAISE_DATE else MANAGER_SALARY_NEW
        pay = rate / 260.0
        total += pay
        if d < MANAGER_RAISE_DATE:
            old_days += 1
        else:
            new_days += 1
        daily_rows.append({
            "date": d.isoformat(),
            "rate": rate,
            "pay": round(pay, 2),
            "bonus": MANAGER_BONUS if d == MANAGER_BONUS_DATE else 0.0
        })
    return round(total + bonus, 2), old_days, new_days, daily_rows, bonus

if _store_name == "port-washington":
    ytd_manager, ytd_mgr_old_days, ytd_mgr_new_days, ytd_mgr_daily, ytd_mgr_bonus = manager_salary_for_range(ytd_start, TODAY)
else:
    ytd_manager, ytd_mgr_old_days, ytd_mgr_new_days, ytd_mgr_daily, ytd_mgr_bonus = 0, 0, 0, [], 0

# YTD bather & retail pay from time clocks
ytd_bather_hours, _ = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, ytd_start.strftime("%Y-%m-%d"), TODAY.strftime("%Y-%m-%d"))
ytd_bather_pay = round(sum(h * BATHER_RATE_MAP.get(name, BATHER_RATE) for name, h in ytd_bather_hours.items()), 2)
ytd_retail_hours, _ = get_hours_from_clocks(data.get("time_clocks", []), RETAIL_NAME_MAP, ytd_start.strftime("%Y-%m-%d"), TODAY.strftime("%Y-%m-%d"))
ytd_retail_pay = round(sum(h * RETAIL_RATES.get(name, 0) for name, h in ytd_retail_hours.items()), 2)

# Monthly breakdown for exec dashboard
import calendar as _cal
monthly_data = []
m_start = date(2024, 9, 1)
while m_start <= TODAY:
    m_end = date(m_start.year, m_start.month, _cal.monthrange(m_start.year, m_start.month)[1])
    m_end = min(m_end, TODAY)
    # simpler: use period_summary
    m_groomer_data = {g: period_summary(g, m_start, m_end) for g in groomers}
    m_rev = sum(d["rev"] for d in m_groomer_data.values())
    m_bather = sum(v for day, v in groom_by_day.get("_bather_revenue", {}).items()
                   if m_start.isoformat() <= day <= m_end.isoformat())
    m_rev += m_bather
    m_paid = sum(d["paid"] for d in m_groomer_data.values())
    m_tips = sum(d["tips"] for d in m_groomer_data.values())
    m_mgr, _, _, _, _ = manager_salary_for_range(m_start, m_end) if _store_name == "port-washington" else (0, 0, 0, [], 0)
    m_days = sum(1 for n in range((m_end - m_start).days + 1)
                 if (m_start + timedelta(days=n)).weekday() < 5)
    m_rent = get_monthly_rent(_store_name, m_start)
    m_royalties = round(m_rev * get_royalty_rate(_store_name, m_start), 2)
    # Bather pay for this month from time clocks
    s_str = m_start.strftime("%Y-%m-%d")
    e_str = m_end.strftime("%Y-%m-%d")
    m_bather_hours, _ = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, s_str, e_str)
    m_bather_pay = round(sum(h * BATHER_RATE_MAP.get(name, BATHER_RATE) for name, h in m_bather_hours.items()), 2)
    m_retail_hours, _ = get_hours_from_clocks(data.get("time_clocks", []), RETAIL_NAME_MAP, s_str, e_str)
    m_retail_pay = round(sum(h * RETAIL_RATES.get(name, 0) for name, h in m_retail_hours.items()), 2)
    m_disc = round(sum(v for g in groom_disc_by_day for d, v in groom_disc_by_day[g].items()
                       if m_start.isoformat() <= d <= m_end.isoformat()), 2)
    m_total_cost = m_paid + m_mgr + m_bather_pay + m_retail_pay + m_royalties + m_rent
    m_net_margin = round(m_rev - m_disc - m_total_cost, 2)
    m_net_margin_pct = round(m_net_margin / m_rev * 100, 1) if m_rev else 0
    monthly_data.append({
        "month": m_start.strftime("%b %Y"),
        "year": m_start.year,
        "rev": round(m_rev, 2),
        "paid": round(m_paid, 2),
        "tips": round(m_tips, 2),
        "mgr": round(m_mgr, 2),
        "bather_pay": m_bather_pay,
        "retail_pay": m_retail_pay,
        "royalties": m_royalties,
        "rent": m_rent,
        "payroll": round(m_paid + m_mgr, 2),
        "margin": m_net_margin,
        "margin_pct": m_net_margin_pct,
        "payroll_pct": round((m_paid + m_mgr) / m_rev * 100, 1) if m_rev else 0,
        "working_days": m_days,
        "rev_per_day": round(m_rev / m_days, 2) if m_days else 0,
    })
    # next month
    if m_start.month == 12:
        m_start = date(m_start.year + 1, 1, 1)
    else:
        m_start = date(m_start.year, m_start.month + 1, 1)

import json as _json2
monthly_json = _json2.dumps(monthly_data)

# YTD royalties and rent from monthly data (uses tiered rates)
ytd_royalties = sum(m["royalties"] for m in monthly_data if m["year"] == 2026)
ytd_rent = sum(m["rent"] for m in monthly_data if m["year"] == 2026)

# Last 30 days
l30_start = TODAY - timedelta(days=29)
l30_data = {g: period_summary(g, l30_start, TODAY) for g in groomers}

def groomer_badge(g):
    guar = GUARANTEES.get(g)
    if guar and TODAY.isoformat() <= guar[2]:
        badge = f' <span style="background:#e3f2fd;color:#1565c0;padding:1px 6px;border-radius:6px;font-size:0.7rem;font-weight:700">G${guar[0]:.0f}</span>'
    else:
        badge = ""
    dot = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{groomer_color[g]};margin-right:7px"></span>'
    return f"{dot}<strong>{g}</strong>{badge}"

def summary_table(data_dict, show_groomers=None):
    rows = ""
    groomers_to_show = show_groomers or sorted(groomers, key=lambda x: -data_dict[x]["total"])
    for g in groomers_to_show:
        d = data_dict[g]
        if d["rev"] == 0 and d["tips"] == 0: continue
        disc_cell = f'<span style="color:#e53935">-{fc(d["disc"])}</span>' if d["disc"] > 0 else "—"
        rows += f'''<tr>
          <td>{groomer_badge(g)}</td>
          <td style="text-align:right;color:#888;font-weight:600">{d["working_days"]}</td>
          <td style="text-align:right">{fc(d["rev"])}</td>
          <td style="text-align:right">{disc_cell}</td>
          <td style="text-align:right;color:#888">{fc(d["comm"])}</td>
          <td style="text-align:right;color:#1565c0;font-weight:600">{fc(d["paid"])}{"<br><span style='font-size:0.72rem;color:#1565c0'>↑ "+str(d['guar_days'])+" guar days</span>" if d["guar_days"] else ""}</td>
          <td style="text-align:right;color:#f57c00">{fc(d["tips"])}</td>
          <td style="text-align:right;font-weight:700;color:#C4276E">{fc(d["total"])}</td>
          <td style="text-align:right;color:#888;font-size:0.82rem">{fc(d["total"]/d["working_days"]) if d["working_days"] else "—"}</td>
        </tr>'''
    return rows

def daily_detail_rows(data_dict):
    rows = ""
    for g in sorted(groomers, key=lambda x: -data_dict[x]["total"]):
        d = data_dict[g]
        if not d["daily"]: continue
        rows += f'<tr style="background:#f8f7f4"><td colspan="9" style="padding:10px 14px;font-weight:700">{groomer_badge(g)}</td></tr>'
        for day in d["daily"]:
            gflag = '<span style="background:#e3f2fd;color:#1565c0;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:6px">guarantee</span>' if day["guar_applied"] else ""
            wflag = '<span style="background:#fce4ec;color:#c62828;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:4px">waive</span>' if day["guar_applied"] else ""
            day_disc = f'<span style="color:#e53935">-{fc(day["disc"])}</span>' if day.get("disc", 0) > 0 else "—"
            rows += f'''<tr>
              <td colspan="2" style="padding-left:24px;color:#888;font-size:0.82rem">{day["date"]}</td>
              <td style="text-align:right">{fc(day["rev"])}</td>
              <td style="text-align:right">{day_disc}</td>
              <td style="text-align:right;color:#888">{fc(day["comm"])}</td>
              <td style="text-align:right;color:#1565c0">{fc(day["paid"])}{gflag}{wflag}</td>
              <td style="text-align:right;color:#f57c00">{fc(day["tips"])}</td>
              <td style="text-align:right;font-weight:600;color:#C4276E">{fc(day["total"])}</td>
              <td></td>
            </tr>'''
    return rows

# ── Build pay period dropdown options + JSON data ────────────────────────────
pp_options = ""
pp_data = {}  # period_id -> {groomer -> summary}

for i, (s, e) in enumerate(pay_periods):
    label = period_label(s, e)
    selected = "selected" if i == 0 else ""
    pp_options += f'<option value="pp_{i}" {selected}>{label}</option>'
    pp_data[f"pp_{i}"] = {}
    for g in groomers:
        d = period_summary(g, s, e)
        pp_data[f"pp_{i}"][g] = d
    if _store_name == "port-washington":
        mgr_pay, mgr_old, mgr_new, mgr_daily, mgr_bonus = manager_salary_for_range(s, e)
    else:
        mgr_pay, mgr_old, mgr_new, mgr_daily, mgr_bonus = 0, 0, 0, [], 0
    pp_data[f"pp_{i}"]["_royalty_rate"] = get_royalty_rate(_store_name, s)
    pp_data[f"pp_{i}"]["_monthly_rent"] = get_monthly_rent(_store_name, s)
    pp_data[f"pp_{i}"]["_manager_salary"] = mgr_pay
    pp_data[f"pp_{i}"]["_manager_old_days"] = mgr_old
    pp_data[f"pp_{i}"]["_manager_new_days"] = mgr_new
    pp_data[f"pp_{i}"]["_manager_daily"] = mgr_daily
    pp_data[f"pp_{i}"]["_manager_bonus"] = mgr_bonus
    # Bather hours for this period from time clocks
    s_str = s.strftime("%Y-%m-%d")
    bather_hours, bather_daily = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, s_str, e.strftime("%Y-%m-%d"))
    # Bather revenue for this period
    pp_bather_rev = sum(v for d, v in groom_by_day.get("_bather_revenue", {}).items()
                        if s.isoformat() <= d <= e.isoformat())
    pp_data[f"pp_{i}"]["_bather_rev"] = pp_bather_rev
    pp_data[f"pp_{i}"]["_bather_pay"] = {
        name: round(hrs * BATHER_RATE_MAP.get(name, BATHER_RATE), 2)
        for name, hrs in bather_hours.items()
    }
    pp_data[f"pp_{i}"]["_bather_hours"] = bather_hours
    pp_data[f"pp_{i}"]["_bather_daily"] = bather_daily
    # Bather tips for this period
    bather_tips = {}
    bather_tips_daily = {}
    for bname in BATHER_NAME_MAP:
        btdays = {d: v for d, v in tips_by_day.get(bname, {}).items()
                  if s.isoformat() <= d <= e.isoformat()}
        if btdays:
            bather_tips[bname] = round(sum(btdays.values()), 2)
            bather_tips_daily[bname] = btdays
    pp_data[f"pp_{i}"]["_bather_tips"] = bather_tips
    pp_data[f"pp_{i}"]["_bather_tips_daily"] = bather_tips_daily
    # Retail staff hours/pay
    retail_hours, retail_daily = get_hours_from_clocks(data.get("time_clocks", []), RETAIL_NAME_MAP, s_str, e.strftime("%Y-%m-%d"))
    pp_data[f"pp_{i}"]["_retail_pay"] = {
        name: round(hrs * RETAIL_RATES.get(name, 0), 2)
        for name, hrs in retail_hours.items()
    }
    pp_data[f"pp_{i}"]["_retail_hours"] = retail_hours
    pp_data[f"pp_{i}"]["_retail_daily"] = retail_daily
    pp_data[f"pp_{i}"]["_retail_rates"] = {name: RETAIL_RATES.get(name, 0) for name in retail_hours}

# Serialize pay period data to JS
import json as _json
# Sanitize any strings that could break JS
def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    elif isinstance(obj, str):
        return obj.replace('\\', '\\\\').replace('"', '\\"')
    return obj
pp_json = _json.dumps(pp_data)
groomers_json = _json.dumps(groomers)
groomer_colors_json = _json.dumps(groomer_color)
# Convert guarantees to JS-friendly format: {name: {rate, start, end}}
_guar_js = {name: {"rate": g[0], "start": g[1], "end": g[2]} for name, g in GUARANTEES.items()}
guarantees_json = _json.dumps(_guar_js)

# ── Sue M: weekly tips + product purchases ────────────────────────────────────
sue_name = "Sue M"
from collections import OrderedDict as _OD

# Collect Sue's tips per order (proportionally split for multi-groomer orders)
_sue_tip_by_day = defaultdict(float)
for oid, rev_map in order_groomer_rev.items():
    if sue_name not in rev_map:
        continue
    day = order_date.get(oid, "")
    if not day:
        continue
    tip = tips_by_order.get(oid, 0)
    total_rev = sum(rev_map.values())
    sue_rev = rev_map[sue_name]
    sue_tip = round(tip * sue_rev / total_rev, 2) if total_rev > 0 and len(rev_map) > 1 else tip
    _sue_tip_by_day[day] += sue_tip
# Sue Moore's purchases as customer (employee 20% discount → owes 80% of subtotal)
SUE_CUSTOMER_ID = 418921628
_sue_purchases = []
for o in data["orders"]:
    if o.get("CustomerId") == SUE_CUSTOMER_ID:
        st = float(o.get("SubTotal") or 0)
        if st <= 0:
            continue
        day = (o.get("CreatedOn") or "")[:10]
        receipt = str(o.get("CustomReceiptNumber", ""))
        oid = o.get("OrderId")
        items = [item.get("Name", "") for item in data["order_items"] if item.get("OrderId") == oid]
        _sue_purchases.append({
            "date": day, "receipt": receipt,
            "subtotal": round(st, 2), "owed": round(st * 0.80, 2),
            "items": ", ".join(items),
        })
_sue_purchases.sort(key=lambda x: x["date"], reverse=True)

# Build simple weekly summary: tips + purchases + net
_sue_weekly = _OD()
for day, tip in _sue_tip_by_day.items():
    dt = datetime.strptime(day, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    wk = monday.isoformat()
    if wk not in _sue_weekly:
        _sue_weekly[wk] = {"label": f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}",
                           "tips": 0, "purchases": [], "owed": 0}
    _sue_weekly[wk]["tips"] += tip
for p in _sue_purchases:
    dt = datetime.strptime(p["date"], "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    wk = monday.isoformat()
    if wk not in _sue_weekly:
        _sue_weekly[wk] = {"label": f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}",
                           "tips": 0, "purchases": [], "owed": 0}
    _sue_weekly[wk]["purchases"].append(p)
    _sue_weekly[wk]["owed"] += p["owed"]
_sue_weekly = _OD(sorted(_sue_weekly.items(), reverse=True))
for w in _sue_weekly.values():
    w["tips"] = round(w["tips"], 2)
    w["owed"] = round(w["owed"], 2)
    w["net"] = round(w["tips"] - w["owed"], 2)
sue_weekly_json = _json.dumps(list(_sue_weekly.values()))

# YTD summary rows
ytd_rows = summary_table(ytd_data)
ytd_daily = daily_detail_rows(ytd_data)
l30_rows  = summary_table(l30_data)
l30_daily = daily_detail_rows(l30_data)

TABLE_HEADER = '''<thead><tr>
  <th>Groomer</th>
  <th style="text-align:right">Days</th>
  <th style="text-align:right">Groom Revenue</th>
  <th style="text-align:right">Discounts</th>
  <th style="text-align:right">50% Comm</th>
  <th style="text-align:right">Comm Paid</th>
  <th style="text-align:right">Tips</th>
  <th style="text-align:right">Total Pay</th>
  <th style="text-align:right">Avg/Day</th>
</tr></thead>'''

from zoneinfo import ZoneInfo
et_tz = ZoneInfo("America/New_York")
now_et = datetime.now(et_tz).strftime("%B %d, %Y at %I:%M %p ET")

# Sue panel - built outside f-string to avoid backslash issues
_sue_tab_btn = '<button class="tab" onclick="showTab(\'sue\',this)">Sue</button>' if _store_name == "port-washington" else ''
_sue_panel = '''<!-- Sue M -->
<div class="panel" id="panel-sue">
  <div class="kpi-grid" id="sue-kpis"></div>
  <div class="info-box">Sue M's weekly tips and product purchases. Employees get a <strong>20% discount</strong> - purchases are charged at <strong>80% of subtotal</strong> and deducted from tips. Net = Tips - Purchases.</div>
  <div id="sue-weeks-container"></div>
</div>''' if _store_name == "port-washington" else ''

# Carol panel - built outside f-string to avoid backslash issues (hicksville only)
_carol_tab_btn = '<button class="tab" onclick="showTab(\'carol\',this)">Carol</button>' if _store_name == "hicksville" else ''
_carol_panel = '''<!-- Carol W -->
<div class="panel" id="panel-carol">
  <div class="kpi-grid" id="carol-kpis"></div>
  <div class="info-box">Carol W's weekly pay breakdown. <strong>$100/week</strong> goes on payroll; the remainder is paid off-books via check.</div>
  <div id="carol-weeks-container"></div>
</div>''' if _store_name == "hicksville" else ''

if _store_name == "port-washington":
    _mgr_bonus_kpi = f"<div class='kpi' style='border-color:#E91E63;flex:1;min-width:120px'><div class='kpi-val' style='color:#E91E63;font-size:1.4rem'>{fc(ytd_mgr_bonus)}</div><div class='kpi-label'>Bonus (Mar 1)</div></div>" if ytd_mgr_bonus else ""
    _mgr_daily_rows = "".join(
        f'<tr><td style="font-size:0.84rem">{r["date"]}</td>'
        f'<td style="font-size:0.84rem;color:#888">{datetime.strptime(r["date"],"%Y-%m-%d").strftime("%a")}</td>'
        f'<td style="text-align:right;font-size:0.84rem">{"$65,000" if r["rate"]==65000 else "$67,000"}'
        f'<span style="font-size:0.72rem;color:#5C6BC0;margin-left:4px">{"↑ raised" if r["date"]=="2026-03-01" else ""}</span></td>'
        f'<td style="text-align:right;font-weight:600;color:#5C6BC0">{fc(r["pay"])}</td>'
        f'<td style="text-align:right;font-weight:700;color:#E91E63">{fc(r["bonus"]) if r["bonus"] else "—"}</td></tr>'
        for r in ytd_mgr_daily
    )
    _mgr_ytd_card = f"""<div class="card">
    <div class="stitle">Manager Salary — Cindy Szczudlo</div>
    <div style="display:flex;gap:16px;margin-bottom:18px;flex-wrap:wrap">
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{fc(ytd_manager)}</div><div class="kpi-label">YTD Total</div></div>
      <div class="kpi" style="border-color:#7986CB;flex:1;min-width:120px"><div class="kpi-val" style="color:#7986CB;font-size:1.4rem">{ytd_mgr_old_days}</div><div class="kpi-label">Days @ $65k</div></div>
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{ytd_mgr_new_days}</div><div class="kpi-label">Days @ $67k</div></div>
      {_mgr_bonus_kpi}
      <div class="kpi" style="border-color:#9FA8DA;flex:1;min-width:120px"><div class="kpi-val" style="color:#9FA8DA;font-size:1.4rem">{fc(65000/260)}</div><div class="kpi-label">Old Rate/Day</div></div>
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{fc(67000/260)}</div><div class="kpi-label">New Rate/Day</div></div>
    </div>
    <button class="toggle-btn" onclick="toggleDetail('cindy-ytd-detail',this)">▼ Show daily breakdown</button>
    <div class="detail-section" id="cindy-ytd-detail">
      <div class="tbl-wrap"><table>
        <thead><tr>
          <th>Date</th><th>Day</th><th style="text-align:right">Annual Rate</th><th style="text-align:right">Daily Pay</th><th style="text-align:right">Bonus</th>
        </tr></thead>
        <tbody>{_mgr_daily_rows}</tbody>
      </table></div>
    </div>
  </div>"""
else:
    _mgr_ytd_card = ""

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Woof Gang Commission · Port Washington</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f4f0;color:#1a1a1a}}
.topbar{{background:#C4276E;padding:18px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3)}}
.topbar-title{{color:white;font-size:1.1rem;font-weight:700}}.topbar-sub{{color:rgba(255,255,255,0.75);font-size:0.82rem;margin-top:2px}}
.topbar-date{{color:rgba(255,255,255,0.85);font-size:0.82rem}}
.tab-bar{{background:white;border-bottom:2px solid #eee;padding:0 32px;display:flex;gap:4px;position:sticky;top:57px;z-index:99;align-items:center}}
.tab{{padding:14px 20px;border:none;background:transparent;color:#999;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;transition:all 0.2s;font-family:inherit;margin-bottom:-2px;white-space:nowrap}}
.tab:hover{{color:#C4276E}}.tab.active{{color:#C4276E;border-bottom-color:#C4276E}}
.pp-select{{margin-left:auto;margin-right:8px;padding:7px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.84rem;font-family:inherit;outline:none;cursor:pointer;display:none}}
.pp-select:focus{{border-color:#C4276E}}
.page{{max-width:1300px;margin:0 auto;padding:28px 24px}}
.panel{{display:none}}.panel.active{{display:block}}
.stitle{{font-size:1.05rem;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.stitle::before{{content:"";width:4px;height:18px;background:#C4276E;border-radius:2px;display:inline-block}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{background:white;border-radius:12px;padding:16px;border-top:3px solid #C4276E;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.kpi.blue{{border-color:#1976D2}}.kpi.green{{border-color:#388E3C}}.kpi.orange{{border-color:#F57C00}}.kpi.grey{{border-color:#aaa}}
.kpi-val{{font-size:1.6rem;font-weight:700;line-height:1;color:#C4276E}}
.kpi.blue .kpi-val{{color:#1976D2}}.kpi.green .kpi-val{{color:#388E3C}}.kpi.orange .kpi-val{{color:#F57C00}}.kpi.grey .kpi-val{{color:#888}}
.kpi-label{{font-size:0.73rem;color:#999;margin-top:5px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em}}
.card{{background:white;border-radius:14px;padding:22px;margin-bottom:22px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem}}
th{{background:#f8f7f4;padding:9px 12px;text-align:left;font-size:0.72rem;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:0.04em;border-bottom:2px solid #eee;white-space:nowrap}}
td{{padding:9px 12px;border-bottom:1px solid #f0ede8;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#fafaf8!important}}
.toggle-btn{{background:none;border:none;color:#C4276E;font-size:0.82rem;font-weight:600;cursor:pointer;font-family:inherit;padding:4px 0;margin-top:10px}}
.toggle-btn:hover{{text-decoration:underline}}
.detail-section{{display:none;margin-top:16px;border-top:1px solid #eee;padding-top:16px}}
.info-box{{background:#f0f7ff;border-left:4px solid #1976D2;border-radius:8px;padding:12px 16px;margin-bottom:18px;font-size:0.85rem;line-height:1.6}}
.header{{background:linear-gradient(135deg,#1B6B6B 0%,#6B3520 100%);color:white;padding:40px 0 30px;text-align:center;position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(196,39,110,0.15) 0%,transparent 50%)}}
.header h1{{font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px}}
.header .subtitle{{font-size:1rem;font-weight:400;opacity:0.9;position:relative}}
.header .brand-tag{{display:inline-block;background:#C4276E;color:white;padding:4px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-top:12px;position:relative}}
.header-timestamp{{position:absolute;top:12px;right:20px;font-size:0.78rem;opacity:0.85;font-weight:400;z-index:1}}
.adj-overlay{{position:fixed;inset:0;background:rgba(0,0,0,0.42);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px}}
.adj-overlay.hidden{{display:none}}
.adj-modal{{background:white;border-radius:14px;max-width:420px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.18)}}
.adj-modal-hdr{{padding:18px 22px 12px;border-bottom:1px solid #eee}}
.adj-modal-hdr h3{{font-size:1rem;font-weight:800;margin:0;color:#1a1a2e}}
.adj-modal-body{{padding:16px 22px}}
.adj-fg{{margin-bottom:13px}}
.adj-fg label{{display:block;font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#999;margin-bottom:4px}}
.adj-fg input,.adj-fg textarea{{width:100%;padding:8px 11px;border:2px solid #e8e8e8;border-radius:8px;font-family:inherit;font-size:0.9rem;box-sizing:border-box}}
.adj-fg input:focus,.adj-fg textarea:focus{{outline:none;border-color:#C4276E}}
.adj-fg textarea{{resize:vertical;min-height:52px}}
.adj-orig{{font-size:0.8rem;color:#999;margin-top:3px}}
.adj-modal-actions{{padding:10px 22px 18px;display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}}
.adj-save-btn{{padding:9px 22px;background:#C4276E;color:white;border:none;border-radius:8px;font-family:inherit;font-size:0.87rem;font-weight:700;cursor:pointer}}
.adj-save-btn:hover{{background:#a31f5b}}
.adj-save-btn:disabled{{opacity:.5;cursor:not-allowed}}
.adj-cancel-btn{{padding:9px 18px;background:white;border:2px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.87rem;font-weight:600;cursor:pointer;color:#888}}
.adj-clear-btn{{padding:9px 18px;background:white;border:2px solid #e53935;border-radius:8px;font-family:inherit;font-size:0.87rem;font-weight:600;cursor:pointer;color:#e53935}}
.adj-clear-btn:hover{{background:#ffebee}}
.adj-tag{{font-size:0.68rem;padding:1px 5px;border-radius:5px;font-weight:700;background:#fff3e0;color:#e65100;margin-left:5px;vertical-align:middle}}
.adj-edit-btn{{padding:1px 5px;border-radius:5px;border:1px solid #ccc;background:#fff;color:#aaa;font-size:0.7rem;cursor:pointer;margin-left:5px;vertical-align:middle}}
.adj-edit-btn:hover{{border-color:#C4276E;color:#C4276E}}

/* ── Print Sheet styles ── */
@media print {{
  /* Hide everything except the print sheet card */
  body * {{ visibility: hidden !important; }}
  #print-sheet-card, #print-sheet-card * {{ visibility: visible !important; }}
  #print-sheet-card {{
    position: absolute !important;
    left: 0 !important; top: 0 !important; right: 0 !important;
    width: 100% !important;
    margin: 0 !important; padding: 24px 32px !important;
    box-shadow: none !important; border-radius: 0 !important;
    background: white !important;
    page-break-inside: avoid;
  }}
  .print-hide {{ display: none !important; }}
  .print-sheet-header {{ border-top-color: #C4276E !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  #print-sheet-body table {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  #print-sheet-body table th {{ background: #FDF0F5 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; color: #C4276E !important; }}
  #print-sheet-body table tfoot tr {{ background: #FDF0F5 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ background: white !important; padding: 0 !important; margin: 0 !important; }}
  @page {{ margin: 0.5in; }}
}}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div class="header">
    <div class="header-timestamp">Updated {now_et}<br><a href="{_switch_url}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.78rem">&#x21C4; {_other_store}</a></div>
    <h1>Woof Gang {_store_display}</h1>
    <div class="subtitle">Groomer Commission Dashboard</div>
    <div class="brand-tag">Woof Gang Bakery &amp; Grooming</div>
</div>
<div class="topbar">
  <a id="portal-back" href="{_home_url}" style="color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.85rem;font-weight:600">&larr; Home</a>{PORTAL_BACK_JS}
</div>

<div class="tab-bar">
  <button class="tab active" onclick="showTab('ytd',this)">2026 YTD</button>
  <button class="tab" onclick="showTab('l30',this)">Last 30 Days</button>
  <button class="tab" onclick="showTab('pp',this)">Pay Period</button>
  <button class="tab" onclick="showTab('exec',this)">&#128200; Executive</button>
  {_sue_tab_btn}
  {_carol_tab_btn}
  <select class="pp-select" id="pp-select" onchange="renderPayPeriod(this.value)">
    {pp_options}
  </select>
  <button id="save-overrides-btn" onclick="saveOverrides()" style="display:none;margin-left:8px;padding:7px 14px;border-radius:8px;border:none;background:#C4276E;color:#fff;font-size:0.84rem;font-family:inherit;cursor:pointer;font-weight:600">💾 Save Overrides</button>
</div>

<div class="page">

<!-- ── YTD ── -->
<div class="panel active" id="panel-ytd">
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">{fc(ytd_total["rev"])}</div><div class="kpi-label">Groom Revenue</div></div>
    <div class="kpi" style="border-color:#e53935"><div class="kpi-val" style="color:#e53935">-{fc(ytd_total["disc"])}</div><div class="kpi-label">Grooming Discounts</div></div>
    <div class="kpi blue"><div class="kpi-val">{fc(ytd_total["paid"])}</div><div class="kpi-label">Commission Paid</div></div>
    <div class="kpi orange"><div class="kpi-val">{fc(ytd_total["tips"])}</div><div class="kpi-label">Tips</div></div>
    <div class="kpi green"><div class="kpi-val">{fc(ytd_total["total"])}</div><div class="kpi-label">Total Groomer Pay</div></div>
    {"" if _store_name != "port-washington" else '<div class="kpi" style="border-color:#5C6BC0"><div class="kpi-val" style="color:#5C6BC0">' + fc(ytd_manager) + '</div><div class="kpi-label">Manager Salary</div><div style="font-size:0.78rem;color:#5C6BC0;margin-top:3px">from Feb 23</div></div>'}
    <div class="kpi" style="border-color:#00796B"><div class="kpi-val" style="color:#00796B">{fc(ytd_bather_pay)}</div><div class="kpi-label">Bather Pay</div></div>
    <div class="kpi" style="border-color:#6A1B9A"><div class="kpi-val" style="color:#6A1B9A">{fc(ytd_retail_pay)}</div><div class="kpi-label">Retail Staff Pay</div></div>
    <div class="kpi" style="border-color:#AD1457"><div class="kpi-val" style="color:#AD1457">{fc(ytd_royalties)}</div><div class="kpi-label">Royalties + Marketing</div></div>
    <div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">{fc(ytd_rent)}</div><div class="kpi-label">Rent</div><div style="font-size:0.78rem;color:#6D4C41;margin-top:3px">{ytd_days_count} days</div></div>
    <div class="kpi" style="border-color:#00838F"><div class="kpi-val" style="color:#00838F">{fc(ytd_total["rev"] - ytd_total["disc"] - ytd_total["paid"] - ytd_manager - ytd_bather_pay - ytd_retail_pay - ytd_royalties - ytd_rent)}</div><div class="kpi-label">Margin</div><div style="font-size:0.78rem;color:#00838F;margin-top:3px;font-weight:600">{(ytd_total["rev"] - ytd_total["disc"] - ytd_total["paid"] - ytd_manager - ytd_bather_pay - ytd_retail_pay - ytd_royalties - ytd_rent) / (ytd_total["rev"] or 1) * 100:.1f}%</div></div>
    <div class="kpi grey"><div class="kpi-val">{int(ytd_total["guar_days"])}</div><div class="kpi-label">Guarantee Days</div></div>
  </div>
  <div class="info-box">Commission = 50% of daily grooming revenue. All groomers (except Kimberly) receive a <strong>$200/day guarantee</strong> for their first 90 days (Sue M: $300/day). Paid whichever is higher. Tips assigned to the groomer who performed the service.</div>
  <div class="card">
    <div class="stitle">2026 YTD Summary</div>
    <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{ytd_rows}</tbody></table></div>
    <button class="toggle-btn" onclick="toggleDetail('ytd-detail',this)">▼ Show daily detail</button>
    <div class="detail-section" id="ytd-detail">
      <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{ytd_daily}</tbody></table></div>
    </div>
  </div>
  {_mgr_ytd_card}
</div>

<!-- ── Last 30 Days ── -->
<div class="panel" id="panel-l30">
  <div class="card">
    <div class="stitle">Last 30 Days Summary</div>
    <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{l30_rows}</tbody></table></div>
    <button class="toggle-btn" onclick="toggleDetail('l30-detail',this)">▼ Show daily detail</button>
    <div class="detail-section" id="l30-detail">
      <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{l30_daily}</tbody></table></div>
    </div>
  </div>
</div>

<!-- ── Pay Period ── -->
<div class="panel" id="panel-pp">
  <div id="pp-kpis" class="kpi-grid"></div>
  <div class="card">
    <div class="stitle" id="pp-title">Pay Period Summary</div>
    <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody id="pp-tbody"></tbody></table></div>
    <button class="toggle-btn" onclick="toggleDetail('pp-detail',this)">▼ Show daily detail</button>
    <div class="detail-section" id="pp-detail">
      <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody id="pp-detail-tbody"></tbody></table></div>
    </div>
  </div>
  <div class="card" id="bather-pp-card" style="display:none">
    <div class="stitle">Bathers (Hourly @ $17/hr)</div>
    <div style="display:flex;gap:16px;margin-bottom:18px;flex-wrap:wrap" id="bather-pp-kpis"></div>
    <div class="detail-section" id="bather-pp-detail" style="display:none;margin-top:12px"></div>
    <button class="toggle-btn" data-label="details by person" onclick="toggleDetail('bather-pp-detail',this)">▼ Show details by person</button>
  </div>
  <div class="card" id="retail-pp-card" style="display:none">
    <div class="stitle">Retail Staff</div>
    <div style="display:flex;gap:16px;margin-bottom:18px;flex-wrap:wrap" id="retail-pp-kpis"></div>
    <div class="detail-section" id="retail-pp-detail" style="display:none;margin-top:12px"></div>
    <button class="toggle-btn" data-label="details by person" onclick="toggleDetail('retail-pp-detail',this)">▼ Show details by person</button>
  </div>
  <div class="card" id="cindy-pp-card" style="display:none">
    <div class="stitle">Manager Salary — Cindy Szczudlo</div>
    <div style="display:flex;gap:16px;margin-bottom:18px;flex-wrap:wrap" id="cindy-pp-kpis"></div>
    <button class="toggle-btn" onclick="toggleDetail('cindy-pp-detail',this)">▼ Show daily breakdown</button>
    <div class="detail-section" id="cindy-pp-detail">
      <div class="tbl-wrap"><table>
        <thead><tr><th>Date</th><th>Day</th><th style="text-align:right">Annual Rate</th><th style="text-align:right">Daily Pay</th><th style="text-align:right">Bonus</th></tr></thead>
        <tbody id="cindy-pp-tbody"></tbody>
      </table></div>
    </div>
  </div>

  <!-- ── Printable Groomer Sheet ── -->
  <div class="card no-print-shadow" id="print-sheet-card">
    <div class="stitle print-hide">&#128424; Printable Groomer Sheet</div>
    <div class="print-controls print-hide" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px">
      <label style="font-size:0.82rem;font-weight:600;color:#666">Groomer:</label>
      <select id="print-groomer-select" onchange="renderPrintSheet()" style="padding:8px 14px;border:2px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.88rem;font-weight:600;min-width:180px">
        <option value="">Select a groomer…</option>
      </select>
      <button onclick="window.print()" id="print-btn" style="padding:9px 20px;background:#C4276E;color:#fff;border:none;border-radius:8px;font-family:inherit;font-size:0.87rem;font-weight:700;cursor:pointer">&#128424; Print Sheet</button>
      <span style="font-size:0.78rem;color:#999;font-style:italic">Select a groomer to generate their daily pay period sheet</span>
    </div>
    <div id="print-sheet-body"></div>
  </div>
</div>

</div>

{_sue_panel}
{_carol_panel}

<div class="panel" id="panel-exec">
  <div style="padding:32px">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:32px" id="exec-kpis"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:32px">
      <div style="background:white;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08)">
        <h3 style="margin:0 0 4px;font-size:0.95rem;color:#444">2025 — Monthly Net Margin</h3>
        <p style="margin:0 0 14px;font-size:0.78rem;color:#aaa">Revenue vs. all costs by month</p>
        <canvas id="chart-margin-2025" height="240"></canvas>
      </div>
      <div style="background:white;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08)">
        <h3 style="margin:0 0 4px;font-size:0.95rem;color:#444">2026 — Monthly Net Margin</h3>
        <p style="margin:0 0 14px;font-size:0.78rem;color:#aaa">Revenue vs. all costs by month</p>
        <canvas id="chart-margin-2026" height="240"></canvas>
      </div>
    </div>
    <div style="background:white;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:24px">
      <h3 style="margin:0 0 16px;font-size:0.95rem;color:#444">Monthly Breakdown</h3>
      <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:0.84rem">
        <thead><tr style="border-bottom:2px solid #eee">
          <th style="text-align:left;padding:8px 12px;color:#888;font-weight:600">Month</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Revenue</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Commission</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Manager</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Bather</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Retail Staff</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Royalties</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Rent</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Net Margin</th>
          <th style="text-align:right;padding:8px 12px;color:#888;font-weight:600">Rev/Day</th>
        </tr></thead>
        <tbody id="exec-monthly-tbody"></tbody>
      </table></div>
    </div>
  </div>
</div>

<script>
var PP_DATA = {pp_json};
var GROOMERS = {groomers_json};
var MONTHLY_DATA = {monthly_json};
var COLORS = {groomer_colors_json};
var GUARANTEES = {guarantees_json};
var SUE_WEEKLY = {sue_weekly_json};

function fc(v) {{ return '$' + parseFloat(v).toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}}); }}

function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-'+id).classList.add('active');
  btn.classList.add('active');
  var sel = document.getElementById('pp-select');
  sel.style.display = id === 'pp' ? 'block' : 'none';
  if (id === 'pp') renderPayPeriod(sel.value);
  if (id === 'exec') renderExec();
  if (id === 'sue') renderSue();
  if (id === 'carol') renderCarol();
}}

function toggleDetail(id, btn) {{
  var el = document.getElementById(id);
  var open = el.style.display === 'block';
  el.style.display = open ? 'none' : 'block';
  var label = btn.getAttribute('data-label') || 'daily detail';
  btn.textContent = open ? '▼ Show ' + label : '▲ Hide ' + label;
}}

function getGuarRate(g, day) {{
  var gd = GUARANTEES[g];
  if (!gd) return 0;
  if (day && (day < gd.start || day > gd.end)) return 0;
  return gd.rate;
}}
function groomerBadge(g) {{
  var gd = GUARANTEES[g];
  var today = new Date().toISOString().slice(0,10);
  var badge = (gd && today <= gd.end) ? ' <span style="background:#e3f2fd;color:#1565c0;padding:1px 6px;border-radius:6px;font-size:0.7rem;font-weight:700">G$'+gd.rate+'</span>' : '';
  return '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'+COLORS[g]+';margin-right:7px"></span><strong>'+g+'</strong>'+badge;
}}

// ── Overrides system (Supabase) ──────────────────────────────────────────────
var _overrides = {{}};
var _adjMap = {{}};  // _adjMap[groomer][date][type] = corrected_amount
var _sbUrl = '{{supabase_url}}';
var _sbKey = '{{supabase_key}}';
var _storeKey = '{_store_name}';
var _sbHeaders = {{
  'apikey': _sbKey,
  'Authorization': 'Bearer ' + _sbKey,
  'Content-Type': 'application/json',
  'Prefer': 'return=minimal'
}};

function _loadOverrides() {{
  var p1 = fetch(_sbUrl + '/rest/v1/guarantee_overrides?select=groomer_name,override_date,waived', {{
    headers: {{ 'apikey': _sbKey, 'Authorization': 'Bearer ' + _sbKey }}
  }}).then(function(r) {{ return r.ok ? r.json() : []; }}).catch(function() {{ return []; }});
  var p2 = fetch(_sbUrl + '/rest/v1/commission_adjustments?select=groomer_name,adj_date,adj_type,corrected_amount&store_key=eq.' + encodeURIComponent(_storeKey), {{
    headers: {{ 'apikey': _sbKey, 'Authorization': 'Bearer ' + _sbKey }}
  }}).then(function(r) {{ return r.ok ? r.json() : []; }}).catch(function() {{ return []; }});
  Promise.all([p1, p2]).then(function(results) {{
    _overrides = {{}};
    results[0].forEach(function(row) {{
      if (row.waived) _overrides['guar_override_' + row.override_date + '_' + row.groomer_name] = true;
    }});
    _adjMap = {{}};
    results[1].forEach(function(row) {{
      if (!_adjMap[row.groomer_name]) _adjMap[row.groomer_name] = {{}};
      if (!_adjMap[row.groomer_name][row.adj_date]) _adjMap[row.groomer_name][row.adj_date] = {{}};
      _adjMap[row.groomer_name][row.adj_date][row.adj_type] = parseFloat(row.corrected_amount);
    }});
    renderPayPeriod(document.getElementById('pp-select').value);
  }}).catch(function() {{
    renderPayPeriod(document.getElementById('pp-select').value);
  }});
}}
_loadOverrides();

function getOverride(key) {{
  return _overrides[key] === true;
}}
function getAdj(g, date, type) {{
  return (_adjMap[g] && _adjMap[g][date] && _adjMap[g][date][type] !== undefined) ? _adjMap[g][date][type] : null;
}}

function _parseOverrideKey(key) {{
  // key format: guar_override_YYYY-MM-DD_Groomer Name
  var parts = key.replace('guar_override_', '').split('_');
  var dt = parts[0];
  var name = parts.slice(1).join('_');
  return {{ groomer_name: name, override_date: dt }};
}}

function setOverride(key, val) {{
  var parsed = _parseOverrideKey(key);
  var btn = document.getElementById('save-overrides-btn');
  btn.style.display = 'inline-block';
  btn.textContent = '⏳ Saving...';
  btn.style.background = '#888';

  if (val) {{
    // Upsert: insert or update
    _overrides[key] = true;
    fetch(_sbUrl + '/rest/v1/guarantee_overrides', {{
      method: 'POST',
      headers: Object.assign({{}}, _sbHeaders, {{'Prefer': 'return=minimal,resolution=merge-duplicates'}}),
      body: JSON.stringify({{ groomer_name: parsed.groomer_name, override_date: parsed.override_date, waived: true }})
    }})
    .then(function(r) {{
      if (r.ok || r.status === 201 || r.status === 200) {{
        btn.textContent = '✓ Saved';
        btn.style.background = '#388E3C';
      }} else {{
        btn.textContent = '✗ Error';
        btn.style.background = '#c62828';
      }}
      setTimeout(function() {{ btn.style.display = 'none'; }}, 2000);
    }})
    .catch(function() {{
      btn.textContent = '✗ Error';
      btn.style.background = '#c62828';
      setTimeout(function() {{ btn.style.display = 'none'; }}, 3000);
    }});
  }} else {{
    // Delete the override row
    delete _overrides[key];
    fetch(_sbUrl + '/rest/v1/guarantee_overrides?groomer_name=eq.' + encodeURIComponent(parsed.groomer_name) + '&override_date=eq.' + parsed.override_date, {{
      method: 'DELETE',
      headers: _sbHeaders
    }})
    .then(function(r) {{
      if (r.ok) {{
        btn.textContent = '✓ Saved';
        btn.style.background = '#388E3C';
      }} else {{
        btn.textContent = '✗ Error';
        btn.style.background = '#c62828';
      }}
      setTimeout(function() {{ btn.style.display = 'none'; }}, 2000);
    }})
    .catch(function() {{
      btn.textContent = '✗ Error';
      btn.style.background = '#c62828';
      setTimeout(function() {{ btn.style.display = 'none'; }}, 3000);
    }});
  }}
}}

function saveOverrides() {{
  // No-op: overrides auto-save on each toggle now
}}

function toggleGuar(key, ppId) {{
  var current = getOverride(key);
  setOverride(key, !current);
  renderPayPeriod(ppId);
}}

function renderPayPeriod(ppId) {{
  var data = PP_DATA[ppId];
  if (!data) return;

  // Find period label from select
  var sel = document.getElementById('pp-select');
  var label = sel.options[sel.selectedIndex].text;
  document.getElementById('pp-title').innerHTML = '<span style="content:none"></span>' + label;

  // Totals
  var totRev=0, totDisc=0, totPaid=0, totTips=0, totTotal=0, totGuar=0;
  GROOMERS.forEach(function(g) {{
    var d = data[g]; if (!d) return;
    totDisc += (d.disc || 0);
    var adjPaid = 0, adjTotal = 0, adjTips = 0;
    if (d.daily && d.daily.length) {{
      d.daily.forEach(function(day) {{
        var guar = getGuarRate(g, day.date);
        var overrideKey = 'guar_override_'+day.date+'_'+g;
        var overridden = getOverride(overrideKey);
        var guarActive = guar > 0 && day.guar_applied && !overridden;
        var commVal = getAdj(g, day.date, 'commission'); if (commVal === null) commVal = day.comm;
        var tipsVal = getAdj(g, day.date, 'tip');        if (tipsVal === null) tipsVal = day.tips;
        var p = guarActive ? Math.max(commVal, guar) : commVal;
        adjPaid += p; adjTips += tipsVal; adjTotal += p + tipsVal;
      }});
    }} else {{
      adjPaid = d.paid; adjTips = d.tips; adjTotal = d.total;
    }}
    totRev += d.rev; totPaid += adjPaid; totTips += adjTips; totTotal += adjTotal;
    totGuar += d.guar_days;
  }});
  totRev += (data._bather_rev || 0);
  var totRoyalties = totRev * (data._royalty_rate || 0.07);
  var DAILY_RENT = (data._monthly_rent || {MONTHLY_RENT}) * 12 / 365;
  var PP_LENGTH = {PAY_PERIOD_CONFIG.get(_store_name, PAY_PERIOD_CONFIG["port-washington"])["length_days"]};
  var totRent = DAILY_RENT * PP_LENGTH;
  var totManager = data._manager_salary || 0;
  var batherPay = data._bather_pay || {{}};
  var batherHours = data._bather_hours || {{}};
  var totBatherPay = Object.values(batherPay).reduce(function(a,b){{return a+b;}}, 0);
  var retailPay = data._retail_pay || {{}};
  var retailHours = data._retail_hours || {{}};
  var retailRates = data._retail_rates || {{}};
  var totRetailPay = Object.values(retailPay).reduce(function(a,b){{return a+b;}}, 0);
  var totMargin = totRev - totDisc - totPaid - totRoyalties - totRent - totManager - totBatherPay - totRetailPay;
  var totMarginPct = totRev > 0 ? (totMargin / totRev * 100).toFixed(1) : '0.0';

  document.getElementById('pp-kpis').innerHTML =
    '<div class="kpi"><div class="kpi-val">'+fc(totRev)+'</div><div class="kpi-label">Groom Revenue</div></div>'+
    (totDisc > 0 ? '<div class="kpi" style="border-color:#e53935"><div class="kpi-val" style="color:#e53935">-'+fc(totDisc)+'</div><div class="kpi-label">Grooming Discounts</div></div>' : '')+
    '<div class="kpi blue"><div class="kpi-val">'+fc(totPaid)+'</div><div class="kpi-label">Commission Paid</div></div>'+
    '<div class="kpi orange"><div class="kpi-val">'+fc(totTips)+'</div><div class="kpi-label">Tips</div></div>'+
    '<div class="kpi green"><div class="kpi-val">'+fc(totTotal)+'</div><div class="kpi-label">Total Pay</div></div>'+
    (totManager ? '<div class="kpi" style="border-color:#5C6BC0"><div class="kpi-val" style="color:#5C6BC0">'+fc(totManager)+'</div><div class="kpi-label">Manager Salary</div></div>' : '')+
    (totBatherPay ? '<div class="kpi" style="border-color:#00796B"><div class="kpi-val" style="color:#00796B">'+fc(totBatherPay)+'</div><div class="kpi-label">Bather Pay</div></div>' : '')+
    (totRetailPay ? '<div class="kpi" style="border-color:#6A1B9A"><div class="kpi-val" style="color:#6A1B9A">'+fc(totRetailPay)+'</div><div class="kpi-label">Retail Staff Pay</div></div>' : '')+
    '<div class="kpi" style="border-color:#AD1457"><div class="kpi-val" style="color:#AD1457">'+fc(totRoyalties)+'</div><div class="kpi-label">Royalties + Marketing</div></div>'+
    '<div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">'+fc(totRent)+'</div><div class="kpi-label">Rent</div><div style="font-size:0.78rem;color:#6D4C41;margin-top:3px">'+PP_LENGTH+' days</div></div>'+
    '<div class="kpi" style="border-color:#00838F"><div class="kpi-val" style="color:#00838F">'+fc(totMargin)+'</div><div class="kpi-label">Margin</div><div style="font-size:0.78rem;color:#00838F;margin-top:3px;font-weight:600">'+totMarginPct+'%</div></div>'+
    '<div class="kpi grey"><div class="kpi-val">'+totGuar+'</div><div class="kpi-label">Guarantee Days</div></div>';

  // Summary rows
  var sorted = GROOMERS.slice().sort(function(a,b) {{ return (data[b]||{{total:0}}).total - (data[a]||{{total:0}}).total; }});
  var rows = '';
  sorted.forEach(function(g) {{
    var d = data[g]; if (!d || (d.rev===0 && d.tips===0)) return;
    // Recompute paid/total respecting guarantee overrides + commission adjustments
    var adjPaid = 0, adjTotal = 0;
    if (d.daily && d.daily.length) {{
      d.daily.forEach(function(day) {{
        var guar = getGuarRate(g, day.date);
        var overrideKey = 'guar_override_'+day.date+'_'+g;
        var guarActive = guar > 0 && day.guar_applied && !getOverride(overrideKey);
        var commVal = getAdj(g, day.date, 'commission'); if (commVal === null) commVal = day.comm;
        var tipsVal = getAdj(g, day.date, 'tip');        if (tipsVal === null) tipsVal = day.tips;
        var p = guarActive ? Math.max(commVal, guar) : commVal;
        adjPaid += p; adjTotal += p + tipsVal;
      }});
    }} else {{
      adjPaid = d.paid; adjTotal = d.total;
    }}
    var guarNote = d.guar_days ? '<br><span style="font-size:0.72rem;color:#1565c0">↑ '+d.guar_days+' guar days</span>' : '';
    var avgDay = d.working_days ? fc(adjTotal/d.working_days) : '—';
    var discCell = (d.disc || 0) > 0 ? '<span style="color:#e53935">-'+fc(d.disc)+'</span>' : '—';
    rows += '<tr>'+
      '<td>'+groomerBadge(g)+'</td>'+
      '<td style="text-align:right;color:#888;font-weight:600">'+d.working_days+'</td>'+
      '<td style="text-align:right">'+fc(d.rev)+'</td>'+
      '<td style="text-align:right">'+discCell+'</td>'+
      '<td style="text-align:right;color:#888">'+fc(d.comm)+'</td>'+
      '<td style="text-align:right;color:#1565c0;font-weight:600">'+fc(adjPaid)+guarNote+'</td>'+
      '<td style="text-align:right;color:#f57c00">'+fc(d.tips)+'</td>'+
      '<td style="text-align:right;font-weight:700;color:#C4276E">'+fc(adjTotal)+'</td>'+
      '<td style="text-align:right;color:#888;font-size:0.82rem">'+avgDay+'</td>'+
      '</tr>';
  }});
  document.getElementById('pp-tbody').innerHTML = rows;

  // Daily detail
  var detailRows = '';
  sorted.forEach(function(g) {{
    var d = data[g]; if (!d || !d.daily || !d.daily.length) return;
    detailRows += '<tr style="background:#f8f7f4"><td colspan="9" style="padding:10px 14px;font-weight:700">'+groomerBadge(g)+'</td></tr>';
    d.daily.forEach(function(day) {{
      var guar = getGuarRate(g, day.date);
      var overrideKey = 'guar_override_'+day.date+'_'+g;
      var overridden = getOverride(overrideKey);
      var guarActive = guar > 0 && day.guar_applied && !overridden;
      var commAdj = getAdj(g, day.date, 'commission');
      var tipsAdj = getAdj(g, day.date, 'tip');
      var commVal = (commAdj !== null) ? commAdj : day.comm;
      var tipsVal = (tipsAdj !== null) ? tipsAdj : day.tips;
      var actualPaid = guarActive ? Math.max(commVal, guar) : commVal;
      var actualTotal = actualPaid + tipsVal;
      var gflag = '';
      if (day.guar_applied) {{
        if (overridden) {{
          gflag = '<span style="background:#fce4ec;color:#c62828;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:6px;text-decoration:line-through">guarantee</span>'+
                  '<button onclick="toggleGuar(&quot;'+overrideKey+'&quot;,&quot;'+ppId+'&quot;)" style="margin-left:6px;font-size:0.7rem;padding:1px 6px;border-radius:5px;border:1px solid #c62828;background:#fff;color:#c62828;cursor:pointer">restore</button>';
        }} else {{
          gflag = '<span style="background:#e3f2fd;color:#1565c0;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:6px">guarantee</span>'+
                  '<button onclick="toggleGuar(&quot;'+overrideKey+'&quot;,&quot;'+ppId+'&quot;)" style="margin-left:6px;font-size:0.7rem;padding:1px 6px;border-radius:5px;border:1px solid #e57373;background:#fff;color:#e57373;cursor:pointer">waive</button>';
        }}
      }}
      var commAdjTag = commAdj !== null ? '<span class="adj-tag">adj</span>' : '';
      var tipsAdjTag = tipsAdj !== null ? '<span class="adj-tag">adj</span>' : '';
      var editCommBtn = '<button class="adj-edit-btn" onclick="openAdjModal(&quot;'+g+'&quot;,&quot;'+day.date+'&quot;,&quot;commission&quot;,'+day.comm+','+(commAdj!==null?commAdj:'null')+')">✏</button>';
      var editTipsBtn = '<button class="adj-edit-btn" onclick="openAdjModal(&quot;'+g+'&quot;,&quot;'+day.date+'&quot;,&quot;tip&quot;,'+day.tips+','+(tipsAdj!==null?tipsAdj:'null')+')">✏</button>';
      var dayDisc = (day.disc || 0) > 0 ? '<span style="color:#e53935">-'+fc(day.disc)+'</span>' : '—';
      detailRows += '<tr>'+
        '<td colspan="2" style="padding-left:24px;color:#888;font-size:0.82rem">'+day.date+'</td>'+
        '<td style="text-align:right">'+fc(day.rev)+'</td>'+
        '<td style="text-align:right">'+dayDisc+'</td>'+
        '<td style="text-align:right;color:#888">'+fc(day.comm)+'</td>'+
        '<td style="text-align:right;color:#1565c0">'+fc(actualPaid)+gflag+commAdjTag+editCommBtn+'</td>'+
        '<td style="text-align:right;color:#f57c00">'+fc(tipsVal)+tipsAdjTag+editTipsBtn+'</td>'+
        '<td style="text-align:right;font-weight:600;color:#C4276E">'+fc(actualTotal)+'</td>'+
        '<td></td></tr>';
    }});
  }});
  document.getElementById('pp-detail-tbody').innerHTML = detailRows;

  // Cindy manager card
  var mgr = data._manager_salary || 0;
  var mgrOld = data._manager_old_days || 0;
  var mgrNew = data._manager_new_days || 0;
  var mgrDaily = data._manager_daily || [];
  var mgrBonus = data._manager_bonus || 0;
  var cindyCard = document.getElementById('cindy-pp-card');
  cindyCard.style.display = mgr > 0 ? 'block' : 'none';

  // Bather card — summary KPIs + per-person detail
  var batherCard = document.getElementById('bather-pp-card');
  var batherNames = Object.keys(batherPay);
  var batherTips = data._bather_tips || {{}};
  var batherTipsDaily = data._bather_tips_daily || {{}};
  var allBatherNames = Array.from(new Set(batherNames.concat(Object.keys(batherTips))));
  var batherRev = data._bather_rev || 0;
  batherCard.style.display = allBatherNames.length > 0 ? 'block' : 'none';
  if (allBatherNames.length > 0) {{{{
    var totBHrs = 0, totBTips = 0;
    allBatherNames.forEach(function(name) {{{{ totBHrs += (batherHours[name] || 0); totBTips += (batherTips[name] || 0); }}}});
    document.getElementById('bather-pp-kpis').innerHTML =
      '<div class=\"kpi\" style=\"border-color:#00796B\"><div class=\"kpi-val\" style=\"color:#00796B\">'+fc(batherRev)+'</div><div class=\"kpi-label\">Bather Revenue</div></div>'+
      '<div class=\"kpi\" style=\"border-color:#00796B\"><div class=\"kpi-val\" style=\"color:#00796B\">'+totBHrs.toFixed(1)+'h</div><div class=\"kpi-label\">Total Hours</div></div>'+
      '<div class=\"kpi\" style=\"border-color:#00796B\"><div class=\"kpi-val\" style=\"color:#00796B\">'+fc(totBatherPay)+'</div><div class=\"kpi-label\">Total Pay</div></div>'+
      '<div class=\"kpi\" style=\"border-color:#f57c00\"><div class=\"kpi-val\" style=\"color:#f57c00\">'+fc(totBTips)+'</div><div class=\"kpi-label\">Total Tips</div></div>';
    // Per-person detail
    var batherDailyData = data._bather_daily || {{}};
    var batherDetailHtml = '';
    allBatherNames.forEach(function(name) {{{{
      var hrs = batherHours[name] || 0;
      var pay = batherPay[name] || 0;
      var tips = batherTips[name] || 0;
      batherDetailHtml += '<div style=\"margin-bottom:18px\"><div style=\"font-weight:700;font-size:0.95rem;margin-bottom:8px;color:#00796B\">'+name+' — '+hrs.toFixed(2)+'h · Pay: '+fc(pay)+' · Tips: '+fc(tips)+' · Total: '+fc(pay+tips)+'</div>';
      var days = batherDailyData[name] || [];
      if (days.length > 0) {{{{
        batherDetailHtml += '<table style=\"width:100%;border-collapse:collapse;font-size:0.83rem\"><thead><tr><th style=\"text-align:left;padding:6px 10px;color:#888\">Date</th><th style=\"text-align:right;padding:6px 10px;color:#888\">Hours</th><th style=\"text-align:right;padding:6px 10px;color:#888\">Pay</th><th style=\"text-align:right;padding:6px 10px;color:#888\">Tips</th><th style=\"text-align:right;padding:6px 10px;color:#888\">Total</th></tr></thead><tbody>';
        days.forEach(function(r) {{{{
          var tipForDay = (batherTipsDaily[name] || {{}})[r.date] || 0;
          var dayPay = r.hours * 17;
          batherDetailHtml += '<tr><td>'+r.date+'</td><td style=\"text-align:right\">'+r.hours.toFixed(2)+'h</td><td style=\"text-align:right;color:#00796B\">'+fc(dayPay)+'</td><td style=\"text-align:right;color:#f57c00\">'+fc(tipForDay)+'</td><td style=\"text-align:right;font-weight:700\">'+fc(dayPay+tipForDay)+'</td></tr>';
        }}}});
        batherDetailHtml += '</tbody></table>';
      }}}}
      batherDetailHtml += '</div>';
    }}}});
    document.getElementById('bather-pp-detail').innerHTML = batherDetailHtml;
  }}}}
  // Retail staff card — summary KPIs + daily detail
  var retailCard = document.getElementById('retail-pp-card');
  var retailNames = Object.keys(retailPay);
  var totRHrs = 0;
  retailNames.forEach(function(name) {{ totRHrs += (retailHours[name] || 0); }});
  retailCard.style.display = retailNames.length > 0 ? 'block' : 'none';
  if (retailNames.length > 0) {{
    document.getElementById('retail-pp-kpis').innerHTML =
      '<div class="kpi" style="border-color:#6A1B9A"><div class="kpi-val" style="color:#6A1B9A">'+totRHrs.toFixed(1)+'h</div><div class="kpi-label">Total Hours</div></div>'+
      '<div class="kpi" style="border-color:#6A1B9A"><div class="kpi-val" style="color:#6A1B9A">'+fc(totRetailPay)+'</div><div class="kpi-label">Total Retail Wages</div></div>';
    // Per-person detail breakdown
    var retailDailyData = data._retail_daily || {{}};
    var retailDetailHtml = '';
    retailNames.forEach(function(name) {{
      var hrs = retailHours[name] || 0;
      var pay = retailPay[name] || 0;
      var rate = retailRates[name] || 0;
      retailDetailHtml += '<div style="margin-bottom:18px"><div style="font-weight:700;font-size:0.95rem;margin-bottom:8px;color:#6A1B9A">'+name+' — '+hrs.toFixed(2)+'h · $'+rate.toFixed(2)+'/hr · Pay: '+fc(pay)+'</div>';
      var days = retailDailyData[name] || [];
      if (days.length > 0) {{
        retailDetailHtml += '<table style="width:100%;border-collapse:collapse;font-size:0.83rem"><thead><tr><th style="text-align:left;padding:6px 10px;color:#888">Date</th><th style="text-align:right;padding:6px 10px;color:#888">Hours</th><th style="text-align:right;padding:6px 10px;color:#888">Rate</th><th style="text-align:right;padding:6px 10px;color:#888">Pay</th></tr></thead><tbody>';
        days.forEach(function(r) {{
          retailDetailHtml += '<tr><td>'+r.date+'</td><td style="text-align:right">'+r.hours.toFixed(2)+'h</td><td style="text-align:right">'+fc(rate)+'</td><td style="text-align:right;font-weight:600;color:#6A1B9A">'+fc(r.hours*rate)+'</td></tr>';
        }});
        retailDetailHtml += '</tbody></table>';
      }}
      retailDetailHtml += '</div>';
    }});
    document.getElementById('retail-pp-detail').innerHTML = retailDetailHtml;
  }}
  if (mgr > 0) {{
    document.getElementById('cindy-pp-kpis').innerHTML =
      '<div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">'+fc(mgr)+'</div><div class="kpi-label">Period Total</div></div>'+
      (mgrOld ? '<div class="kpi" style="border-color:#7986CB;flex:1;min-width:120px"><div class="kpi-val" style="color:#7986CB;font-size:1.4rem">'+mgrOld+'</div><div class="kpi-label">Days @ $65k</div></div>' : '')+
      (mgrNew ? '<div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">'+mgrNew+'</div><div class="kpi-label">Days @ $67k</div></div>' : '')+
      (mgrBonus ? '<div class="kpi" style="border-color:#E91E63;flex:1;min-width:120px"><div class="kpi-val" style="color:#E91E63;font-size:1.4rem">'+fc(mgrBonus)+'</div><div class="kpi-label">Bonus (Mar 1)</div></div>' : '')+
      (mgrOld ? '<div class="kpi" style="border-color:#9FA8DA;flex:1;min-width:120px"><div class="kpi-val" style="color:#9FA8DA;font-size:1.4rem">$250.00</div><div class="kpi-label">Old Rate/Day</div></div>' : '')+
      (mgrNew ? '<div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">$257.69</div><div class="kpi-label">New Rate/Day</div></div>' : '');
    var DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    var cindyRows = '';
    mgrDaily.forEach(function(r) {{
      var d = new Date(r.date+'T12:00:00');
      var raised = r.date === '2026-03-01' ? '<span style="font-size:0.72rem;color:#5C6BC0;margin-left:6px">↑ raised</span>' : '';
      var bonusCell = r.bonus ? '<strong style="color:#E91E63">'+fc(r.bonus)+'</strong>' : '—';
      cindyRows += '<tr>'+
        '<td style="font-size:0.84rem">'+r.date+'</td>'+
        '<td style="font-size:0.84rem;color:#888">'+DAYS[d.getDay()]+'</td>'+
        '<td style="text-align:right;font-size:0.84rem">'+(r.rate===65000?'$65,000':'$67,000')+raised+'</td>'+
        '<td style="text-align:right;font-weight:600;color:#5C6BC0">'+fc(r.pay)+'</td>'+
        '<td style="text-align:right">'+bonusCell+'</td>'+
        '</tr>';
    }});
    document.getElementById('cindy-pp-tbody').innerHTML = cindyRows;
  }}

  // Populate print sheet groomer dropdown with groomers who have activity this pay period
  _populatePrintGroomerSelect(ppId);
  // Re-render print sheet if a groomer is already selected
  if (document.getElementById('print-groomer-select').value) {{
    renderPrintSheet();
  }} else {{
    document.getElementById('print-sheet-body').innerHTML = '';
  }}
}}

// ── Print Sheet: per-groomer daily pay period breakdown ──────────────────
function _populatePrintGroomerSelect(ppId) {{
  var data = PP_DATA[ppId];
  if (!data) return;
  var sel = document.getElementById('print-groomer-select');
  var prior = sel.value;
  // Only include groomers with daily activity in this pay period
  var active = GROOMERS.filter(function(g) {{
    var d = data[g];
    return d && d.daily && d.daily.length > 0;
  }});
  var opts = '<option value="">Select a groomer…</option>';
  active.forEach(function(g) {{
    opts += '<option value="'+g+'"'+(g===prior?' selected':'')+'>'+g+'</option>';
  }});
  sel.innerHTML = opts;
}}

function renderPrintSheet() {{
  var DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var ppId = document.getElementById('pp-select').value;
  var groomer = document.getElementById('print-groomer-select').value;
  var body = document.getElementById('print-sheet-body');
  if (!groomer || !ppId) {{ body.innerHTML = ''; return; }}

  var data = PP_DATA[ppId];
  var sel = document.getElementById('pp-select');
  var ppLabel = sel.options[sel.selectedIndex].text;
  var d = data[groomer];
  if (!d) {{ body.innerHTML = '<div style="padding:20px;text-align:center;color:#999">No data for '+groomer+' in this pay period.</div>'; return; }}

  // Recompute totals respecting adjustments + guarantee overrides (matches main renderPayPeriod logic)
  var rows = '';
  var sumRev=0, sumDisc=0, sumComm=0, sumPaid=0, sumTips=0, sumTotal=0;
  var dayCount = 0;
  (d.daily || []).forEach(function(day) {{
    var guar = getGuarRate(groomer, day.date);
    var overrideKey = 'guar_override_'+day.date+'_'+groomer;
    var overridden = getOverride(overrideKey);
    var guarActive = guar > 0 && day.guar_applied && !overridden;
    var commVal = getAdj(groomer, day.date, 'commission'); if (commVal === null) commVal = day.comm;
    var tipsVal = getAdj(groomer, day.date, 'tip'); if (tipsVal === null) tipsVal = day.tips;
    var p = guarActive ? Math.max(commVal, guar) : commVal;
    var tot = p + tipsVal;
    sumRev += day.rev; sumDisc += (day.disc||0); sumComm += commVal;
    sumPaid += p; sumTips += tipsVal; sumTotal += tot;
    dayCount++;
    var dt = new Date(day.date+'T12:00:00');
    var dayName = DAYS[dt.getDay()];
    var guarTag = guarActive && p > commVal ? ' <span style="font-size:0.7rem;color:#1565c0">(guar)</span>' : '';
    rows += '<tr>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee">'+day.date+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;color:#888">'+dayName+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right">'+fc(day.rev)+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:'+((day.disc||0)>0?'#e53935':'#ccc')+'">'+((day.disc||0)>0?'-'+fc(day.disc):'—')+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right">'+fc(commVal)+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:600;color:#1565c0">'+fc(p)+guarTag+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:#f57c00">'+fc(tipsVal)+'</td>'+
      '<td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:700;color:#2E7D32">'+fc(tot)+'</td>'+
      '</tr>';
  }});

  body.innerHTML =
    '<div class="print-sheet-header" style="border-top:3px solid #C4276E;border-bottom:1px solid #eee;padding:18px 2px 14px;margin-bottom:16px">'+
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">'+
        '<div>'+
          '<div style="font-size:0.78rem;color:#999;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Woof Gang {_store_display} — Groomer Pay Sheet</div>'+
          '<div style="font-size:1.6rem;font-weight:800;color:#1a1a2e;margin-top:4px">'+groomer+'</div>'+
          '<div style="font-size:0.9rem;color:#666;margin-top:2px">Pay period: <strong>'+ppLabel+'</strong></div>'+
        '</div>'+
        '<div style="text-align:right">'+
          '<div style="font-size:0.72rem;color:#999;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Total Pay</div>'+
          '<div style="font-size:2rem;font-weight:800;color:#2E7D32">'+fc(sumTotal)+'</div>'+
          '<div style="font-size:0.78rem;color:#666">'+dayCount+' working day'+(dayCount===1?'':'s')+'</div>'+
        '</div>'+
      '</div>'+
    '</div>'+
    '<div class="tbl-wrap"><table style="width:100%;border-collapse:collapse;font-size:0.88rem">'+
      '<thead>'+
        '<tr style="background:#FDF0F5">'+
          '<th style="padding:10px 12px;text-align:left;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Date</th>'+
          '<th style="padding:10px 12px;text-align:left;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Day</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Revenue</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Discounts</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">50% Comm</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Paid</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Tips</th>'+
          '<th style="padding:10px 12px;text-align:right;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;color:#C4276E;border-bottom:2px solid #C4276E">Day Total</th>'+
        '</tr>'+
      '</thead>'+
      '<tbody>'+rows+'</tbody>'+
      '<tfoot>'+
        '<tr style="background:#FDF0F5">'+
          '<td colspan="2" style="padding:12px;font-weight:700;color:#1a1a2e;border-top:2px solid #C4276E">TOTAL ('+dayCount+' days)</td>'+
          '<td style="padding:12px;text-align:right;font-weight:700;border-top:2px solid #C4276E">'+fc(sumRev)+'</td>'+
          '<td style="padding:12px;text-align:right;font-weight:700;color:'+(sumDisc>0?'#e53935':'#ccc')+';border-top:2px solid #C4276E">'+(sumDisc>0?'-'+fc(sumDisc):'—')+'</td>'+
          '<td style="padding:12px;text-align:right;font-weight:700;border-top:2px solid #C4276E">'+fc(sumComm)+'</td>'+
          '<td style="padding:12px;text-align:right;font-weight:800;color:#1565c0;border-top:2px solid #C4276E">'+fc(sumPaid)+'</td>'+
          '<td style="padding:12px;text-align:right;font-weight:800;color:#f57c00;border-top:2px solid #C4276E">'+fc(sumTips)+'</td>'+
          '<td style="padding:12px;text-align:right;font-weight:800;color:#2E7D32;font-size:1.05rem;border-top:2px solid #C4276E">'+fc(sumTotal)+'</td>'+
        '</tr>'+
      '</tfoot>'+
    '</table></div>'+
    '<div class="print-footer" style="margin-top:20px;padding-top:14px;border-top:1px solid #eee;display:flex;justify-content:space-between;font-size:0.72rem;color:#999">'+
      '<div>Generated '+new Date().toLocaleString()+'</div>'+
      '<div>Woof Gang Bakery &amp; Grooming — {_store_display}</div>'+
    '</div>';
}}

// Init pay period on load
renderPayPeriod(document.getElementById('pp-select').value);

// ── Sue M Weekly Tab ──────────────────────────────────────────────────────
var _sueRendered = false;
function renderSue() {{
  if (_sueRendered) return;
  _sueRendered = true;

  // KPIs
  var totalTips = 0, totalOwed = 0;
  SUE_WEEKLY.forEach(function(w) {{
    totalTips += w.tips;
    totalOwed += w.owed;
  }});
  var totalNet = totalTips - totalOwed;

  document.getElementById('sue-kpis').innerHTML =
    '<div class="kpi orange"><div class="kpi-val">'+fc(totalTips)+'</div><div class="kpi-label">Total Tips</div></div>'+
    '<div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">'+fc(totalOwed)+'</div><div class="kpi-label">Product Purchases (80%)</div></div>'+
    '<div class="kpi green"><div class="kpi-val">'+fc(totalNet)+'</div><div class="kpi-label">Net Tips</div></div>';

  // Weekly rows
  var html = '<div class="card"><div class="stitle">Weekly Summary</div><div class="tbl-wrap"><table>'+
    '<thead><tr><th>Week</th><th style="text-align:right">Tips</th><th style="text-align:right">Purchases (80%)</th><th style="text-align:right">Net</th></tr></thead><tbody>';
  SUE_WEEKLY.forEach(function(w, i) {{
    var purchases = w.purchases || [];
    var netColor = w.net >= 0 ? '#388E3C' : '#e53935';
    html += '<tr>'+
      '<td style="font-weight:600">'+w.label+'</td>'+
      '<td style="text-align:right;color:#f57c00">'+fc(w.tips)+'</td>'+
      '<td style="text-align:right;color:#6D4C41">'+(w.owed > 0 ? fc(w.owed) : '—')+'</td>'+
      '<td style="text-align:right;font-weight:700;color:'+netColor+'">'+fc(w.net)+'</td>'+
      '</tr>';
    // Show purchase detail rows
    if (purchases.length > 0) {{
      purchases.forEach(function(p) {{
        html += '<tr style="background:#fff8f0">'+
          '<td colspan="1" style="padding-left:24px;font-size:0.8rem;color:#888">'+p.date+'</td>'+
          '<td colspan="2" style="font-size:0.8rem;color:#666">'+p.items+'</td>'+
          '<td style="text-align:right;font-size:0.8rem;color:#6D4C41">'+fc(p.owed)+' <span style="color:#aaa;font-size:0.72rem">('+fc(p.subtotal)+' × 80%)</span></td>'+
          '</tr>';
      }});
    }}
  }});
  html += '</tbody></table></div></div>';
  document.getElementById('sue-weeks-container').innerHTML = html;
}}

// ── Carol W Weekly Payroll Tab (Hicksville) ───────────────────────────────
var _carolRendered = false;
function renderCarol() {{
  if (_carolRendered) return;
  _carolRendered = true;

  var CAROL_PAYROLL = 100; // $100/week goes on payroll

  // Build pay period label map from the select element
  var sel = document.getElementById('pp-select');
  var labelMap = {{}};
  for (var i = 0; i < sel.options.length; i++) {{
    labelMap[sel.options[i].value] = sel.options[i].text;
  }}

  // Gather Carol's data across all pay periods (PP_DATA is most-recent-first)
  var carolPeriods = [];
  var totalEarned = 0, totalPayroll = 0, totalOffBooks = 0;

  Object.keys(PP_DATA).forEach(function(ppId) {{
    var d = PP_DATA[ppId]['Carol W'];
    if (!d || (d.total <= 0 && d.rev <= 0)) return;
    var total = d.total; // comm paid + tips
    var payroll = CAROL_PAYROLL;
    var offBook = total - CAROL_PAYROLL;
    totalEarned  += total;
    totalPayroll += payroll;
    totalOffBooks += offBook;
    carolPeriods.push({{
      label: labelMap[ppId] || ppId,
      rev:  d.rev,
      comm: d.paid,
      tips: d.tips,
      total: total,
      payroll: payroll,
      offBook: offBook,
      days: d.working_days
    }});
  }});

  // KPI cards
  document.getElementById('carol-kpis').innerHTML =
    '<div class="kpi green"><div class="kpi-val">'+fc(totalEarned)+'</div><div class="kpi-label">Total Earned</div></div>'+
    '<div class="kpi blue"><div class="kpi-val">'+fc(totalPayroll)+'</div><div class="kpi-label">Total on Payroll</div></div>'+
    '<div class="kpi orange"><div class="kpi-val">'+fc(totalOffBooks)+'</div><div class="kpi-label">Total Off-Books (Check)</div></div>'+
    '<div class="kpi grey"><div class="kpi-val">'+carolPeriods.length+'</div><div class="kpi-label">Working Weeks</div></div>';

  // Table
  var html = '<div class="card"><div class="stitle">Weekly Payroll Breakdown — Carol W</div><div class="tbl-wrap"><table>'+
    '<thead><tr>'+
    '<th>Pay Period</th>'+
    '<th style="text-align:right">Days</th>'+
    '<th style="text-align:right">Groom Revenue</th>'+
    '<th style="text-align:right">Commission</th>'+
    '<th style="text-align:right">Tips</th>'+
    '<th style="text-align:right">Total Earned</th>'+
    '<th style="text-align:right">On Payroll</th>'+
    '<th style="text-align:right">Off-Books (Check)</th>'+
    '</tr></thead><tbody>';

  carolPeriods.forEach(function(p) {{
    var offColor = p.offBook < 0 ? '#e53935' : '#F57C00';
    html += '<tr>'+
      '<td style="font-weight:600">'+p.label+'</td>'+
      '<td style="text-align:right">'+p.days+'</td>'+
      '<td style="text-align:right">'+fc(p.rev)+'</td>'+
      '<td style="text-align:right;color:#388E3C">'+fc(p.comm)+'</td>'+
      '<td style="text-align:right;color:#F57C00">'+(p.tips > 0 ? fc(p.tips) : '—')+'</td>'+
      '<td style="text-align:right;font-weight:700">'+fc(p.total)+'</td>'+
      '<td style="text-align:right;color:#1976D2">'+fc(p.payroll)+'</td>'+
      '<td style="text-align:right;font-weight:700;color:'+offColor+'">'+fc(p.offBook)+'</td>'+
      '</tr>';
  }});

  html += '</tbody></table></div></div>';
  document.getElementById('carol-weeks-container').innerHTML = html;
}}

// ── Executive Dashboard ───────────────────────────────────────────────────
var _execCharts = {{}};
function renderExec() {{
  var ytd = MONTHLY_DATA.filter(function(m) {{ return m.year === 2026; }});
  var all = MONTHLY_DATA;

  // KPIs - YTD 2026
  var ytdRev = ytd.reduce(function(a,m){{return a+m.rev;}},0);
  var ytdPaid = ytd.reduce(function(a,m){{return a+m.paid;}},0);
  var ytdMgr = ytd.reduce(function(a,m){{return a+m.mgr;}},0);
  var ytdBather = ytd.reduce(function(a,m){{return a+(m.bather_pay||0);}},0);
  var ytdRetail = ytd.reduce(function(a,m){{return a+(m.retail_pay||0);}},0);
  var ytdRoyalties = ytd.reduce(function(a,m){{return a+(m.royalties||0);}},0);
  var ytdRent = ytd.reduce(function(a,m){{return a+(m.rent||0);}},0);
  var ytdMargin = ytd.reduce(function(a,m){{return a+m.margin;}},0);
  var ytdMarginPct = ytdRev ? (ytdMargin/ytdRev*100).toFixed(1) : 0;
  var ytdRevDay = ytd.reduce(function(a,m){{return a+m.working_days;}},0);
  ytdRevDay = ytdRevDay ? (ytdRev/ytdRevDay).toFixed(0) : 0;

  document.getElementById('exec-kpis').innerHTML =
    kpiCard('2026 YTD Revenue', fc(ytdRev), '#C4276E') +
    kpiCard('Groomer Commission', fc(ytdPaid), '#1565c0') +
    kpiCard('Bather Pay', fc(ytdBather), '#00796B') +
    kpiCard(ytdMgr > 0 ? 'Manager + Retail Staff' : 'Retail Staff Pay', fc(ytdMgr + ytdRetail), '#7B1FA2') +
    kpiCard('Royalties + Marketing', fc(ytdRoyalties), '#AD1457') +
    kpiCard('Rent', fc(ytdRent), '#6D4C41') +
    kpiCard('Net Margin', fc(ytdMargin) + ' ('+ytdMarginPct+'%)', parseFloat(ytdMarginPct) >= 0 ? '#558B2F' : '#e53935');

  // Monthly table
  var tbody = '';
  all.forEach(function(m) {{
    var marginColor = m.margin < 0 ? '#e53935' : '#558B2F';
    var yearBg = m.year === 2025 ? '' : 'background:#fdf8fb';
    tbody += '<tr style="border-bottom:1px solid #f5f5f5;'+yearBg+'">'+
      '<td style="padding:8px 12px;font-weight:600">'+m.month+'</td>'+
      '<td style="text-align:right;padding:8px 12px;font-weight:700;color:#C4276E">'+fc(m.rev)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#1565c0">'+fc(m.paid)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#5C6BC0">'+fc(m.mgr)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#00796B">'+fc(m.bather_pay||0)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#6A1B9A">'+fc(m.retail_pay||0)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#AD1457">'+fc(m.royalties||0)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;color:#6D4C41">'+fc(m.rent||0)+'</td>'+
      '<td style="text-align:right;padding:8px 12px;font-weight:700;color:'+marginColor+'">'+fc(m.margin)+'<br><span style="font-size:0.72rem;font-weight:400">'+m.margin_pct+'%</span></td>'+
      '<td style="text-align:right;padding:8px 12px;color:#888">'+fc(m.rev_per_day)+'</td>'+
      '</tr>';
  }});
  document.getElementById('exec-monthly-tbody').innerHTML = tbody;

  // ── Margin Charts: 2025 and 2026 side by side ──
  function buildMarginChart(canvasId, yearData) {{
    if (_execCharts[canvasId]) {{ _execCharts[canvasId].destroy(); }}
    var labels = yearData.map(function(m){{return m.month.replace(' 2025','').replace(' 2026','');}});
    var commissions = yearData.map(function(m){{return m.paid;}});
    var managers    = yearData.map(function(m){{return m.mgr;}});
    var bathers     = yearData.map(function(m){{return m.bather_pay||0;}});
    var retails     = yearData.map(function(m){{return m.retail_pay||0;}});
    var royalties   = yearData.map(function(m){{return m.royalties||0;}});
    var rents       = yearData.map(function(m){{return m.rent||0;}});
    var margins     = yearData.map(function(m){{return m.margin;}});
    var revs        = yearData.map(function(m){{return m.rev;}});

    _execCharts[canvasId] = new Chart(document.getElementById(canvasId), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [
          {{label:'Commission',  data:commissions, backgroundColor:'rgba(21,101,192,0.75)',  stack:'costs'}},
          {{label:'Manager',     data:managers,    backgroundColor:'rgba(92,107,192,0.75)',  stack:'costs'}},
          {{label:'Bather',      data:bathers,     backgroundColor:'rgba(0,121,107,0.75)',   stack:'costs'}},
          {{label:'Retail Staff',data:retails,     backgroundColor:'rgba(106,27,154,0.75)',  stack:'costs'}},
          {{label:'Royalties',   data:royalties,   backgroundColor:'rgba(173,20,87,0.75)',   stack:'costs'}},
          {{label:'Rent',        data:rents,       backgroundColor:'rgba(109,76,65,0.75)',   stack:'costs'}},
          {{label:'Net Margin',  data:margins,     backgroundColor: margins.map(function(v){{return v>=0?'rgba(85,139,47,0.85)':'rgba(229,57,53,0.85)'}}), stack:'costs'}},
          {{label:'Revenue',     data:revs,        type:'line', borderColor:'#C4276E', backgroundColor:'transparent',
            borderWidth:2, pointRadius:4, pointBackgroundColor:'#C4276E', order:0, yAxisID:'y'}}
        ]
      }},
      options: {{
        responsive:true,
        interaction:{{mode:'index',intersect:false}},
        plugins:{{
          legend:{{position:'bottom', labels:{{font:{{size:11}},boxWidth:12,padding:10}}}},
          tooltip:{{callbacks:{{label:function(ctx){{
            return ctx.dataset.label+': $'+parseFloat(ctx.raw).toLocaleString('en-US',{{minimumFractionDigits:0,maximumFractionDigits:0}});
          }}}}}}
        }},
        scales:{{
          x:{{stacked:true}},
          y:{{stacked:true, ticks:{{callback:function(v){{return '$'+v.toLocaleString();}}}}}}
        }}
      }}
    }});
  }}

  var data2025 = all.filter(function(m){{return m.year===2025;}});
  var data2026 = all.filter(function(m){{return m.year===2026;}});
  buildMarginChart('chart-margin-2025', data2025);
  buildMarginChart('chart-margin-2026', data2026);
}}

function kpiCard(label, val, color) {{
  return '<div style="background:white;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);border-top:3px solid '+color+'">'+
    '<div style="font-size:1.4rem;font-weight:800;color:'+color+'">'+val+'</div>'+
    '<div style="font-size:0.82rem;color:#888;margin-top:4px">'+label+'</div>'+
    '</div>';
}}

// ── Commission / Tip Adjustment Modal ────────────────────────────────────────
var _adjModal = {{g:'', date:'', type:'', original:0}};

function openAdjModal(g, date, type, original, current) {{
  _adjModal = {{g:g, date:date, type:type, original:original}};
  document.getElementById('adj-modal-title').textContent =
    'Adjust ' + (type==='commission'?'Commission':'Tips') + ' \u2014 ' + g + ' on ' + date;
  document.getElementById('adj-original').textContent = 'FranPOS value: $' + parseFloat(original).toFixed(2);
  document.getElementById('adj-amount').value = current !== null ? parseFloat(current).toFixed(2) : parseFloat(original).toFixed(2);
  document.getElementById('adj-note').value = '';
  document.getElementById('adj-clear-btn').style.display = current !== null ? 'inline-block' : 'none';
  document.getElementById('adj-overlay').classList.remove('hidden');
  setTimeout(function() {{
    var inp = document.getElementById('adj-amount');
    inp.focus(); inp.select();
  }}, 50);
}}

function closeAdjModal() {{
  document.getElementById('adj-overlay').classList.add('hidden');
}}

function saveAdj() {{
  var amount = parseFloat(document.getElementById('adj-amount').value);
  if (isNaN(amount) || amount < 0) {{ alert('Enter a valid amount (0 or more)'); return; }}
  var note = document.getElementById('adj-note').value.trim();
  var btn = document.getElementById('adj-save-btn');
  btn.disabled = true; btn.textContent = 'Saving\u2026';
  var hdrs = Object.assign({{}}, _sbHeaders, {{'Prefer': 'return=minimal,resolution=merge-duplicates'}});
  fetch(_sbUrl + '/rest/v1/commission_adjustments', {{
    method: 'POST', headers: hdrs,
    body: JSON.stringify({{
      store_key: _storeKey,
      groomer_name: _adjModal.g,
      adj_date: _adjModal.date,
      adj_type: _adjModal.type,
      corrected_amount: amount,
      note: note || null
    }})
  }}).then(function(r) {{
    btn.disabled = false; btn.textContent = 'Save';
    if (r.ok || r.status === 201 || r.status === 200) {{ closeAdjModal(); _loadOverrides(); }}
    else {{ r.text().then(function(t) {{ alert('Save failed: ' + t); }}); }}
  }}).catch(function(e) {{ btn.disabled = false; btn.textContent = 'Save'; alert('Save failed'); }});
}}

function clearAdj() {{
  var btn = document.getElementById('adj-clear-btn');
  btn.disabled = true;
  fetch(_sbUrl + '/rest/v1/commission_adjustments?store_key=eq.' + encodeURIComponent(_storeKey) +
    '&groomer_name=eq.' + encodeURIComponent(_adjModal.g) +
    '&adj_date=eq.' + _adjModal.date +
    '&adj_type=eq.' + _adjModal.type, {{
    method: 'DELETE', headers: _sbHeaders
  }}).then(function() {{ btn.disabled = false; closeAdjModal(); _loadOverrides(); }})
    .catch(function() {{ btn.disabled = false; alert('Clear failed'); }});
}}
</script>

<!-- Commission/Tip Adjustment Modal -->
<div class="adj-overlay hidden" id="adj-overlay" onclick="if(event.target===this)closeAdjModal()">
  <div class="adj-modal">
    <div class="adj-modal-hdr">
      <h3 id="adj-modal-title">Adjust Commission</h3>
      <div class="adj-orig" id="adj-original"></div>
    </div>
    <div class="adj-modal-body">
      <div class="adj-fg">
        <label>Corrected Amount ($)</label>
        <input type="number" id="adj-amount" min="0" step="0.01" placeholder="0.00">
      </div>
      <div class="adj-fg">
        <label>Note (optional)</label>
        <textarea id="adj-note" placeholder="Reason for correction\u2026"></textarea>
      </div>
    </div>
    <div class="adj-modal-actions">
      <button class="adj-clear-btn" id="adj-clear-btn" onclick="clearAdj()" style="display:none;margin-right:auto">Clear Adjustment</button>
      <button class="adj-cancel-btn" onclick="closeAdjModal()">Cancel</button>
      <button class="adj-save-btn" id="adj-save-btn" onclick="saveAdj()">Save</button>
    </div>
  </div>
</div>
</body></html>'''

# Inject Supabase credentials
html = html.replace("{supabase_url}", SUPABASE_URL)
html = html.replace("{supabase_key}", SUPABASE_ANON_KEY)

_fn_store = _store_display.replace(" ", "")
out_path = OUTPUT_DIR / f"WoofGang_{_fn_store}_Commission_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"Pay periods built: {len(pay_periods)}")
print(f"Most recent: {period_label(*pay_periods[0])}")
print(f"Oldest: {period_label(*pay_periods[-1])}")
print(f"YTD: Rev={fc(ytd_total['rev'])} | Paid={fc(ytd_total['paid'])} | Tips={fc(ytd_total['tips'])} | Total={fc(ytd_total['total'])}")
