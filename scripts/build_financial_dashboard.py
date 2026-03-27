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
    MANAGER_SALARY_OLD, MANAGER_SALARY_NEW, MANAGER_RAISE_DATE,
    MANAGER_BONUS_DATE, MANAGER_BONUS, MANAGER_START, MONTHLY_RENT,
    STORE_OPEN,
)
from formatting import fc

SCRIPTS_DIR = Path(__file__).parent
_store = get_store("port-washington")
DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir
TODAY = date.today()
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
    mgr = manager_salary_for_range(m_start, m_end)
    bather_hrs = get_hours_from_clocks(data.get("time_clocks", []), BATHER_NAME_MAP, s, e)
    bather_pay = round(sum(h * BATHER_RATE for h in bather_hrs.values()), 2)
    retail_hrs = get_hours_from_clocks(data.get("time_clocks", []), RETAIL_NAME_MAP, s, e)
    retail_pay = round(sum(h * RETAIL_RATES.get(name, 0) for name, h in retail_hrs.items()), 2)
    royalties = round(total_rev * 0.07, 2)
    days_in_month = (m_end - m_start).days + 1
    rent = round(DAILY_RENT * days_in_month, 2)

    gross_profit = round(net_rev - retail_cogs - comm_paid, 2)
    total_opex = round(mgr + bather_pay + retail_pay + royalties + rent, 2)
    net_margin = round(gross_profit - total_opex, 2)
    net_margin_pct = round(net_margin / total_rev * 100, 1) if total_rev else 0

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

# ── JSON for JS ──────────────────────────────────────────────────────────────
import json as _j
monthly_json = _j.dumps(monthly)

# ── HTML ─────────────────────────────────────────────────────────────────────
from datetime import datetime as _dt
NOW_STR = _dt.now().strftime("%B %d, %Y at %I:%M %p ET").replace(" 0", " ")

def kpi(label, value, color="#C4276E", sub=""):
    sub_html = f'<div style="font-size:0.78rem;color:{color};margin-top:3px;font-weight:600">{sub}</div>' if sub else ""
    return f'<div class="kpi" style="border-color:{color}"><div class="kpi-val" style="color:{color}">{value}</div><div class="kpi-label">{label}</div>{sub_html}</div>'

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

html = f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Woof Gang Port Washington — Financial Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',system-ui,sans-serif;background:#f5f4f0;color:#1a1a2e}}
.topbar{{background:#C4276E;padding:18px 32px;color:white;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3);display:flex;justify-content:space-between;align-items:center}}
.topbar-title{{font-size:1.1rem;font-weight:700}}
.topbar-sub{{color:rgba(255,255,255,0.75);font-size:0.82rem;margin-top:2px}}
.topbar-time{{font-family:'DM Mono',monospace;font-size:0.78rem;opacity:0.8}}
.tabs{{background:white;border-bottom:2px solid #eee;position:sticky;top:56px;z-index:99;display:flex;padding:0 24px}}
.tab{{padding:14px 20px;border:none;background:transparent;color:#999;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;font-family:inherit}}
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
.panel{{display:none}}.panel.active{{display:block}}
.home-link{{color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.82rem;font-weight:600}}
.home-link:hover{{color:white}}
</style>
</head><body>

<div class="topbar">
  <div>
    <div style="display:flex;align-items:center;gap:12px">
      <a href="index.html" class="home-link">← Home</a>
      <div class="topbar-title">💰 Woof Gang Port Washington — Financial Dashboard</div>
    </div>
    <div class="topbar-sub">Revenue · COGS · Operating Expenses · Net Margin</div>
  </div>
  <div class="topbar-time">Updated {NOW_STR}</div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('ytd',this)">2026 YTD</button>
  <button class="tab" onclick="showTab('monthly',this)">Monthly P&L</button>
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
    {kpi("Royalties (7%)", fc(ytd_sums["royalties"]), "#AD1457")}
    {kpi("Rent", fc(ytd_sums["rent"]), "#6D4C41")}
    {kpi("Net Margin", fc(ytd_sums["net_margin"]), "#2E7D32" if ytd_sums["net_margin"] >= 0 else "#e53935", f'{ytd_sums["net_margin_pct"]}%')}
    {kpi("Tips Collected", fc(ytd_sums["tips"]), "#F57C00")}
  </div>

  <div class="card">
    <div class="stitle">Cost Breakdown</div>
    <div style="max-width:500px;margin:0 auto"><canvas id="chart-costs"></canvas></div>
  </div>

  <div class="card">
    <div class="stitle">P&L Waterfall</div>
    <div style="max-width:800px;margin:0 auto"><canvas id="chart-waterfall" height="300"></canvas></div>
  </div>
</div>

<!-- ── Monthly ── -->
<div class="panel" id="panel-monthly">
  <div class="card">
    <div class="stitle">Monthly P&L</div>
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

</div>

<div style="text-align:center;padding:24px;font-size:0.8rem;color:#999">
  <strong style="color:#C4276E">Woof Gang Bakery &amp; Grooming</strong> — Port Washington, NY &middot; Financial Dashboard
</div>

<script>
var DATA = {monthly_json};

function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.remove('active'); }});
  document.getElementById('panel-' + id).classList.add('active');
  btn.classList.add('active');
}}

function fc(n) {{ return '$' + Math.round(n).toLocaleString(); }}

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
// Add net margin bar
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
var labels2026 = DATA.filter(function(m) {{ return m.year >= 2025; }}).map(function(m) {{ return m.label; }});
var revs = DATA.filter(function(m) {{ return m.year >= 2025; }}).map(function(m) {{ return m.total_rev; }});
var margins = DATA.filter(function(m) {{ return m.year >= 2025; }}).map(function(m) {{ return m.net_margin; }});

new Chart(document.getElementById('chart-monthly'), {{
  type: 'bar',
  data: {{
    labels: labels2026,
    datasets: [
      {{ label: 'Total Revenue', data: revs, backgroundColor: 'rgba(196,39,110,0.7)', order: 1 }},
      {{ label: 'Net Margin', data: margins, backgroundColor: margins.map(function(v) {{ return v >= 0 ? 'rgba(27,107,107,0.8)' : 'rgba(229,57,53,0.8)'; }}), order: 0 }}
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

out_path = OUTPUT_DIR / "WoofGang_PortWashington_Financial_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"Months: {len(monthly)} | YTD Rev: {fc(ytd_sums['total_rev'])} | Net Margin: {fc(ytd_sums['net_margin'])} ({ytd_sums['net_margin_pct']}%)")
