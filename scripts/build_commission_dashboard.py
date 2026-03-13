import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date, timedelta

DATA_DIR   = Path("/home/claude/port-washington/data")
OUTPUT_DIR = Path("/home/claude/port-washington")

GUARANTEES = {"Maria C": 200.0, "Sue M": 300.0}
COMMISSION_RATE = 0.50
EXCLUDE = {"Unknown", "Wgb Port Washington", "Kyle Hoberman"}

MANAGER_SALARY_OLD    = 65000.0   # Mar 1 2025 – Feb 28 2026
MANAGER_SALARY_NEW    = 67000.0   # Mar 1 2026 onward
MANAGER_RAISE_DATE    = date(2026, 3, 1)
MANAGER_BONUS_DATE    = date(2026, 3, 1)
MANAGER_BONUS         = 2000.0
MANAGER_START         = date(2025, 3, 1)
MANAGER_NAME          = "Cindy Szczudlo"

# ── Load all data ─────────────────────────────────────────────────────────────
groom_by_day  = defaultdict(lambda: defaultdict(float))
order_groomer = {}
order_date    = {}
tips_by_order = {}

for yr in ["2024", "2025", "2026"]:
    p = DATA_DIR / f"all_data_{yr}.json"
    if not p.exists(): continue
    with open(p) as f:
        data = json.load(f)
    for item in data["order_items"]:
        sku    = str(item.get("Sku",""))
        person = item.get("SalesPerson","") or "Unknown"
        price  = float(item.get("Price") or 0)
        qty    = float(item.get("Quantity") or 0)
        day    = (item.get("CreatedOn") or "")[:10]
        oid    = item.get("OrderId")
        if sku.startswith("987") and person not in EXCLUDE:
            groom_by_day[person][day] += price * qty
            if oid: order_groomer[oid] = person
    for o in data["orders"]:
        oid = o.get("OrderId")
        if oid:
            order_date[oid] = (o.get("CreatedOn") or "")[:10]
            tip = float(o.get("Tips") or 0)
            if tip > 0: tips_by_order[oid] = tip

# Tips → groomer by day
tips_by_day = defaultdict(lambda: defaultdict(float))
for oid, tip in tips_by_order.items():
    groomer = order_groomer.get(oid)
    day = order_date.get(oid, "")
    if groomer and day:
        tips_by_day[groomer][day] += tip

groomers = sorted(groom_by_day.keys())

# ── Pay periods: bi-weekly Mon–Sun, anchor = Feb 23 2026 ─────────────────────
ANCHOR_START = date(2026, 2, 23)
STORE_OPEN   = date(2024, 9, 26)
TODAY        = date(2026, 3, 8)

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
            actual_end = min(end, TODAY)
            periods.append((start, actual_end))
        start += timedelta(days=14)
    return list(reversed(periods))  # most recent first

pay_periods = build_pay_periods()

# ── Commission helpers ────────────────────────────────────────────────────────
def day_pay(groomer, day):
    rev  = groom_by_day[groomer].get(day, 0)
    tips = tips_by_day[groomer].get(day, 0)
    comm = rev * COMMISSION_RATE
    guar = GUARANTEES.get(groomer, 0)
    paid = max(comm, guar) if guar else comm
    return {"rev": rev, "comm": comm, "paid": paid, "tips": tips,
            "total": paid + tips, "guar_applied": guar > 0 and comm < guar}

def period_summary(groomer, start, end):
    days = [d for d in sorted(groom_by_day[groomer].keys())
            if start.isoformat() <= d <= end.isoformat()]
    # Also include days with only tips
    tip_days = [d for d in sorted(tips_by_day[groomer].keys())
                if start.isoformat() <= d <= end.isoformat() and d not in days]
    all_days = sorted(set(days + tip_days))
    
    t = {"rev":0,"comm":0,"paid":0,"tips":0,"total":0,"working_days":0,"guar_days":0,"daily":[]}
    for day in all_days:
        d = day_pay(groomer, day)
        if d["rev"] > 0 or d["tips"] > 0:
            t["rev"]   += d["rev"]
            t["comm"]  += d["comm"]
            t["paid"]  += d["paid"]
            t["tips"]  += d["tips"]
            t["total"] += d["total"]
            t["working_days"] += 1
            if d["guar_applied"]: t["guar_days"] += 1
            t["daily"].append({"date": day, **d})
    return t

def fc(v): return f"${v:,.2f}"
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
             for k in ["rev","comm","paid","tips","total","working_days","guar_days"]}

MONTHLY_RENT = 7700.0
DAILY_RENT   = MONTHLY_RENT * 12 / 365
ytd_days_count = (TODAY - ytd_start).days + 1
ytd_rent = round(DAILY_RENT * ytd_days_count, 2)

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

ytd_manager, ytd_mgr_old_days, ytd_mgr_new_days, ytd_mgr_daily, ytd_mgr_bonus = manager_salary_for_range(ytd_start, TODAY)

# Last 30 days
l30_start = TODAY - timedelta(days=29)
l30_data = {g: period_summary(g, l30_start, TODAY) for g in groomers}

def groomer_badge(g):
    guar = GUARANTEES.get(g)
    badge = f' <span style="background:#e3f2fd;color:#1565c0;padding:1px 6px;border-radius:6px;font-size:0.7rem;font-weight:700">G${guar:.0f}</span>' if guar else ""
    dot = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{groomer_color[g]};margin-right:7px"></span>'
    return f"{dot}<strong>{g}</strong>{badge}"

def summary_table(data_dict, show_groomers=None):
    rows = ""
    groomers_to_show = show_groomers or sorted(groomers, key=lambda x: -data_dict[x]["total"])
    for g in groomers_to_show:
        d = data_dict[g]
        if d["rev"] == 0 and d["tips"] == 0: continue
        rows += f'''<tr>
          <td>{groomer_badge(g)}</td>
          <td style="text-align:right;color:#888;font-weight:600">{d["working_days"]}</td>
          <td style="text-align:right">{fc(d["rev"])}</td>
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
        rows += f'<tr style="background:#f8f7f4"><td colspan="7" style="padding:10px 14px;font-weight:700">{groomer_badge(g)}</td></tr>'
        for day in d["daily"]:
            gflag = '<span style="background:#e3f2fd;color:#1565c0;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:6px">guarantee</span>' if day["guar_applied"] else ""
            rows += f'''<tr>
              <td style="padding-left:24px;color:#888;font-size:0.82rem">{day["date"]}</td>
              <td style="text-align:right">{fc(day["rev"])}</td>
              <td style="text-align:right;color:#888">{fc(day["comm"])}</td>
              <td style="text-align:right;color:#1565c0">{fc(day["paid"])}{gflag}</td>
              <td style="text-align:right;color:#f57c00">{fc(day["tips"])}</td>
              <td style="text-align:right;font-weight:600;color:#C4276E">{fc(day["total"])}</td>
              <td></td><td></td>
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
    mgr_pay, mgr_old, mgr_new, mgr_daily, mgr_bonus = manager_salary_for_range(s, e)
    pp_data[f"pp_{i}"]["_manager_salary"] = mgr_pay
    pp_data[f"pp_{i}"]["_manager_old_days"] = mgr_old
    pp_data[f"pp_{i}"]["_manager_new_days"] = mgr_new
    pp_data[f"pp_{i}"]["_manager_daily"] = mgr_daily
    pp_data[f"pp_{i}"]["_manager_bonus"] = mgr_bonus

# Serialize pay period data to JS
import json as _json
pp_json = _json.dumps(pp_data)
groomers_json = _json.dumps(groomers)
groomer_colors_json = _json.dumps(groomer_color)
guarantees_json = _json.dumps(GUARANTEES)

# YTD summary rows
ytd_rows = summary_table(ytd_data)
ytd_daily = daily_detail_rows(ytd_data)
l30_rows  = summary_table(l30_data)
l30_daily = daily_detail_rows(l30_data)

TABLE_HEADER = '''<thead><tr>
  <th>Groomer</th>
  <th style="text-align:right">Days</th>
  <th style="text-align:right">Groom Revenue</th>
  <th style="text-align:right">50% Comm</th>
  <th style="text-align:right">Comm Paid</th>
  <th style="text-align:right">Tips</th>
  <th style="text-align:right">Total Pay</th>
  <th style="text-align:right">Avg/Day</th>
</tr></thead>'''

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
</head>
<body>
<div class="topbar">
  <div><div class="topbar-title">💅 Woof Gang Port Washington — Groomer Commission</div>
  <div class="topbar-sub">50% commission · Guarantees: Maria C $200/day · Sue M $300/day</div></div>
  <div class="topbar-date">Through Mar 8, 2026</div>
</div>

<div class="tab-bar">
  <button class="tab active" onclick="showTab('ytd',this)">2026 YTD</button>
  <button class="tab" onclick="showTab('l30',this)">Last 30 Days</button>
  <button class="tab" onclick="showTab('pp',this)">Pay Period</button>
  <select class="pp-select" id="pp-select" onchange="renderPayPeriod(this.value)">
    {pp_options}
  </select>
</div>

<div class="page">

<!-- ── YTD ── -->
<div class="panel active" id="panel-ytd">
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">{fc(ytd_total["rev"])}</div><div class="kpi-label">Groom Revenue</div></div>
    <div class="kpi blue"><div class="kpi-val">{fc(ytd_total["paid"])}</div><div class="kpi-label">Commission Paid</div></div>
    <div class="kpi orange"><div class="kpi-val">{fc(ytd_total["tips"])}</div><div class="kpi-label">Tips</div></div>
    <div class="kpi green"><div class="kpi-val">{fc(ytd_total["total"])}</div><div class="kpi-label">Total Groomer Pay</div></div>
    <div class="kpi" style="border-color:#5C6BC0"><div class="kpi-val" style="color:#5C6BC0">{fc(ytd_manager)}</div><div class="kpi-label">Manager Salary</div><div style="font-size:0.78rem;color:#5C6BC0;margin-top:3px">from Feb 23</div></div>
    <div class="kpi" style="border-color:#AD1457"><div class="kpi-val" style="color:#AD1457">{fc(ytd_total["rev"] * 0.07)}</div><div class="kpi-label">Royalties (7%)</div></div>
    <div class="kpi" style="border-color:#6D4C41"><div class="kpi-val" style="color:#6D4C41">{fc(ytd_rent)}</div><div class="kpi-label">Rent</div><div style="font-size:0.78rem;color:#6D4C41;margin-top:3px">{ytd_days_count} days</div></div>
    <div class="kpi" style="border-color:#00838F"><div class="kpi-val" style="color:#00838F">{fc(ytd_total["rev"] - ytd_total["paid"] - ytd_manager - ytd_total["rev"] * 0.07 - ytd_rent)}</div><div class="kpi-label">Margin</div><div style="font-size:0.78rem;color:#00838F;margin-top:3px;font-weight:600">{(ytd_total["rev"] - ytd_total["paid"] - ytd_manager - ytd_total["rev"] * 0.07 - ytd_rent) / ytd_total["rev"] * 100:.1f}%</div></div>
    <div class="kpi grey"><div class="kpi-val">{int(ytd_total["guar_days"])}</div><div class="kpi-label">Guarantee Days</div></div>
  </div>
  <div class="info-box">Commission = 50% of daily grooming revenue. <strong>Maria C</strong> guaranteed $200/day · <strong>Sue M</strong> guaranteed $300/day — paid whichever is higher. Tips assigned to the groomer who performed the service.</div>
  <div class="card">
    <div class="stitle">2026 YTD Summary</div>
    <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{ytd_rows}</tbody></table></div>
    <button class="toggle-btn" onclick="toggleDetail('ytd-detail',this)">▼ Show daily detail</button>
    <div class="detail-section" id="ytd-detail">
      <div class="tbl-wrap"><table>{TABLE_HEADER}<tbody>{ytd_daily}</tbody></table></div>
    </div>
  </div>
  <div class="card">
    <div class="stitle">Manager Salary — Cindy Szczudlo</div>
    <div style="display:flex;gap:16px;margin-bottom:18px;flex-wrap:wrap">
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{fc(ytd_manager)}</div><div class="kpi-label">YTD Total</div></div>
      <div class="kpi" style="border-color:#7986CB;flex:1;min-width:120px"><div class="kpi-val" style="color:#7986CB;font-size:1.4rem">{ytd_mgr_old_days}</div><div class="kpi-label">Days @ $65k</div></div>
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{ytd_mgr_new_days}</div><div class="kpi-label">Days @ $67k</div></div>
      {"<div class='kpi' style='border-color:#E91E63;flex:1;min-width:120px'><div class='kpi-val' style='color:#E91E63;font-size:1.4rem'>" + fc(ytd_mgr_bonus) + "</div><div class='kpi-label'>Bonus (Mar 1)</div></div>" if ytd_mgr_bonus else ""}
      <div class="kpi" style="border-color:#9FA8DA;flex:1;min-width:120px"><div class="kpi-val" style="color:#9FA8DA;font-size:1.4rem">{fc(65000/260)}</div><div class="kpi-label">Old Rate/Day</div></div>
      <div class="kpi" style="border-color:#5C6BC0;flex:1;min-width:120px"><div class="kpi-val" style="color:#5C6BC0;font-size:1.4rem">{fc(67000/260)}</div><div class="kpi-label">New Rate/Day</div></div>
    </div>
    <button class="toggle-btn" onclick="toggleDetail('cindy-ytd-detail',this)">▼ Show daily breakdown</button>
    <div class="detail-section" id="cindy-ytd-detail">
      <div class="tbl-wrap"><table>
        <thead><tr>
          <th>Date</th><th>Day</th><th style="text-align:right">Annual Rate</th><th style="text-align:right">Daily Pay</th><th style="text-align:right">Bonus</th>
        </tr></thead>
        <tbody>
          {"".join(f'<tr><td style="font-size:0.84rem">{r["date"]}</td><td style="font-size:0.84rem;color:#888">{datetime.strptime(r["date"],"%Y-%m-%d").strftime("%a")}</td><td style="text-align:right;font-size:0.84rem">{("$65,000" if r["rate"]==65000 else "$67,000")}<span style="font-size:0.72rem;color:#5C6BC0;margin-left:4px">{"↑ raised" if r["date"]=="2026-03-01" else ""}</span></td><td style="text-align:right;font-weight:600;color:#5C6BC0">{fc(r["pay"])}</td><td style="text-align:right;font-weight:700;color:#E91E63">{fc(r["bonus"]) if r["bonus"] else "—"}</td></tr>' for r in ytd_mgr_daily)}
        </tbody>
      </table></div>
    </div>
  </div>
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

<script>
var PP_DATA = {pp_json};
var GROOMERS = {groomers_json};
var COLORS = {groomer_colors_json};
var GUARANTEES = {guarantees_json};

function fc(v) {{ return '$' + parseFloat(v).toLocaleString('en-US', {{minimumFractionDigits:2,maximumFractionDigits:2}}); }}

function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-'+id).classList.add('active');
  btn.classList.add('active');
  var sel = document.getElementById('pp-select');
  sel.style.display = id === 'pp' ? 'block' : 'none';
  if (id === 'pp') renderPayPeriod(sel.value);
}}

function toggleDetail(id, btn) {{
  var el = document.getElementById(id);
  var open = el.style.display === 'block';
  el.style.display = open ? 'none' : 'block';
  btn.textContent = open ? '▼ Show daily detail' : '▲ Hide daily detail';
}}

function groomerBadge(g) {{
  var guar = GUARANTEES[g];
  var badge = guar ? ' <span style="background:#e3f2fd;color:#1565c0;padding:1px 6px;border-radius:6px;font-size:0.7rem;font-weight:700">G$'+guar+'</span>' : '';
  return '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'+COLORS[g]+';margin-right:7px"></span><strong>'+g+'</strong>'+badge;
}}

function renderPayPeriod(ppId) {{
  var data = PP_DATA[ppId];
  if (!data) return;

  // Find period label from select
  var sel = document.getElementById('pp-select');
  var label = sel.options[sel.selectedIndex].text;
  document.getElementById('pp-title').innerHTML = '<span style="content:none"></span>' + label;

  // Totals
  var totRev=0, totPaid=0, totTips=0, totTotal=0, totGuar=0;
  GROOMERS.forEach(function(g) {{
    var d = data[g]; if (!d) return;
    totRev += d.rev; totPaid += d.paid; totTips += d.tips; totTotal += d.total; totGuar += d.guar_days;
  }});
  var totRoyalties = totRev * 0.07;
  var DAILY_RENT = 7700.0 * 12 / 365;
  var totRent = DAILY_RENT * 14;
  var totManager = data._manager_salary || 0;
  var totMargin = totRev - totPaid - totRoyalties - totRent - totManager;
  var totMarginPct = totRev > 0 ? (totMargin / totRev * 100).toFixed(1) : '0.0';

  document.getElementById('pp-kpis').innerHTML =
    '<div class="kpi"><div class="kpi-val">'+fc(totRev)+'</div><div class="kpi-label">Groom Revenue</div></div>'+
    '<div class="kpi blue"><div class="kpi-val">'+fc(totPaid)+'</div><div class="kpi-label">Commission Paid</div></div>'+
    '<div class="kpi orange"><div class="kpi-val">'+fc(totTips)+'</div><div class="kpi-label">Tips</div></div>'+
    '<div class="kpi green"><div class="kpi-val">'+fc(totTotal)+'</div><div class="kpi-label">Total Pay</div></div>'+
    (totManager ? '<div class="kpi" style="border-color:#5C6BC0"><div class="kpi-val" style="color:#5C6BC0">'+fc(totManager)+'</div><div class="kpi-label">Manager Salary</div></div>' : '')+
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
    rows += '<tr>'+
      '<td>'+groomerBadge(g)+'</td>'+
      '<td style="text-align:right;color:#888;font-weight:600">'+d.working_days+'</td>'+
      '<td style="text-align:right">'+fc(d.rev)+'</td>'+
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
    detailRows += '<tr style="background:#f8f7f4"><td colspan="8" style="padding:10px 14px;font-weight:700">'+groomerBadge(g)+'</td></tr>';
    d.daily.forEach(function(day) {{
      var gflag = day.guar_applied ? '<span style="background:#e3f2fd;color:#1565c0;padding:1px 5px;border-radius:5px;font-size:0.7rem;margin-left:6px">guarantee</span>' : '';
      detailRows += '<tr>'+
        '<td style="padding-left:24px;color:#888;font-size:0.82rem">'+day.date+'</td>'+
        '<td style="text-align:right">'+fc(day.rev)+'</td>'+
        '<td style="text-align:right;color:#888">'+fc(day.comm)+'</td>'+
        '<td style="text-align:right;color:#1565c0">'+fc(day.paid)+gflag+'</td>'+
        '<td style="text-align:right;color:#f57c00">'+fc(day.tips)+'</td>'+
        '<td style="text-align:right;font-weight:600;color:#C4276E">'+fc(day.total)+'</td>'+
        '<td></td><td></td></tr>';
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
</script>
</body></html>'''

out_path = OUTPUT_DIR / "WoofGang_PortWashington_Commission_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"Pay periods built: {len(pay_periods)}")
print(f"Most recent: {period_label(*pay_periods[0])}")
print(f"Oldest: {period_label(*pay_periods[-1])}")
print(f"YTD: Rev={fc(ytd_total['rev'])} | Paid={fc(ytd_total['paid'])} | Tips={fc(ytd_total['tips'])} | Total={fc(ytd_total['total'])}")
