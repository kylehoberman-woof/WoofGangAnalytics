# v2.1
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    get_store, GUARANTEES, COMMISSION_RATE, EXCLUDE_EMPLOYEES as EXCLUDE,
    BATHER_RATE, RETAIL_RATES, RETAIL_NAME_MAP, BATHER_NAME_MAP,
    HICKSVILLE_RETAIL_RATES, HICKSVILLE_RETAIL_NAME_MAP, HICKSVILLE_BATHER_NAME_MAP,
    MANAGER_SALARY_OLD, MANAGER_SALARY_NEW, MANAGER_RAISE_DATE,
    MANAGER_BONUS_DATE, MANAGER_BONUS, MANAGER_START, MANAGER_NAME,
    MONTHLY_RENT as _DEFAULT_RENT, ANCHOR_START, STORE_OPEN,
    SUPABASE_URL, SUPABASE_ANON_KEY, STORE_RENT,
)
from formatting import fc

SCRIPTS_DIR = Path(__file__).parent
_store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = "Port Washington" if _store_name == "port-washington" else "Hicksville"
_home_url = "index.html" if _store_name == "port-washington" else "../port-washington/index.html"
MONTHLY_RENT = STORE_RENT.get(_store_name, _DEFAULT_RENT)

# Store-specific employee maps
if _store_name == "hicksville":
    RETAIL_NAME_MAP = HICKSVILLE_RETAIL_NAME_MAP
    RETAIL_RATES = HICKSVILLE_RETAIL_RATES
    BATHER_NAME_MAP = HICKSVILLE_BATHER_NAME_MAP

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

# ── Pay periods: bi-weekly Mon–Sun, anchor = Feb 23 2026 ─────────────────────
TODAY        = date.today()

def build_pay_periods():
    periods = []
    start = ANCHOR_START
    # Go back to store open
    while start > STORE_OPEN:
        start -= timedelta(days=14)
    # Build forward
    while start <= TODAY:
        end = start + timedelta(days=13)
        if end >= STORE_OPEN and start <= TODAY:
            periods.append((start, end))  # show full 14-day period, data capped by TODAY naturally
        start += timedelta(days=14)
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

DAILY_RENT   = MONTHLY_RENT * 12 / 365  # kept for pay period calc
ytd_days_count = (TODAY - ytd_start).days + 1
ytd_months = TODAY.month  # number of months in YTD (Jan=1, Feb=2, etc.)
ytd_rent = MONTHLY_RENT * ytd_months

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
ytd_bather_pay = round(sum(h * BATHER_RATE for h in ytd_bather_hours.values()), 2)
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
    m_rent = MONTHLY_RENT  # flat monthly rent
    m_royalties = round(m_rev * 0.07, 2)
    # Bather pay for this month from time clocks
    s_str = m_start.strftime("%Y-%m-%d")
    e_str = m_end.strftime("%Y-%m-%d")
    m_bather_hours, _ = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, s_str, e_str)
    m_bather_pay = round(sum(h * BATHER_RATE for h in m_bather_hours.values()), 2)
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
        name: round(hrs * BATHER_RATE, 2)
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
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"DM Sans",sans-serif;background:#f5f4f0;color:#1a1a1a}}
.topbar{{background:#C4276E;padding:18px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3)}}
.topbar-title{{color:white;font-size:1.1rem;font-weight:700}}.topbar-sub{{color:rgba(255,255,255,0.75);font-size:0.82rem;margin-top:2px}}
.topbar-date{{color:rgba(255,255,255,0.85);font-size:0.82rem;font-family:"DM Mono",monospace}}
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
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div class="topbar">
  <div><div style="display:flex;align-items:center;gap:12px"><a href="{_home_url}" style="color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.82rem;font-weight:600">&larr; Home</a><div class="topbar-title">💅 Woof Gang {_store_display} — Groomer Commission</div></div>
  <div class="topbar-sub">50% commission · Guarantees: Maria C $200/day · Sue M $300/day</div></div>
  <div class="topbar-date">Updated {now_et}</div>
</div>

<div class="tab-bar">
  <button class="tab active" onclick="showTab('ytd',this)">2026 YTD</button>
  <button class="tab" onclick="showTab('l30',this)">Last 30 Days</button>
  <button class="tab" onclick="showTab('pp',this)">Pay Period</button>
  <button class="tab" onclick="showTab('exec',this)">&#128200; Executive</button>
  {_sue_tab_btn}
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
    <div class="kpi" style="border-color:#AD1457"><div class="kpi-val" style="color:#AD1457">{fc(ytd_total["rev"] * 0.07)}</div><div class="kpi-label">Royalties (7%)</div></div>
    <div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">{fc(ytd_rent)}</div><div class="kpi-label">Rent</div><div style="font-size:0.78rem;color:#6D4C41;margin-top:3px">{ytd_days_count} days</div></div>
    <div class="kpi" style="border-color:#00838F"><div class="kpi-val" style="color:#00838F">{fc(ytd_total["rev"] - ytd_total["disc"] - ytd_total["paid"] - ytd_manager - ytd_bather_pay - ytd_retail_pay - ytd_total["rev"] * 0.07 - ytd_rent)}</div><div class="kpi-label">Margin</div><div style="font-size:0.78rem;color:#00838F;margin-top:3px;font-weight:600">{(ytd_total["rev"] - ytd_total["disc"] - ytd_total["paid"] - ytd_manager - ytd_bather_pay - ytd_retail_pay - ytd_total["rev"] * 0.07 - ytd_rent) / (ytd_total["rev"] or 1) * 100:.1f}%</div></div>
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
</div>

</div>

{_sue_panel}

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
var _sbUrl = '{{supabase_url}}';
var _sbKey = '{{supabase_key}}';
var _sbHeaders = {{
  'apikey': _sbKey,
  'Authorization': 'Bearer ' + _sbKey,
  'Content-Type': 'application/json',
  'Prefer': 'return=minimal'
}};

function _loadOverrides() {{
  fetch(_sbUrl + '/rest/v1/guarantee_overrides?select=groomer_name,override_date,waived', {{
    headers: {{ 'apikey': _sbKey, 'Authorization': 'Bearer ' + _sbKey }}
  }})
  .then(function(r) {{ return r.ok ? r.json() : []; }})
  .then(function(rows) {{
    _overrides = {{}};
    rows.forEach(function(row) {{
      if (row.waived) {{
        _overrides['guar_override_' + row.override_date + '_' + row.groomer_name] = true;
      }}
    }});
    renderPayPeriod(document.getElementById('pp-select').value);
  }})
  .catch(function() {{
    renderPayPeriod(document.getElementById('pp-select').value);
  }});
}}
_loadOverrides();

function getOverride(key) {{
  return _overrides[key] === true;
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
    var adjPaid = d.paid, adjTotal = d.total;
    if (GUARANTEES[g] && d.daily) {{
      adjPaid = 0; adjTotal = 0;
      d.daily.forEach(function(day) {{
        var guar = getGuarRate(g, day.date);
        var overrideKey = 'guar_override_'+day.date+'_'+g;
        var overridden = getOverride(overrideKey);
        var guarActive = guar > 0 && day.guar_applied && !overridden;
        var p = guarActive ? Math.max(day.comm, guar) : day.comm;
        adjPaid += p; adjTotal += p + day.tips;
      }});
      // add tip-only days
      adjTotal += (d.tips - (d.daily ? d.daily.reduce(function(a,b){{return a+b.tips;}},0) : 0));
    }}
    totRev += d.rev; totPaid += adjPaid; totTips += d.tips; totTotal += adjTotal;
    totGuar += d.guar_days;
  }});
  totRev += (data._bather_rev || 0);
  var totRoyalties = totRev * 0.07;
  var DAILY_RENT = 7700.0 * 12 / 365;
  var totRent = DAILY_RENT * 14;
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
    '<div class="kpi" style="border-color:#AD1457"><div class="kpi-val" style="color:#AD1457">'+fc(totRoyalties)+'</div><div class="kpi-label">Royalties (7%)</div></div>'+
    '<div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">'+fc(totRent)+'</div><div class="kpi-label">Rent</div><div style="font-size:0.78rem;color:#6D4C41;margin-top:3px">14 days</div></div>'+
    '<div class="kpi" style="border-color:#00838F"><div class="kpi-val" style="color:#00838F">'+fc(totMargin)+'</div><div class="kpi-label">Margin</div><div style="font-size:0.78rem;color:#00838F;margin-top:3px;font-weight:600">'+totMarginPct+'%</div></div>'+
    '<div class="kpi grey"><div class="kpi-val">'+totGuar+'</div><div class="kpi-label">Guarantee Days</div></div>';

  // Summary rows
  var sorted = GROOMERS.slice().sort(function(a,b) {{ return (data[b]||{{total:0}}).total - (data[a]||{{total:0}}).total; }});
  var rows = '';
  sorted.forEach(function(g) {{
    var d = data[g]; if (!d || (d.rev===0 && d.tips===0)) return;
    var guarNote = d.guar_days ? '<br><span style="font-size:0.72rem;color:#1565c0">↑ '+d.guar_days+' guar days</span>' : '';
    var avgDay = d.working_days ? fc(d.total/d.working_days) : '—';
    var discCell = (d.disc || 0) > 0 ? '<span style="color:#e53935">-'+fc(d.disc)+'</span>' : '—';
    rows += '<tr>'+
      '<td>'+groomerBadge(g)+'</td>'+
      '<td style="text-align:right;color:#888;font-weight:600">'+d.working_days+'</td>'+
      '<td style="text-align:right">'+fc(d.rev)+'</td>'+
      '<td style="text-align:right">'+discCell+'</td>'+
      '<td style="text-align:right;color:#888">'+fc(d.comm)+'</td>'+
      '<td style="text-align:right;color:#1565c0;font-weight:600">'+fc(d.paid)+guarNote+'</td>'+
      '<td style="text-align:right;color:#f57c00">'+fc(d.tips)+'</td>'+
      '<td style="text-align:right;font-weight:700;color:#C4276E">'+fc(d.total)+'</td>'+
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
      var actualPaid = guarActive ? Math.max(day.comm, guar) : day.comm;
      var actualTotal = actualPaid + day.tips;
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
      var dayDisc = (day.disc || 0) > 0 ? '<span style="color:#e53935">-'+fc(day.disc)+'</span>' : '—';
      detailRows += '<tr>'+
        '<td colspan="2" style="padding-left:24px;color:#888;font-size:0.82rem">'+day.date+'</td>'+
        '<td style="text-align:right">'+fc(day.rev)+'</td>'+
        '<td style="text-align:right">'+dayDisc+'</td>'+
        '<td style="text-align:right;color:#888">'+fc(day.comm)+'</td>'+
        '<td style="text-align:right;color:#1565c0">'+fc(actualPaid)+gflag+'</td>'+
        '<td style="text-align:right;color:#f57c00">'+fc(day.tips)+'</td>'+
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
    kpiCard('Royalties (7%)', fc(ytdRoyalties), '#AD1457') +
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
</script>
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
