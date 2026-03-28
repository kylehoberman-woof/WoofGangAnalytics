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
    days_open = int(df_slice["ymd"].nunique()) if "ymd" in df_slice.columns else 1
    # Groomer days = sum of unique groomers per day
    groomer_days = 0
    if "ymd" in groom.columns and "salesperson" in groom.columns:
        for _, day_grp in groom.groupby("ymd"):
            groomer_days += day_grp["salesperson"].nunique()
    return {
        "total_revenue": round(total_rev, 2),
        "groom_revenue": round(groom_rev, 2),
        "retail_revenue": round(retail_rev, 2),
        "appointments": appointments,
        "transactions": txns,
        "avg_ticket": round(total_rev / txns, 2) if txns else 0,
        "unique_customers": int(df_slice["customer_id"].nunique()),
        "tips": round(to_py(df_orders_slice["tips"].sum()), 2),
        "days_open": days_open,
        "groomer_days": groomer_days,
    }


def _compute_daily_sub_kpis(df_slice, df_orders_slice):
    """Compute per-day KPI arrays within a period slice for pro-rating.

    Returns list of {dom: day_of_month, dow: day_of_week(0=Mon), ...kpis} sorted by date.
    """
    days = sorted(df_slice["ymd"].dropna().unique())
    from datetime import datetime
    daily = []
    for d in days:
        df_d = df_slice[df_slice["ymd"] == d]
        df_o_d = df_orders_slice[df_orders_slice["ymd"] == d]
        kpis = _compute_period_kpis(df_d, df_o_d)
        dt = datetime.strptime(d, "%Y-%m-%d")
        kpis["dom"] = dt.day          # day of month (1-31)
        kpis["dow"] = dt.weekday()     # day of week (0=Mon, 6=Sun)
        daily.append(kpis)
    return daily


def build_monthly_comparison_data(df_all, df_orders_all):
    """Pre-compute per-month KPIs for all available months.

    Each period includes a 'daily' array for pro-rate comparisons.
    """
    periods = sorted(df_all["ym"].dropna().unique())
    result = {"periods": [], "data": {}}
    for p in periods:
        key = str(p)
        result["periods"].append({"key": key, "label": p.strftime("%b '%y")})
        df_p = df_all[df_all["ym"] == p]
        df_o_p = df_orders_all[df_orders_all["ym"] == p]
        kpis = _compute_period_kpis(df_p, df_o_p)
        kpis["daily"] = _compute_daily_sub_kpis(df_p, df_o_p)
        result["data"][key] = kpis
    return result


def build_weekly_comparison_data(df_all, df_orders_all):
    """Pre-compute per-week KPIs for all available weeks.

    Each period includes a 'daily' array for pro-rate comparisons.
    """
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
        df_w = df_all[df_all["yw"] == w]
        df_o_w = df_orders_all[df_orders_all["yw"] == w]
        kpis = _compute_period_kpis(df_w, df_o_w)
        kpis["daily"] = _compute_daily_sub_kpis(df_w, df_o_w)
        result["data"][w] = kpis
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
    ("days_open", "Days Open", ""),
    ("groomer_days", "Groomer Days", "accent"),
]


def _build_comparison_panel_html(data, prefix, select_id_a, select_id_b, update_fn, title, subtitle, chart_id, quick_buttons=None, detail_fn=None, detail_chart_id=None, prorate=False):
    """Generic comparison panel builder shared by monthly/weekly/daily.

    quick_buttons: list of (label, js_onclick) for shortcut compare buttons
    detail_fn: JS function name for updating detail view (enables Compare/Detail toggle)
    detail_chart_id: canvas ID for the detail breakdown chart
    prorate: if True, adds a pro-rate checkbox for partial period comparisons
    """
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
    # Detail view uses same options, default to latest
    options_detail = "\n".join(
        f'<option value="{p["key"]}"{"selected" if p["key"] == default_b else ""}>{p["label"]}</option>'
        for p in periods
    )

    # Quick compare buttons HTML
    quick_html = ""
    if quick_buttons:
        btns = " ".join(f'<button class="quick-btn" onclick="{js}">{label}</button>' for label, js in quick_buttons)
        quick_html = f'<div class="quick-btns">{btns}</div>'

    # Comparison KPI cards
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

    # Detail KPI cards (single-period, no delta)
    detail_cards = ""
    for key, label, cls in KPI_DEFS:
        did = f"{prefix}-det-{key}"
        detail_cards += f'''<div class="kpi-card detail-card {cls}" id="{did}">
  <div class="kpi-label">{label}</div>
  <div class="kpi-value" id="{did}-val"></div>
</div>
'''

    # View toggle — Detail is default
    toggle_html = f'''<div class="view-toggle" id="{prefix}-toggle">
  <button class="active" onclick="_showView('{prefix}','detail',this)">Detail</button>
  <button onclick="_showView('{prefix}','compare',this)">Compare</button>
</div>'''

    # Detail panel (shown by default)
    detail_select_id = f"{prefix}-detail-sel"
    detail_update_call = f"{detail_fn}()" if detail_fn else ""
    detail_panel = f'''<div id="{prefix}-detail-panel">
  <div class="section">
    <h2><span class="dot"></span>Period Detail</h2>
    <p class="desc">View KPIs for a single period</p>
    <div class="monthly-controls">
      <div class="control-group">
        <label for="{detail_select_id}">Period</label>
        <select id="{detail_select_id}" onchange="{detail_update_call}">{options_detail}</select>
      </div>
    </div>
  </div>
  <div class="detail-grid">{detail_cards}</div>
  <div class="section" id="{prefix}-detail-chart-section">
    <h2><span class="dot"></span>Daily Breakdown</h2>
    <p class="desc">Revenue by day within the selected period</p>
    <div class="chart-container"><canvas id="{detail_chart_id}" style="max-height:400px"></canvas></div>
  </div>
</div>'''

    return f'''{toggle_html}
{detail_panel}
<div id="{prefix}-compare-panel" style="display:none">
<div class="section">
  <h2><span class="dot"></span>{title}</h2>
  <p class="desc">{subtitle}</p>
  {quick_html}
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
    {f'<label class="prorate-toggle" title="Limit Period A to the same number of calendar days as Period B (useful for partial months/weeks)"><input type="checkbox" id="{prefix}-prorate" onchange="{update_fn}()"><span>Pro-rate to match days</span></label>' if prorate else ''}
  </div>
</div>
<div class="comparison-grid">
{cards}</div>
<div class="section">
  <h2><span class="dot"></span>Visual Comparison</h2>
  <p class="desc">Side-by-side bar chart of selected periods</p>
  <div class="chart-container"><canvas id="{chart_id}" style="max-height:400px"></canvas></div>
</div>
</div>
'''


def build_monthly_panel_html(data):
    return _build_comparison_panel_html(
        data, "cmp", "monthA", "monthB", "updateMonthlyComparison",
        "Month-to-Month Comparison", "Select two months to compare key performance metrics side by side",
        "monthlyCompareChart",
        quick_buttons=[
            ("vs Last Month", "_qcMonth('prev')"),
            ("vs Same Month Last Year", "_qcMonth('yoy')"),
        ],
        detail_fn="updateMonthlyDetail",
        detail_chart_id="monthlyDetailChart",
        prorate=True)


def build_weekly_panel_html(data):
    return _build_comparison_panel_html(
        data, "wcmp", "weekA", "weekB", "updateWeeklyComparison",
        "Week-to-Week Comparison", "Select two weeks to compare key performance metrics side by side",
        "weeklyCompareChart",
        quick_buttons=[
            ("vs Last Week", "_qcWeek('prev')"),
            ("vs Same Week Last Year", "_qcWeek('yoy')"),
        ],
        detail_fn="updateWeeklyDetail",
        detail_chart_id="weeklyDetailChart",
        prorate=True)


def build_daily_panel_html(data):
    return _build_comparison_panel_html(
        data, "dcmp", "dayA", "dayB", "updateDailyComparison",
        "Day-to-Day Comparison", "Select two days to compare key performance metrics side by side",
        "dailyCompareChart",
        quick_buttons=[
            ("vs Yesterday", "_qcDay('prev')"),
            ("vs Same Day Last Week", "_qcDay('week')"),
            ("vs Same Day Last Year", "_qcDay('yoy')"),
        ],
        detail_fn="updateDailyDetail",
        detail_chart_id="dailyDetailChart")


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

    # Build year tabs dynamically based on available data
    available_years = sorted(df_all["year"].unique())
    YEARS = {}
    YEAR_LABELS = {}
    for y in available_years:
        y_str = str(y)
        if y == 2024:
            YEARS[y_str] = ("2024-09-26", "2024-12-31")
            YEAR_LABELS[y_str] = "2024 (Sep\u2013Dec)"
        elif y == int(pd.Timestamp.now().year):
            YEARS[y_str] = (f"{y}-01-01", f"{y}-12-31")
            YEAR_LABELS[y_str] = f"{y} (YTD)"
        else:
            YEARS[y_str] = (f"{y}-01-01", f"{y}-12-31")
            YEAR_LABELS[y_str] = f"{y} (Full Year)"

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
.yr-tab-bar { background: #C4276E; padding: 0 24px; display: flex; gap: 4px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.25); flex-wrap: wrap; }
.yr-tab { padding: 14px 32px; border: none; background: transparent; color: rgba(255,255,255,0.6); font-size: 15px; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; font-family: inherit; }
.yr-tab:hover { color: white; background: rgba(255,255,255,0.08); }
.yr-tab.active { color: white; border-bottom-color: white; }
.quick-btns { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.quick-btn { padding: 6px 14px; border: 1.5px solid #C4276E; border-radius: 20px; background: transparent; color: #C4276E; font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: inherit; }
.quick-btn:hover { background: #C4276E; color: white; }
.view-toggle { display: flex; gap: 0; margin: 16px 24px 0; }
.view-toggle button { padding: 8px 20px; border: 2px solid #e0e0e0; background: white; font-size: 0.85rem; font-weight: 600; cursor: pointer; font-family: inherit; color: #666; transition: all 0.2s; }
.view-toggle button:first-child { border-radius: 8px 0 0 8px; }
.view-toggle button:last-child { border-radius: 0 8px 8px 0; border-left: none; }
.view-toggle button.active { background: #C4276E; color: white; border-color: #C4276E; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 22px; padding: 0 24px; }
.detail-card { text-align: center; }
.detail-card .kpi-value { font-size: 1.6rem; }
.prorate-toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; padding-top: 18px; font-size: 0.82rem; font-weight: 600; color: #666; user-select: none; white-space: nowrap; }
.prorate-toggle input { accent-color: #C4276E; width: 16px; height: 16px; cursor: pointer; }
.prorate-toggle:hover span { color: #C4276E; }
"""
    _year_strs = [str(y) for y in available_years]
    _default_year = _year_strs[-1] if len(_year_strs) == 1 else (_year_strs[-2] if len(_year_strs) >= 2 else _year_strs[0])
    _init_chart_checks = "\n".join(
        f"        if (yr === '{y}' && typeof initCharts_{y} === 'function') initCharts_{y}();"
        for y in _year_strs
    )
    tabbed_js = f"""
var _chartsInited = {{}};
function showYear(yr) {{
    document.querySelectorAll('.yr-panel').forEach(function(p) {{ p.style.display = 'none'; }});
    document.querySelectorAll('.yr-tab').forEach(function(t) {{ t.classList.remove('active'); }});
    document.getElementById('panel-' + yr).style.display = 'block';
    document.getElementById('tab-' + yr).classList.add('active');
    if (!_chartsInited[yr]) {{
        _chartsInited[yr] = true;
{_init_chart_checks}
        if (yr === 'monthly') updateMonthlyDetail();
        if (yr === 'weekly') updateWeeklyDetail();
        if (yr === 'daily') updateDailyDetail();
    }}
}}
window.addEventListener('DOMContentLoaded', function() {{ showYear('{_default_year}'); }});
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

/* ── Pro-rate helper: sum daily sub-data up to a calendar day cutoff ── */
function _proRate(periodData, maxDom, useDOM) {
    // periodData has a 'daily' array of {dom, dow, ...kpis}
    // useDOM=true → filter by dom <= maxDom (monthly: calendar day of month)
    // useDOM=false → filter by dow <= maxDom (weekly: day of week index)
    var daily = periodData.daily;
    if (!daily || !daily.length) return periodData;
    var filtered = daily.filter(function(d) { return useDOM ? d.dom <= maxDom : d.dow <= maxDom; });
    if (!filtered.length) return periodData;
    var sum = {days_open: filtered.length};
    ['total_revenue','groom_revenue','retail_revenue','appointments','transactions','unique_customers','tips','groomer_days'].forEach(function(k) {
        sum[k] = 0;
        filtered.forEach(function(d) { sum[k] += d[k]; });
        sum[k] = Math.round(sum[k] * 100) / 100;
    });
    sum.avg_ticket = sum.transactions > 0 ? Math.round(sum.total_revenue / sum.transactions * 100) / 100 : 0;
    return sum;
}

function _updateComparison(DATA, selA, selB, prefix, chartId, chartRef) {
    var keyA = document.getElementById(selA).value;
    var keyB = document.getElementById(selB).value;
    var aRaw = DATA.data[keyA], b = DATA.data[keyB];
    if (!aRaw || !b) return null;
    var labelA = document.getElementById(selA).selectedOptions[0].text;
    var labelB = document.getElementById(selB).selectedOptions[0].text;

    // Check pro-rate checkbox
    var prCb = document.getElementById(prefix + '-prorate');
    var prorated = prCb && prCb.checked && b.daily && aRaw.daily;
    var a = aRaw;
    if (prorated) {
        var useDOM = prefix === 'cmp'; // cmp = monthly → day-of-month, wcmp = weekly → day-of-week
        var field = useDOM ? 'dom' : 'dow';
        // Find max calendar day in each period
        var maxA = 0, maxB = 0;
        aRaw.daily.forEach(function(d) { if (d[field] > maxA) maxA = d[field]; });
        b.daily.forEach(function(d) { if (d[field] > maxB) maxB = d[field]; });
        var cutoff = Math.min(maxA, maxB);
        var daysLabel = useDOM ? cutoff : (cutoff + 1);
        // Pro-rate whichever period has more days
        if (maxA > cutoff) {
            a = _proRate(aRaw, cutoff, useDOM);
            labelA += ' (first ' + daysLabel + ' days)';
        }
        if (maxB > cutoff) {
            b = _proRate(b, cutoff, useDOM);
            labelB += ' (first ' + daysLabel + ' days)';
        }
    }

    var kpis = [
        {key: 'total_revenue', fmt: 'c'}, {key: 'groom_revenue', fmt: 'c'},
        {key: 'retail_revenue', fmt: 'c'}, {key: 'appointments', fmt: 'i'},
        {key: 'transactions', fmt: 'i'}, {key: 'avg_ticket', fmt: 'c'},
        {key: 'unique_customers', fmt: 'i'}, {key: 'tips', fmt: 'c'},
        {key: 'days_open', fmt: 'i'},
        {key: 'groomer_days', fmt: 'i'}
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

/* ── Quick Compare helpers ─────────────────────────── */
function _findPeriodIdx(periods, key) {
    for (var i = 0; i < periods.length; i++) { if (periods[i].key === key) return i; }
    return -1;
}
function _setSelAndUpdate(selA, keyA, updateFn) {
    var el = document.getElementById(selA);
    for (var i = 0; i < el.options.length; i++) {
        if (el.options[i].value === keyA) { el.selectedIndex = i; updateFn(); return true; }
    }
    return false;
}

function _qcMonth(mode) {
    var keyB = document.getElementById('monthB').value;
    var idx = _findPeriodIdx(MONTHLY_DATA.periods, keyB);
    if (idx < 0) return;
    var targetKey;
    if (mode === 'prev') {
        if (idx <= 0) return;
        targetKey = MONTHLY_DATA.periods[idx - 1].key;
    } else {
        // yoy: subtract 1 year from the period key (format: "2026-03")
        var parts = keyB.split('-');
        targetKey = (parseInt(parts[0]) - 1) + '-' + parts[1];
    }
    _setSelAndUpdate('monthA', targetKey, updateMonthlyComparison);
}

function _qcWeek(mode) {
    var keyB = document.getElementById('weekB').value;
    var idx = _findPeriodIdx(WEEKLY_DATA.periods, keyB);
    if (idx < 0) return;
    var targetKey;
    if (mode === 'prev') {
        if (idx <= 0) return;
        targetKey = WEEKLY_DATA.periods[idx - 1].key;
    } else {
        // yoy: "2026-W11" → "2025-W11"
        var parts = keyB.split('-W');
        targetKey = (parseInt(parts[0]) - 1) + '-W' + parts[1];
    }
    _setSelAndUpdate('weekA', targetKey, updateWeeklyComparison);
}

function _qcDay(mode) {
    var keyB = document.getElementById('dayB').value;
    var idx = _findPeriodIdx(DAILY_DATA.periods, keyB);
    if (idx < 0) return;
    var targetKey;
    if (mode === 'prev') {
        if (idx <= 0) return;
        targetKey = DAILY_DATA.periods[idx - 1].key;
    } else if (mode === 'week') {
        // 7 days back
        var d = new Date(keyB + 'T12:00:00');
        d.setDate(d.getDate() - 7);
        targetKey = d.toISOString().slice(0, 10);
    } else {
        // yoy: "2026-03-17" → "2025-03-17"
        var parts = keyB.split('-');
        targetKey = (parseInt(parts[0]) - 1) + '-' + parts[1] + '-' + parts[2];
    }
    _setSelAndUpdate('dayA', targetKey, updateDailyComparison);
}

/* ── Compare / Detail toggle ───────────────────────── */
function _showView(prefix, mode, btn) {
    var toggle = document.getElementById(prefix + '-toggle');
    toggle.querySelectorAll('button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    document.getElementById(prefix + '-compare-panel').style.display = mode === 'compare' ? 'block' : 'none';
    document.getElementById(prefix + '-detail-panel').style.display = mode === 'detail' ? 'block' : 'none';
    if (mode === 'detail') {
        if (prefix === 'cmp') updateMonthlyDetail();
        if (prefix === 'wcmp') updateWeeklyDetail();
        if (prefix === 'dcmp') updateDailyDetail();
    }
}

/* ── Detail view (single period KPIs + breakdown chart) ── */
var _monthlyDetailChart = null, _weeklyDetailChart = null, _dailyDetailChart = null;

function _updateDetail(DATA, selId, prefix, chartId, chartRef, showChart) {
    var key = document.getElementById(selId).value;
    var d = DATA.data[key];
    if (!d) return chartRef;
    var kpis = [
        {key: 'total_revenue', fmt: 'c'}, {key: 'groom_revenue', fmt: 'c'},
        {key: 'retail_revenue', fmt: 'c'}, {key: 'appointments', fmt: 'i'},
        {key: 'transactions', fmt: 'i'}, {key: 'avg_ticket', fmt: 'c'},
        {key: 'unique_customers', fmt: 'i'}, {key: 'tips', fmt: 'c'},
        {key: 'days_open', fmt: 'i'},
        {key: 'groomer_days', fmt: 'i'}
    ];
    kpis.forEach(function(kpi) {
        var el = document.getElementById(prefix + '-det-' + kpi.key + '-val');
        if (el) el.textContent = kpi.fmt === 'c' ? _mfc(d[kpi.key]) : _mfi(d[kpi.key]);
    });
    // Breakdown chart: horizontal bars for the key revenue metrics
    var chartSection = document.getElementById(prefix + '-detail-chart-section');
    if (!showChart) { if (chartSection) chartSection.style.display = 'none'; return chartRef; }
    if (chartSection) chartSection.style.display = 'block';
    if (chartRef) chartRef.destroy();
    var ctx = document.getElementById(chartId);
    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Total Revenue', 'Grooming', 'Retail', 'Avg Ticket', 'Tips'],
            datasets: [{
                data: [d.total_revenue, d.groom_revenue, d.retail_revenue, d.avg_ticket, d.tips],
                backgroundColor: ['#C4276E', '#1B6B6B', '#6B3520', '#2E7D32', '#F4A261'],
                borderRadius: 6
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(c) { return '$' + c.parsed.x.toLocaleString(); } } } },
            scales: { x: { ticks: { callback: function(v) { return '$' + v.toLocaleString(); } } } }
        }
    });
}

function updateMonthlyDetail() { _monthlyDetailChart = _updateDetail(MONTHLY_DATA, 'cmp-detail-sel', 'cmp', 'monthlyDetailChart', _monthlyDetailChart, true); }
function updateWeeklyDetail() { _weeklyDetailChart = _updateDetail(WEEKLY_DATA, 'wcmp-detail-sel', 'wcmp', 'weeklyDetailChart', _weeklyDetailChart, true); }
function updateDailyDetail() { _dailyDetailChart = _updateDetail(DAILY_DATA, 'dcmp-detail-sel', 'dcmp', 'dailyDetailChart', _dailyDetailChart, false); }
"""

    _store_title = "Woof Gang " + ("Port Washington" if "port" in str(store.output_dir).lower() else "Hicksville")
    _home_url = "index.html" if "port" in str(store.output_dir).lower() else "../port-washington/index.html"
    html = gd.html_head(_store_title, f"Store Performance Analysis \u00b7 Sales through {through}", home_url=_home_url, show_home=False)
    html = html.replace("</style>", tabbed_css + "\n</style>", 1)
    html += f'<div style="background:#C4276E;padding:12px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:101;box-shadow:0 2px 12px rgba(196,39,110,0.3)">\n'
    html += f'  <a href="{_home_url}" style="color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.85rem;font-weight:600">&larr; Home</a>\n'
    html += f'  <div style="color:rgba(255,255,255,0.85);font-size:0.82rem">Updated {through}</div>\n'
    html += '</div>\n'
    html += '<div class="yr-tab-bar">\n'
    for yr in _year_strs:
        active = "active" if yr == _default_year else ""
        html += f'  <button class="yr-tab {active}" onclick="showYear(\'{yr}\')" id="tab-{yr}">{YEAR_LABELS[yr]}</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'monthly\')" id="tab-monthly">Monthly</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'weekly\')" id="tab-weekly">Weekly</button>\n'
    html += '  <button class="yr-tab" onclick="showYear(\'daily\')" id="tab-daily">Daily</button>\n'
    html += '</div>\n'

    for yr in _year_strs:
        display = "block" if yr == _default_year else "none"
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

    _fn = "PortWashington" if "port" in str(store.output_dir).lower() else "Hicksville"
    out_path = store.output_dir / f"WoofGang_{_fn}_NY_AllYears_Dashboard.html"
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
    # Ensure 'import run' in other modules gets this module, not a separate copy
    # with stale PW defaults (Python treats __main__ and 'run' as different modules)
    sys.modules['run'] = sys.modules['__main__']
    store_arg = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
    main(store_arg)
