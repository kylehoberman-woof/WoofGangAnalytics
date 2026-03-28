#!/usr/bin/env python3
"""
Cross-Store Comparison Dashboard
Compares Port Washington vs Hicksville performance side-by-side.
Monthly, Weekly, and Daily comparison tabs with flexible period selectors.

Usage:
    python3 build_crossstore_dashboard.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure scripts dir is in path
SCRIPTS_DIR = Path(__file__).parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import get_store, PROJ_ROOT
from formatting import fc

# ── Load both stores' data ────────────────────────────────────────────────────

def load_store_data(store_name):
    """Load and transform data for a single store, returning (df, df_orders)."""
    store = get_store(store_name)

    # Patch generate_dashboards module to point at this store's data
    import run
    run.DATA_DIR = store.data_dir
    run.STORE_NAME = store.name
    run.START_DATE = store.start_date
    run.END_DATE = store.end_date

    import generate_dashboards as gd
    import importlib
    importlib.reload(gd)

    df, df_orders, _ = gd.load_data()
    return df, df_orders


print("Loading Port Washington data...")
pw_df, pw_orders = load_store_data("port-washington")
print(f"  PW: {len(pw_df)} items, {len(pw_orders)} orders")

print("Loading Hicksville data...")
hv_df, hv_orders = load_store_data("hicksville")
print(f"  HV: {len(hv_df)} items, {len(hv_orders)} orders")

# ── Build comparison data for each store ──────────────────────────────────────

from run import (
    build_monthly_comparison_data,
    build_weekly_comparison_data,
    build_daily_comparison_data,
    KPI_DEFS,
)

pw_monthly = build_monthly_comparison_data(pw_df, pw_orders)
pw_weekly = build_weekly_comparison_data(pw_df, pw_orders)
pw_daily = build_daily_comparison_data(pw_df, pw_orders)

hv_monthly = build_monthly_comparison_data(hv_df, hv_orders)
hv_weekly = build_weekly_comparison_data(hv_df, hv_orders)
hv_daily = build_daily_comparison_data(hv_df, hv_orders)

# ── Merge into cross-store datasets with prefixed keys ────────────────────────

def merge_comparison_data(pw_data, hv_data):
    """Merge two stores' comparison data into a single dataset with prefixed keys.
    Each period gets an 'idx' (0-based index from store opening) for 'same stage' matching."""
    merged = {"periods": [], "data": {}}

    # Add PW periods — original data is oldest-first, idx=0 = first month open
    for i, p in enumerate(pw_data["periods"]):
        key = f"pw-{p['key']}"
        merged["periods"].append({"key": key, "label": f"PW \u2022 {p['label']}", "store": "pw", "idx": i})
        merged["data"][key] = pw_data["data"][p["key"]]

    # Add HV periods — idx=0 = first month open
    for i, p in enumerate(hv_data["periods"]):
        key = f"hv-{p['key']}"
        merged["periods"].append({"key": key, "label": f"HV \u2022 {p['label']}", "store": "hv", "idx": i})
        merged["data"][key] = hv_data["data"][p["key"]]

    return merged

monthly_merged = merge_comparison_data(pw_monthly, hv_monthly)
weekly_merged = merge_comparison_data(pw_weekly, hv_weekly)
daily_merged = merge_comparison_data(pw_daily, hv_daily)

# ── Find good defaults ────────────────────────────────────────────────────────

# Default A = PW latest, Default B = HV latest
pw_monthly_periods = [p for p in monthly_merged["periods"] if p["key"].startswith("pw-")]
hv_monthly_periods = [p for p in monthly_merged["periods"] if p["key"].startswith("hv-")]
default_a_monthly = pw_monthly_periods[-1]["key"] if pw_monthly_periods else monthly_merged["periods"][0]["key"]
default_b_monthly = hv_monthly_periods[-1]["key"] if hv_monthly_periods else monthly_merged["periods"][-1]["key"]

pw_weekly_periods = [p for p in weekly_merged["periods"] if p["key"].startswith("pw-")]
hv_weekly_periods = [p for p in weekly_merged["periods"] if p["key"].startswith("hv-")]
default_a_weekly = pw_weekly_periods[-1]["key"] if pw_weekly_periods else weekly_merged["periods"][0]["key"]
default_b_weekly = hv_weekly_periods[-1]["key"] if hv_weekly_periods else weekly_merged["periods"][-1]["key"]

pw_daily_periods = [p for p in daily_merged["periods"] if p["key"].startswith("pw-")]
hv_daily_periods = [p for p in daily_merged["periods"] if p["key"].startswith("hv-")]
default_a_daily = pw_daily_periods[-1]["key"] if pw_daily_periods else daily_merged["periods"][0]["key"]
default_b_daily = hv_daily_periods[-1]["key"] if hv_daily_periods else daily_merged["periods"][-1]["key"]

# ── Build HTML ────────────────────────────────────────────────────────────────

now_et = datetime.now(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p ET")


def build_options(periods, default_key):
    return "\n".join(
        f'<option value="{p["key"]}"{"selected" if p["key"] == default_key else ""}>{p["label"]}</option>'
        for p in periods
    )


def build_comparison_panel(prefix, data, default_a, default_b, update_fn, title, chart_id, prorate=False):
    """Build a comparison panel with two selectors and KPI cards."""
    options_a = build_options(data["periods"], default_a)
    options_b = build_options(data["periods"], default_b)

    # Quick buttons
    quick_html = f'''<div class="quick-btns">
        <button class="quick-btn" onclick="_qcSameperiod('{prefix}')">Same Period</button>
        <button class="quick-btn" onclick="_qcSamestage('{prefix}')" title="Compare stores at the same point in their lifecycle (e.g. Month 3 of each store)">Same Stage</button>
    </div>'''

    # KPI cards
    cards = ""
    for key, label, cls in KPI_DEFS:
        pid = f"{prefix}-{key}"
        cards += f'''<div class="comparison-card kpi-card {cls}" id="{pid}">
  <div class="kpi-label">{label}</div>
  <div class="comparison-values">
    <div class="cmp-col">
      <div class="cmp-month-label" id="{pid}-labelA"></div>
      <div class="kpi-value" id="{pid}-valA"></div>
    </div>
    <div class="cmp-col cmp-delta">
      <div class="cmp-delta-val" id="{pid}-delta"></div>
      <div class="cmp-delta-pct" id="{pid}-deltaPct"></div>
    </div>
    <div class="cmp-col">
      <div class="cmp-month-label" id="{pid}-labelB"></div>
      <div class="kpi-value" id="{pid}-valB"></div>
    </div>
  </div>
</div>
'''

    prorate_html = ""
    if prorate:
        prorate_html = f'''<label class="prorate-toggle" title="Limit to the same number of calendar days"><input type="checkbox" id="{prefix}-prorate" onchange="{update_fn}()"><span>Pro-rate to match days</span></label>'''

    return f'''<div class="section">
  <h2><span class="dot"></span>{title}</h2>
  <p class="desc">Select any store + period combination to compare side by side</p>
  {quick_html}
  <div class="monthly-controls">
    <div class="control-group">
      <label>Period A</label>
      <select id="{prefix}-selA" onchange="{update_fn}()">{options_a}</select>
    </div>
    <span class="vs-label">vs</span>
    <div class="control-group">
      <label>Period B</label>
      <select id="{prefix}-selB" onchange="{update_fn}()">{options_b}</select>
    </div>
    {prorate_html}
  </div>
</div>
<div class="comparison-grid">
{cards}</div>
<div class="section">
  <h2><span class="dot"></span>Visual Comparison</h2>
  <p class="desc">Side-by-side bar chart of selected periods</p>
  <div class="chart-container"><canvas id="{chart_id}" style="max-height:400px"></canvas></div>
</div>
'''


monthly_panel = build_comparison_panel(
    "xcmp", monthly_merged, default_a_monthly, default_b_monthly,
    "updateXMonthly", "Monthly Cross-Store Comparison", "xMonthlyChart", prorate=True
)
weekly_panel = build_comparison_panel(
    "xwcmp", weekly_merged, default_a_weekly, default_b_weekly,
    "updateXWeekly", "Weekly Cross-Store Comparison", "xWeeklyChart", prorate=True
)
daily_panel = build_comparison_panel(
    "xdcmp", daily_merged, default_a_daily, default_b_daily,
    "updateXDaily", "Daily Cross-Store Comparison", "xDailyChart"
)

# ── CSS ───────────────────────────────────────────────────────────────────────

from generate_dashboards import CSS

extra_css = """
.tab-bar { background: white; border-bottom: 2px solid #eee; padding: 0 32px; display: flex; gap: 4px; position: sticky; top: 45px; z-index: 99; align-items: center; }
.tab { padding: 14px 20px; border: none; background: transparent; color: #999; font-size: 0.88rem; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; font-family: inherit; margin-bottom: -2px; white-space: nowrap; }
.tab:hover { color: #C4276E; }
.tab.active { color: #C4276E; border-bottom-color: #C4276E; }
.panel { display: none; max-width: 1300px; margin: 0 auto; padding: 28px 24px; }
.panel.active { display: block; }
.section { padding: 0 24px 20px; }
.section h2 { font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.section .dot { width: 8px; height: 8px; background: #C4276E; border-radius: 50%; display: inline-block; }
.section .desc { font-size: 0.85rem; color: #888; margin-bottom: 16px; }
.monthly-controls { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 20px; }
.control-group label { display: block; font-size: 0.75rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.control-group select { padding: 8px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 0.88rem; font-family: inherit; outline: none; cursor: pointer; min-width: 200px; }
.control-group select:focus { border-color: #C4276E; }
.vs-label { color: #C4276E; font-weight: 700; font-size: 1.1rem; padding-top: 22px; }
.comparison-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-bottom: 22px; padding: 0 24px; }
.comparison-card { background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border-top: 3px solid #1B6B6B; }
.comparison-card.accent { border-color: #C4276E; }
.comparison-card.green { border-color: #2E7D32; }
.kpi-label { font-size: 0.73rem; color: #999; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
.kpi-value { font-size: 1.4rem; font-weight: 700; color: #1a1a1a; }
.comparison-values { display: flex; align-items: center; gap: 8px; }
.cmp-col { flex: 1; }
.cmp-col.cmp-delta { flex: 0 0 auto; text-align: center; padding: 0 8px; }
.cmp-month-label { font-size: 0.72rem; color: #888; margin-bottom: 2px; font-weight: 500; }
.cmp-delta-val { font-size: 0.85rem; font-weight: 700; }
.cmp-delta-pct { font-size: 0.72rem; font-weight: 600; }
.delta-positive .cmp-delta-val, .delta-positive .cmp-delta-pct { color: #2E7D32; }
.delta-negative .cmp-delta-val, .delta-negative .cmp-delta-pct { color: #e53935; }
.chart-container { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.quick-btns { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.quick-btn { padding: 6px 14px; border: 1.5px solid #C4276E; border-radius: 20px; background: transparent; color: #C4276E; font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: inherit; }
.quick-btn:hover { background: #C4276E; color: white; }
.prorate-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; padding-top: 18px; font-size: 0.82rem; font-weight: 600; color: #666; user-select: none; white-space: nowrap; }
.prorate-toggle input { accent-color: #C4276E; width: 16px; height: 16px; cursor: pointer; }
.prorate-toggle:hover span { color: #C4276E; }
"""

# ── JavaScript ────────────────────────────────────────────────────────────────

js_code = f"""
var MONTHLY_DATA = {json.dumps(monthly_merged)};
var WEEKLY_DATA = {json.dumps(weekly_merged)};
var DAILY_DATA = {json.dumps(daily_merged)};
var _xMonthlyChart = null, _xWeeklyChart = null, _xDailyChart = null;

function _mfc(v) {{
    return (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, {{minimumFractionDigits: 0, maximumFractionDigits: 0}});
}}
function _mfi(v) {{ return v.toLocaleString(); }}

function _proRate(periodData, maxDom, useDOM) {{
    var daily = periodData.daily;
    if (!daily || !daily.length) return periodData;
    var filtered = daily.filter(function(d) {{ return useDOM ? d.dom <= maxDom : d.dow <= maxDom; }});
    if (!filtered.length) return periodData;
    var sum = {{days_open: filtered.length}};
    ['total_revenue','groom_revenue','retail_revenue','appointments','transactions','unique_customers','tips','groomer_days'].forEach(function(k) {{
        sum[k] = 0;
        filtered.forEach(function(d) {{ sum[k] += d[k]; }});
        sum[k] = Math.round(sum[k] * 100) / 100;
    }});
    sum.avg_ticket = sum.transactions > 0 ? Math.round(sum.total_revenue / sum.transactions * 100) / 100 : 0;
    return sum;
}}

function _updateXComparison(DATA, selA, selB, prefix, chartId, chartRef) {{
    var keyA = document.getElementById(selA).value;
    var keyB = document.getElementById(selB).value;
    var aRaw = DATA.data[keyA], b = DATA.data[keyB];
    if (!aRaw || !b) return null;
    var labelA = document.getElementById(selA).selectedOptions[0].text;
    var labelB = document.getElementById(selB).selectedOptions[0].text;

    var prCb = document.getElementById(prefix + '-prorate');
    var prorated = prCb && prCb.checked && b.daily && aRaw.daily;
    var a = aRaw;
    if (prorated) {{
        var useDOM = prefix === 'xcmp';
        var field = useDOM ? 'dom' : 'dow';
        var maxA = 0, maxB = 0;
        aRaw.daily.forEach(function(d) {{ if (d[field] > maxA) maxA = d[field]; }});
        b.daily.forEach(function(d) {{ if (d[field] > maxB) maxB = d[field]; }});
        var cutoff = Math.min(maxA, maxB);
        var daysLabel = useDOM ? cutoff : (cutoff + 1);
        if (maxA > cutoff) {{
            a = _proRate(aRaw, cutoff, useDOM);
            labelA += ' (first ' + daysLabel + ' days)';
        }}
        if (maxB > cutoff) {{
            b = _proRate(b, cutoff, useDOM);
            labelB += ' (first ' + daysLabel + ' days)';
        }}
    }}

    var kpis = [
        {{key: 'total_revenue', fmt: 'c'}}, {{key: 'groom_revenue', fmt: 'c'}},
        {{key: 'retail_revenue', fmt: 'c'}}, {{key: 'appointments', fmt: 'i'}},
        {{key: 'transactions', fmt: 'i'}}, {{key: 'avg_ticket', fmt: 'c'}},
        {{key: 'unique_customers', fmt: 'i'}}, {{key: 'tips', fmt: 'c'}},
        {{key: 'days_open', fmt: 'i'}}, {{key: 'groomer_days', fmt: 'i'}}
    ];

    kpis.forEach(function(kpi) {{
        var valA = a[kpi.key], valB = b[kpi.key];
        var delta = valB - valA;
        var deltaPct = valA !== 0 ? ((valB - valA) / Math.abs(valA) * 100) : 0;
        var f = kpi.fmt === 'c' ? _mfc : _mfi;
        var pid = prefix + '-' + kpi.key;
        document.getElementById(pid + '-labelA').textContent = labelA;
        document.getElementById(pid + '-labelB').textContent = labelB;
        document.getElementById(pid + '-valA').textContent = f(valA);
        document.getElementById(pid + '-valB').textContent = f(valB);
        document.getElementById(pid + '-delta').textContent = (delta >= 0 ? '+' : '') + f(delta);
        document.getElementById(pid + '-deltaPct').textContent = (deltaPct >= 0 ? '+' : '') + deltaPct.toFixed(1) + '%';
        var deltaEl = document.getElementById(pid).querySelector('.cmp-delta');
        deltaEl.classList.remove('delta-positive', 'delta-negative');
        deltaEl.classList.add(delta >= 0 ? 'delta-positive' : 'delta-negative');
    }});

    if (chartRef) {{ chartRef.destroy(); }}
    var ctx = document.getElementById(chartId);
    return new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: ['Total Revenue', 'Grooming', 'Retail', 'Appointments', 'Avg Ticket', 'Tips'],
            datasets: [
                {{ label: labelA, data: [a.total_revenue, a.groom_revenue, a.retail_revenue, a.appointments, a.avg_ticket, a.tips], backgroundColor: '#C4276E', borderRadius: 6 }},
                {{ label: labelB, data: [b.total_revenue, b.groom_revenue, b.retail_revenue, b.appointments, b.avg_ticket, b.tips], backgroundColor: '#1B6B6B', borderRadius: 6 }}
            ]
        }},
        options: {{
            responsive: true,
            plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: function(c) {{ var v = c.parsed.y; return c.dataset.label + ': ' + (c.dataIndex <= 2 || c.dataIndex === 4 || c.dataIndex === 5 ? '$' + v.toLocaleString() : v.toLocaleString()); }} }} }} }},
            scales: {{ y: {{ ticks: {{ callback: function(v) {{ return '$' + v.toLocaleString(); }} }} }} }}
        }}
    }});
}}

function updateXMonthly() {{ _xMonthlyChart = _updateXComparison(MONTHLY_DATA, 'xcmp-selA', 'xcmp-selB', 'xcmp', 'xMonthlyChart', _xMonthlyChart); }}
function updateXWeekly() {{ _xWeeklyChart = _updateXComparison(WEEKLY_DATA, 'xwcmp-selA', 'xwcmp-selB', 'xwcmp', 'xWeeklyChart', _xWeeklyChart); }}
function updateXDaily() {{ _xDailyChart = _updateXComparison(DAILY_DATA, 'xdcmp-selA', 'xdcmp-selB', 'xdcmp', 'xDailyChart', _xDailyChart); }}

// Quick button: match the other store's same period
function _qcSameperiod(prefix) {{
    var selB = document.getElementById(prefix + '-selB');
    var keyB = selB.value;
    var selA = document.getElementById(prefix + '-selA');
    // Extract period part (e.g., "2026-03" from "hv-2026-03")
    var periodB = keyB.substring(3);
    // Find opposite store with same period
    var targetPrefix = keyB.startsWith('pw-') ? 'hv-' : 'pw-';
    var targetKey = targetPrefix + periodB;
    for (var i = 0; i < selA.options.length; i++) {{
        if (selA.options[i].value === targetKey) {{
            selA.selectedIndex = i;
            break;
        }}
    }}
    if (prefix === 'xcmp') updateXMonthly();
    else if (prefix === 'xwcmp') updateXWeekly();
    else updateXDaily();
}}

// Quick button: match the other store's same stage (e.g. Month 3 of each store)
function _qcSamestage(prefix) {{
    var DATA = prefix === 'xcmp' ? MONTHLY_DATA : (prefix === 'xwcmp' ? WEEKLY_DATA : DAILY_DATA);
    var selB = document.getElementById(prefix + '-selB');
    var keyB = selB.value;
    var selA = document.getElementById(prefix + '-selA');

    // Find the idx of selected period B
    var idxB = -1;
    var storeB = '';
    for (var i = 0; i < DATA.periods.length; i++) {{
        if (DATA.periods[i].key === keyB) {{
            idxB = DATA.periods[i].idx;
            storeB = DATA.periods[i].store;
            break;
        }}
    }}
    if (idxB < 0) return;

    // Find opposite store's period with the same idx
    var targetStore = storeB === 'pw' ? 'hv' : 'pw';
    var targetKey = null;
    for (var j = 0; j < DATA.periods.length; j++) {{
        if (DATA.periods[j].store === targetStore && DATA.periods[j].idx === idxB) {{
            targetKey = DATA.periods[j].key;
            break;
        }}
    }}
    if (!targetKey) return;

    // Set selector A to the matching stage
    for (var k = 0; k < selA.options.length; k++) {{
        if (selA.options[k].value === targetKey) {{
            selA.selectedIndex = k;
            break;
        }}
    }}

    if (prefix === 'xcmp') updateXMonthly();
    else if (prefix === 'xwcmp') updateXWeekly();
    else updateXDaily();
}}

// Tab switching
function showTab(id, btn) {{
    document.querySelectorAll('.panel').forEach(function(p) {{ p.classList.remove('active'); }});
    document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.remove('active'); }});
    document.getElementById('panel-' + id).classList.add('active');
    btn.classList.add('active');
    if (id === 'monthly') updateXMonthly();
    if (id === 'weekly') updateXWeekly();
    if (id === 'daily') updateXDaily();
}}

// Init on load
window.addEventListener('DOMContentLoaded', function() {{ updateXMonthly(); }});
"""

# ── Assemble HTML ─────────────────────────────────────────────────────────────

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Woof Gang — Cross-Store Comparison</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f4f0;color:#1a1a1a;line-height:1.6}}
{CSS}
{extra_css}
</style>
</head>
<body>
<div class="header">
    <div class="header-timestamp">Updated {now_et}</div>
    <h1>Woof Gang — Cross-Store Comparison</h1>
    <div class="subtitle">Port Washington vs Hicksville Performance Analysis</div>
    <div class="brand-tag">Woof Gang Bakery &amp; Grooming</div>
</div>
<div style="background:#C4276E;padding:12px 32px;display:flex;align-items:center;position:sticky;top:0;z-index:101;box-shadow:0 2px 12px rgba(196,39,110,0.3)">
  <a href="index.html" style="color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.85rem;font-weight:600">&larr; Home</a>
</div>
<div class="tab-bar">
  <button class="tab active" onclick="showTab('monthly',this)">Monthly</button>
  <button class="tab" onclick="showTab('weekly',this)">Weekly</button>
  <button class="tab" onclick="showTab('daily',this)">Daily</button>
</div>

<div class="panel active" id="panel-monthly">
{monthly_panel}
</div>

<div class="panel" id="panel-weekly">
{weekly_panel}
</div>

<div class="panel" id="panel-daily">
{daily_panel}
</div>

<script>
{js_code}
</script>
</body>
</html>'''

# ── Write output ──────────────────────────────────────────────────────────────

out_dir = PROJ_ROOT / "port-washington"
out_path = out_dir / "WoofGang_CrossStore_Comparison.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"\nSaved: {out_path}")
print(f"Monthly periods: PW={len(pw_monthly['periods'])}, HV={len(hv_monthly['periods'])}")
print(f"Weekly periods: PW={len(pw_weekly['periods'])}, HV={len(hv_weekly['periods'])}")
