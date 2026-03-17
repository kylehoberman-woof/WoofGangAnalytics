#!/usr/bin/env python3
"""
Woof Gang Store Performance Analysis Pipeline
Pulls data from FranPOS API, transforms, and generates Excel workbook + dashboards.

Usage:
    python run.py                    # Port Washington (default)
    python run.py hicksville         # Hicksville
"""

import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import httpx
import pandas as pd

from config import (
    get_store, BASE_URL, UNTRACKED_SKUS,
    # Re-export for backward compatibility with scripts that import from run
    C, PAW_MAGENTA, TEDDY_BROWN, LIGHT_PINK, DARK_TEAL, WHITE, LIGHT_GRAY,
    FDD_RETAIL_COGS_PCT, FDD_GROOM_COGS_PCT,
)
from classifier import classify_item
from api_client import extract_all_data, extract_full_catalog
from transform import transform_data
from workbook import generate_workbook

# Active store config — set by main() or by external scripts patching this module
_store = get_store("port-washington")
STORE_NAME = _store.name
DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir
START_DATE = _store.start_date
END_DATE = _store.end_date
LOCATION_ID = _store.location_id
TOKEN = _store.token


def _compute_period_kpis(df_slice, df_orders_slice):
    """Compute KPIs for a single period slice. Returns dict."""
    from formatting import to_py
    groom = df_slice[df_slice["is_groom"] == True]
    retail = df_slice[df_slice["is_retail"] == True]
    total_rev = to_py(df_slice["net_sales"].sum())
    groom_rev = to_py(groom["net_sales"].sum())
    retail_rev = to_py(retail["net_sales"].sum())
    txns = int(df_orders_slice.shape[0])
    # Appointments = orders that have at least one grooming item
    groom_order_ids = set(groom["order_id"].dropna().unique()) if "order_id" in groom.columns else set()
    appointments = len(groom_order_ids)
    return {
        "total_revenue": round(total_rev, 2),
        "groom_revenue": round(groom_rev, 2),
        "retail_revenue": round(retail_rev, 2),
        "appointments": appointments,
        "transactions": txns,
        "avg_ticket": round(total_rev / txns, 2) if txns else 0,
        "unique_customers": int(df_slice["customer_id"].nunique()),
        "tips": round(to_py(df_orders_slice["tips"].sum()), 2),
    }


def build_monthly_comparison_data(df_all, df_orders_all):
    """Pre-compute per-month KPIs for all available months."""
    periods = sorted(df_all["ym"].dropna().unique())
    result = {"periods": [], "data": {}}
    for p in periods:
        key = str(p)
        result["periods"].append({"key": key, "label": p.strftime("%b '%y")})
        result["data"][key] = _compute_period_kpis(
            df_all[df_all["ym"] == p], df_orders_all[df_orders_all["ym"] == p])
    return result


def build_weekly_comparison_data(df_all, df_orders_all):
    """Pre-compute per-week KPIs for all available weeks."""
    from datetime import datetime, timedelta
    weeks = sorted(df_all["yw"].dropna().unique())
    result = {"periods": [], "data": {}}
    for w in weeks:
        # Parse "2026-W11" → Monday date for label
        try:
            yr, wk = int(w[:4]), int(w.split("W")[1])
            mon = datetime.strptime(f"{yr}-W{wk:02d}-1", "%Y-W%W-%w")
            if mon.isocalendar()[1] != wk:
                mon = datetime.strptime(f"{yr}-W{wk:02d}-1", "%G-W%V-%u")
            sun = mon + timedelta(days=6)
            label = f"{mon.strftime('%b %-d')} – {sun.strftime('%b %-d, %Y')}"
        except Exception:
            label = w
        result["periods"].append({"key": w, "label": label})
        result["data"][w] = _compute_period_kpis(
            df_all[df_all["yw"] == w], df_orders_all[df_orders_all["yw"] == w])
    return result


def build_daily_comparison_data(df_all, df_orders_all, max_days=90):
    """Pre-compute per-day KPIs for recent days (default last 90)."""
    from datetime import datetime
    days = sorted(df_all["ymd"].dropna().unique())
    if max_days and len(days) > max_days:
        days = days[-max_days:]
    result = {"periods": [], "data": {}}
    for d in days:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            label = dt.strftime("%a %b %-d, %Y")
        except Exception:
            label = d
        result["periods"].append({"key": d, "label": label})
        result["data"][d] = _compute_period_kpis(
            df_all[df_all["ymd"] == d], df_orders_all[df_orders_all["ymd"] == d])
    return result


KPI_DEFS = [
    ("total_revenue", "Total Revenue", ""),
    ("groom_revenue", "Grooming Revenue", "accent"),
    ("retail_revenue", "Retail Revenue", ""),
    ("appointments", "Appointments", "green"),
    ("transactions", "Transactions", ""),
    ("avg_ticket", "Avg Ticket", "green"),
    ("unique_customers", "Unique Customers", ""),
    ("tips", "Tips", ""),
]


def _build_comparison_panel_html(data, prefix, select_id_a, select_id_b, update_fn, title, subtitle, chart_id):
    """Generic comparison panel builder shared by monthly/weekly/daily."""
    periods = data["periods"]
    default_a = periods[-2]["key"] if len(periods) >= 2 else periods[0]["key"]
    default_b = periods[-1]["key"] if len(periods) >= 1 else periods[0]["key"]

    options_a = "\n".join(
        f'<option value="{p["key"]}"{"selected" if p["key"] == default_a else ""}>{p["label"]}</option>'
        for p in periods
    )
    options_b = "\n".join(
        f'<option value="{p["key"]}"{"selected" if p["key"] == default_b else ""}>{p["label"]}</option>'
        for p in periods
    )

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

    return f'''<div class="section">
  <h2><span class="dot"></span>{title}</h2>
  <p class="desc">{subtitle}</p>
  <div class="monthly-controls">
    <div class="control-group">
      <label for="{select_id_a}">Period A</label>
      <select id="{select_id_a}" onchange="{update_fn}()">{options_a}</select>
    </div>
    <span class="vs-label">vs</span>
    <div class="control-group">
      <label for="{select_id_b}">Period B</label>
      <select id="{select_id_b}" onchange="{update_fn}()">{options_b}</select>
    </div>
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


def build_monthly_panel_html(data):
    return _build_comparison_panel_html(
        data, "cmp", "monthA", "monthB", "updateMonthlyComparison",
        "Month-to-Month Comparison", "Select two months to compare key performance metrics side by side",
        "monthlyCompareChart")


def build_weekly_panel_html(data):
    return _build_comparison_panel_html(
        data, "wcmp", "weekA", "weekB", "updateWeeklyComparison",
        "Week-to-Week Comparison", "Select two weeks to compare key performance metrics side by side",
        "weeklyCompareChart")


def build_daily_panel_html(data):
    return _build_comparison_panel_html(
        data, "dcmp", "dayA", "dayB", "updateDailyComparison",
        "Day-to-Day Comparison", "Select two days to compare key performance metrics side by side",
        "dailyCompareChart")


def generate_dashboard(store):
    """Generate the tabbed HTML dashboard for all years."""
    import types

    print("\nGenerating HTML dashboard...")

    # generate_dashboards.py imports from 'run' module, so update these module-level vars
    this = sys.modules[__name__]
    this.STORE_NAME = store.name
    this.DATA_DIR = store.data_dir
    this.START_DATE = store.start_date
    this.END_DATE = "2026-12-31"

    scripts_dir = Path(__file__).parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import generate_dashboards as gd
    # Reload to pick up updated module-level vars
    import importlib
    importlib.reload(gd)

    df_all, df_orders_all, _ = gd.load_data()

    YEARS = {
        "2024": ("2024-09-26", "2024-12-31"),
        "2025": ("2025-01-01", "2025-12-31"),
        "2026": ("2026-01-01", "2026-12-31"),
    }
    YEAR_LABELS = {
        "2024": "2024 (Sep\u2013Dec)",
        "2025": "2025 (Full Year)",
        "2026": "2026 (YTD)",
    }

    bodies = {}
    for yr, (start, end) in YEARS.items():
        this.START_DATE = start
        this.END_DATE = end
        df_yr = df_all[df_all["year"] == int(yr)]
        df_ord_yr = df_orders_all[df_orders_all["year"] == int(yr)]
        bodies[yr] = gd.generate_main_dashboard(df_yr, df_ord_yr, None, body_only=True, year_suffix=yr)

    last_txn = df_all["created"].max()
    through = last_txn.strftime("%-m/%-d/%Y") if not pd.isnull(last_txn) else "today"

    tabbed_css = """
.yr-tab-bar { background: #C4276E; padding: 0 24px; display: flex; gap: 4px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }
.yr-tab { padding: 14px 32px; border: none; background: transparent; color: rgba(255,255,255,0.6); font-size: 15px; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; font-family: inherit; }
.yr-tab:hover { color: white; background: rgba(255,255,255,0.08); }
.yr-tab.active { color: white; border-bottom-color: white; }
"""
    tabbed_js = """
var _chartsInited = {};
function showYear(yr) {
    document.querySelectorAll('.yr-panel').forEach(function(p) { p.style.display = 'none'; });
    document.querySelectorAll('.yr-tab').forEach(function(t) { t.classList.remove('active'); });
    document.getElementById('panel-' + yr).style.display = 'block';
    document.getElementById('tab-' + yr).classList.add('active');
    if (!_chartsInited[yr]) {
        _chartsInited[yr] = true;
        if (yr === '2024' && typeof initCharts_2024 === 'function') initCharts_2024();
        if (yr === '2025' && typeof initCharts_2025 === 'function') initCharts_2025();
        if (yr === '2026' && typeof initCharts_2026 === 'function') initCharts_2026();
        if (yr === 'monthly') updateMonthlyComparison();
        if (yr === 'weekly') updateWeeklyComparison();
        if (yr === 'daily') updateDailyComparison();
    }
}
window.addEventListener('DOMContentLoaded', function() { showYear('2025'); });
"""

    # Build comparison data for all three tabs
    import json as _json_cmp
    monthly_data = build_monthly_comparison_data(df_all, df_orders_all)
    weekly_data = build_weekly_comparison_data(df_all, df_orders_all)
    daily_data = build_daily_comparison_data(df_all, df_orders_all)
    monthly_panel_body = build_monthly_panel_html(monthly_data)
    weekly_panel_body = build_weekly_panel_html(weekly_data)
    daily_panel_body = build_daily_panel_html(daily_data)

    # Generic JS comparison function — reused by monthly, weekly, daily
    comparison_js = """
var MONTHLY_DATA = """ + _json_cmp.dumps(monthly_data) + """;
var WEEKLY_DATA = """ + _json_cmp.dumps(weekly_data) + """;
var DAILY_DATA = """ + _json_cmp.dumps(daily_data) + """;
var _monthlyChart = null, _weeklyChart = null, _dailyChart = null;

function _mfc(v) {
    return (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
}
function _mfi(v) { return v.toLocaleString(); }

function _updateComparison(DATA, selA, selB, prefix, chartId, chartRef) {
    var keyA = document.getElementById(selA).value;
    var keyB = document.getElementById(selB).value;
    var a = DATA.data[keyA], b = DATA.data[keyB];
    if (!a || !b) return null;
    var labelA = document.getElementById(selA).selectedOptions[0].text;
    var labelB = document.getElementById(selB).selectedOptions[0].text;

    var kpis = [
        {key: 'total_revenue', fmt: 'c'}, {key: 'groom_revenue', fmt: 'c'},
        {key: 'retail_revenue', fmt: 'c'}, {key: 'appointments', fmt: 'i'},
        {key: 'transactions', fmt: 'i'}, {key: 'avg_ticket', fmt: 'c'},
        {key: 'unique_customers', fmt: 'i'}, {key: 'tips', fmt: 'c'}
    ];

    kpis.forEach(function(kpi) {
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
    });

    if (chartRef) { chartRef.destroy(); }
    var ctx = document.getElementById(chartId);
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Total Revenue', 'Grooming', 'Retail', 'Appointments', 'Avg Ticket', 'Tips'],
            datasets: [
                { label: labelA, data: [a.total_revenue, a.groom_revenue, a.retail_revenue, a.appointments, a.avg_ticket, a.tips], backgroundColor: '#C4276E', borderRadius: 6 },
                { label: labelB, data: [b.total_revenue, b.groom_revenue, b.retail_revenue, b.appointments, b.avg_ticket, b.tips], backgroundColor: '#1B6B6B', borderRadius: 6 }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: function(c) { var v = c.parsed.y; return c.dataset.label + ': ' + (c.dataIndex <= 2 || c.dataIndex === 4 || c.dataIndex === 5 ? '$' + v.toLocaleString() : v.toLocaleString()); } } } },
            scales: { y: { ticks: { callback: function(v) { return '$' + v.toLocaleString(); } } } }
        }
    });
}

function updateMonthlyComparison() { _monthlyChart = _updateComparison(MONTHLY_DATA, 'monthA', 'monthB', 'cmp', 'monthlyCompareChart', _monthlyChart); }
function updateWeeklyComparison() { _weeklyChart = _updateComparison(WEEKLY_DATA, 'weekA', 'weekB', 'wcmp', 'weeklyCompareChart', _weeklyChart); }
function updateDailyComparison() { _dailyChart = _updateComparison(DAILY_DATA, 'dayA', 'dayB', 'dcmp', 'dailyCompareChart', _dailyChart); }
"""

    html = gd.html_head("Woof Gang Port Washington", f"Store Performance Analysis \u00b7 Sales through {through}")
    html = html.replace("</style>", tabbed_css + "\n</style>", 1)
    html += '<div class="yr-tab-bar">\n'
    for yr in ["2024", "2025", "2026"]:
        active = "active" if yr == "2025" else ""
        html += f'  <button class="yr-tab {active}" onclick="showYear(\'{yr}\')" id="tab-{yr}">{YEAR_LABELS[yr]}</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'monthly\')" id="tab-monthly">Monthly</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'weekly\')" id="tab-weekly">Weekly</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'daily\')" id="tab-daily">Daily</button>\n'
    html += '</div>\n'

    for yr in ["2024", "2025", "2026"]:
        display = "block" if yr == "2025" else "none"
        html += f'<div class="yr-panel" id="panel-{yr}" style="display:{display}">\n'
        body = bodies[yr]
        def _wrap(m, _yr=yr):
            inner = m.group(0)[len("<script>\n"):-len("</script>")]
            return f"<script>\nfunction initCharts_{_yr}() {{\n{inner}\n}}\n</script>"
        body = re.sub(r'<script>\nconst cc = .*?</script>', _wrap, body, flags=re.DOTALL)
        html += body
        html += '</div>\n'

    # Comparison panels
    html += '<div class="yr-panel" id="panel-monthly" style="display:none">\n'
    html += monthly_panel_body
    html += '</div>\n'
    html += '<div class="yr-panel" id="panel-weekly" style="display:none">\n'
    html += weekly_panel_body
    html += '</div>\n'
    html += '<div class="yr-panel" id="panel-daily" style="display:none">\n'
    html += daily_panel_body
    html += '</div>\n'

    html += f'<script>\n{tabbed_js}\n</script>\n'
    html += f'<script>\n{comparison_js}\n</script>\n'
    html += gd.HTML_FOOT

    out_path = store.output_dir / "WoofGang_PortWashington_NY_AllYears_Dashboard.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"  Dashboard saved: {out_path}")
    return out_path


def reset_untracked_stock(store):
    """Reset bulk/treat SKUs that can't be inventory-tracked back to 0 nightly."""
    print("\nResetting untracked SKUs to 0...")
    for sku, name in UNTRACKED_SKUS:
        r = httpx.post(f"{BASE_URL}/api/updateStockByProductSKU",
                       params={"sku": sku, "stock": 0, "addToStock": "false", "Token": store.token},
                       timeout=15)
        ok = r.status_code == 200 and r.json()[0].get("IsSuccess")
        print(f"  {'✓' if ok else '✗'} {sku:12} {name}")
        time.sleep(0.3)
    print("  Done!")


def main(store_name="port-washington"):
    store = get_store(store_name)

    # Update module-level vars for backward compat
    this = sys.modules[__name__]
    this.STORE_NAME = store.name
    this.DATA_DIR = store.data_dir
    this.OUTPUT_DIR = store.output_dir
    this.START_DATE = store.start_date
    this.END_DATE = store.end_date
    this.LOCATION_ID = store.location_id
    this.TOKEN = store.token
    this._store = store

    start_time = time.time()

    # 1. Extract data
    raw_data = extract_all_data(store)

    # 2. Transform + Excel
    transformed = transform_data(raw_data)
    generate_workbook(transformed, store)

    # 3. Catalog
    try:
        extract_full_catalog(store)
    except Exception as e:
        print(f"Catalog error: {e}")

    # 4. Dashboard
    try:
        generate_dashboard(store)
    except Exception as e:
        print(f"Dashboard error: {e}")
        traceback.print_exc()

    # 5. Reset untracked stock
    try:
        reset_untracked_stock(store)
    except Exception as e:
        print(f"Stock reset error: {e}")

    # 6. Commission dashboard (separate process)
    try:
        result = subprocess.run(
            ["python3", str(Path(__file__).parent / "build_commission_dashboard.py")],
            capture_output=True, text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"Commission dashboard error: {result.stderr}")
    except Exception as e:
        print(f"Commission dashboard error: {e}")

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.0f}s")


if __name__ == "__main__":
    store_arg = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
    main(store_arg)
