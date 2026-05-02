#!/usr/bin/env python3
"""
Generate branded HTML dashboards for Woof Gang store analyses.
Uses cached FranPOS data and Woof Gang brand aesthetics.
Elite operations analysis with run-rate forecasting, margin analysis,
staffing heatmaps, customer concentration, retail attachment, and more.
"""

import json
import math
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta

import pandas as pd

from classifier import classify_item
from config import C, FDD_RETAIL_COGS_PCT, FDD_GROOM_COGS_PCT
from formatting import to_py, jslist

# Backward compat: these are read from the 'run' module at import time by some scripts.
# They can be patched at runtime (e.g., for Hicksville).
import run
STORE_NAME = run.STORE_NAME
DATA_DIR = run.DATA_DIR
START_DATE = run.START_DATE
END_DATE = run.END_DATE


# ─── Load & Transform Data ────────────────────────────────────────────────────

def load_data():
    cache = DATA_DIR / "all_data.json"
    with open(cache) as f:
        raw = json.load(f)

    items = raw["order_items"]
    orders = raw["orders"]
    employees = raw.get("employees", [])

    emp_map = {}
    for e in employees:
        eid = e.get("Id")
        name = f"{e.get('FirstName', '')} {e.get('LastName', '')}".strip()
        emp_map[eid] = name

    rows = []
    for item in items:
        name = item.get("Name", "")
        sku = item.get("Sku", "")
        cls = classify_item(name, sku)
        qty = float(item.get("Quantity", 1))
        price = float(item.get("Price", 0))
        disc = float(item.get("Discount", 0))
        cost = float(item.get("Cost", 0))
        net = price * qty - disc
        rows.append({
            "order_id": item.get("OrderId"),
            "customer_id": item.get("CustomerId"),
            "created": item.get("CreatedOn"),
            "name": name,
            "sku": sku,
            "price": price,
            "quantity": qty,
            "discount": disc,
            "cost": cost,
            "cogs": cost * qty,
            "net_sales": net,
            "salesperson": item.get("SalesPerson", ""),
            "return_reason": item.get("ReturnReason"),
            "return_disposition": item.get("ReturnDisposition"),
            **cls,
        })

    df = pd.DataFrame(rows)
    df["created"] = pd.to_datetime(df["created"])
    df["month"] = df["created"].dt.month
    df["year"] = df["created"].dt.year
    df["ym"] = df["created"].dt.to_period("M")
    df["yw"] = df["created"].dt.isocalendar().week.astype(str).str.zfill(2).radd(df["created"].dt.isocalendar().year.astype(str) + "-W")
    df["ymd"] = df["created"].dt.strftime("%Y-%m-%d")
    df["dow"] = df["created"].dt.day_name()
    df["hour"] = df["created"].dt.hour
    df = df[df["groom_category"] != "exclude"]

    order_rows = []
    for o in orders:
        order_rows.append({
            "order_id": o.get("OrderId"),
            "customer_id": o.get("CustomerId"),
            "employee_id": o.get("EmployeeId"),
            "created": o.get("CreatedOn"),
            "subtotal": float(o.get("SubTotal", 0)),
            "discount_total": float(o.get("DiscountTotal", 0)),
            "tax_total": float(o.get("TaxTotal", 0)),
            "tips": float(o.get("Tips", 0)),
            "total": float(o.get("Total", 0)),
        })
    if order_rows:
        df_orders = pd.DataFrame(order_rows)
        df_orders["created"] = pd.to_datetime(df_orders["created"])
        # Fill in missing orders from line items (timezone boundary issue)
        missing_ids = set(df["order_id"].dropna()) - set(df_orders["order_id"].dropna())
        if missing_ids:
            missing_df = df[df["order_id"].isin(missing_ids)].groupby("order_id").agg(
                customer_id=("customer_id", "first"),
                created=("created", "first"),
                subtotal=("net_sales", "sum"),
                total=("net_sales", "sum"),
            ).reset_index()
            missing_df["employee_id"] = None
            missing_df["discount_total"] = 0.0
            missing_df["tax_total"] = 0.0
            missing_df["tips"] = 0.0
            df_orders = pd.concat([df_orders, missing_df], ignore_index=True)
            print(f"  Filled in {len(missing_ids)} missing orders from line items")
    else:
        # Reconstruct orders from line items
        grp = df.groupby("order_id").agg(
            customer_id=("customer_id", "first"),
            created=("created", "first"),
            subtotal=("net_sales", "sum"),
            total=("net_sales", "sum"),
        ).reset_index()
        grp["employee_id"] = None
        grp["discount_total"] = 0.0
        grp["tax_total"] = 0.0
        grp["tips"] = 0.0
        df_orders = grp
    df_orders["month"] = df_orders["created"].dt.month
    df_orders["year"] = df_orders["created"].dt.year
    df_orders["ym"] = df_orders["created"].dt.to_period("M")
    df_orders["yw"] = df_orders["created"].dt.isocalendar().week.astype(str).str.zfill(2).radd(df_orders["created"].dt.isocalendar().year.astype(str) + "-W")
    df_orders["ymd"] = df_orders["created"].dt.strftime("%Y-%m-%d")
    df_orders["dow"] = df_orders["created"].dt.day_name()
    df_orders["hour"] = df_orders["created"].dt.hour

    return df, df_orders, emp_map


# ─── Helpers ──────────────────────────────────────────────────────────────────

from formatting import fc, fp, fi, esc

def get_periods(df):
    """Get sorted list of year-month periods from data.
    Returns list of dicts: [{period: Period, label: "Dec '25", idx: 0}, ...]
    """
    periods = sorted(df["ym"].dropna().unique())
    return [{"period": p, "label": p.strftime("%b '%y"), "month": p.month, "year": p.year, "idx": i}
            for i, p in enumerate(periods)]


def periods_data(df, periods, col="net_sales", agg="sum"):
    """Get aggregated values for each period."""
    grouped = df.groupby("ym")[col].sum() if agg == "sum" else df.groupby("ym")[col].count()
    return [to_py(grouped.get(p["period"], 0)) for p in periods]


def trend_line(x_vals, y_vals):
    """Simple linear regression, return slope and intercept."""
    n = len(x_vals)
    if n < 2:
        return 0, y_vals[0] if y_vals else 0
    sx = sum(x_vals)
    sy = sum(y_vals)
    sxx = sum(x * x for x in x_vals)
    sxy = sum(x * y for x, y in zip(x_vals, y_vals))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


# ─── CSS (shared) ────────────────────────────────────────────────────────────

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: """ + C['pink_bg'] + """;
    color: #333;
    line-height: 1.6;
}
.header {
    background: linear-gradient(135deg, """ + C['teal'] + """ 0%, """ + C['brown'] + """ 100%);
    color: white;
    padding: 40px 0 30px;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.header::before {
    content: '';
    position: absolute;
    top: -50%; left: -50%; width: 200%; height: 200%;
    background: radial-gradient(circle, rgba(196,39,110,0.15) 0%, transparent 50%);
}
.header-timestamp {
    position: absolute; top: 12px; right: 20px; font-size: 0.78rem;
    opacity: 0.85; font-weight: 400; z-index: 1;
}
.header h1 {
    font-size: 2.2rem; font-weight: 800; letter-spacing: -0.02em;
    position: relative; margin-bottom: 4px;
}
.header .subtitle { font-size: 1rem; font-weight: 400; opacity: 0.9; position: relative; }
.header .brand-tag {
    display: inline-block; background: """ + C['magenta'] + """; color: white;
    padding: 4px 16px; border-radius: 20px; font-size: 0.75rem; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase; margin-top: 12px; position: relative;
}
.container { max-width: 1440px; margin: 0 auto; padding: 30px 24px; }
.kpi-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px; margin-bottom: 28px;
}
.kpi-card {
    background: white; border-radius: 12px; padding: 22px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-left: 4px solid """ + C['teal'] + """;
    transition: transform 0.2s, box-shadow 0.2s;
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.kpi-card.accent { border-left-color: """ + C['magenta'] + """; }
.kpi-card.green { border-left-color: """ + C['green'] + """; }
.kpi-card.brown { border-left-color: """ + C['brown'] + """; }
.kpi-card.amber { border-left-color: """ + C['amber'] + """; }
.kpi-label {
    font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: #888; margin-bottom: 4px;
}
.kpi-value {
    font-size: 1.65rem; font-weight: 800; color: """ + C['teal'] + """; letter-spacing: -0.02em;
}
.kpi-card.accent .kpi-value { color: """ + C['magenta'] + """; }
.kpi-card.green .kpi-value { color: """ + C['green'] + """; }
.kpi-card.brown .kpi-value { color: """ + C['brown'] + """; }
.kpi-card.amber .kpi-value { color: """ + C['amber'] + """; }
.kpi-sub { font-size: 0.78rem; color: #999; margin-top: 3px; }
.section {
    background: white; border-radius: 12px; padding: 26px;
    margin-bottom: 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.section h2 {
    font-size: 1.2rem; font-weight: 700; color: """ + C['brown'] + """;
    margin-bottom: 3px; display: flex; align-items: center; gap: 8px;
}
.section h2 .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: """ + C['magenta'] + """; display: inline-block;
}
.section .desc { font-size: 0.82rem; color: #888; margin-bottom: 18px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 22px; }
@media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
thead th {
    background: """ + C['teal'] + """; color: white; padding: 9px 11px;
    text-align: left; font-weight: 600; font-size: 0.72rem;
    text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap;
}
thead th:first-child { border-radius: 8px 0 0 0; }
thead th:last-child { border-radius: 0 8px 0 0; }
tbody tr { border-bottom: 1px solid #f0f0f0; }
tbody tr:nth-child(even) { background: """ + C['pink_bg'] + """; }
tbody tr:hover { background: #fce4ec; }
tbody td { padding: 8px 11px; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.green { color: """ + C['green'] + """; font-weight: 600; }
td.red { color: """ + C['red'] + """; font-weight: 600; }
td.magenta { color: """ + C['magenta'] + """; font-weight: 600; }
.chart-container { position: relative; width: 100%; max-height: 380px; }
.insight-box {
    background: """ + C['pink_bg'] + """; border-left: 4px solid """ + C['magenta'] + """;
    border-radius: 0 8px 8px 0; padding: 14px 18px; margin-top: 14px; font-size: 0.88rem;
}
.insight-box strong { color: """ + C['magenta'] + """; }
.alert-box {
    background: #FFF3E0; border-left: 4px solid #FF9800;
    border-radius: 0 8px 8px 0; padding: 14px 18px; margin-top: 14px; font-size: 0.88rem;
}
.alert-box strong { color: #E65100; }
.opp-box {
    background: #E8F5E9; border-left: 4px solid """ + C['green'] + """;
    border-radius: 0 8px 8px 0; padding: 14px 18px; margin-top: 14px; font-size: 0.88rem;
}
.opp-box strong { color: """ + C['green'] + """; }
.badge {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.68rem; font-weight: 600; text-transform: uppercase;
}
.badge-green { background: #C8E6C9; color: #2E7D32; }
.badge-yellow { background: #FFF9C4; color: #F57F17; }
.badge-red { background: #FFCDD2; color: #C62828; }
.badge-teal { background: #B2DFDB; color: #00695C; }
.footer { text-align: center; padding: 30px; color: #999; font-size: 0.75rem; }
.footer span { color: """ + C['magenta'] + """; }
.heatmap-grid {
    display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;
}
.heatmap-cell {
    text-align: center; padding: 10px 4px; border-radius: 6px;
    font-size: 0.75rem; font-weight: 600;
}
.heatmap-label { font-size: 0.65rem; color: #888; margin-bottom: 2px; }
.talking-point {
    background: """ + C['pink_bg'] + """; border-radius: 8px; padding: 12px 16px 12px 34px;
    margin-bottom: 8px; font-size: 0.88rem; position: relative;
}
.talking-point::before { content: '\\1F43E'; position: absolute; left: 10px; top: 12px; font-size: 0.95rem; }
.objection { border-left: 3px solid """ + C['brown'] + """; padding: 10px 14px; margin-bottom: 10px; border-radius: 0 8px 8px 0; }
.objection .q { font-weight: 700; color: """ + C['brown'] + """; margin-bottom: 3px; }
.objection .a { font-size: 0.82rem; color: #555; font-style: italic; }
.scenario-card {
    background: white; border: 2px solid #e0e0e0; border-radius: 10px;
    padding: 18px; text-align: center; transition: border-color 0.2s;
}
.scenario-card.recommended { border-color: """ + C['green'] + """; background: #f1f8e9; }
.scenario-card h4 { font-size: 1.3rem; margin-bottom: 4px; }
.scenario-card .metric { font-size: 0.8rem; color: #666; margin: 2px 0; }
.scenario-card .metric strong { color: #333; }
.progress-bar-bg {
    background: #e0e0e0; border-radius: 6px; height: 10px; overflow: hidden; margin: 4px 0;
}
.progress-bar-fill { height: 100%; border-radius: 6px; }
.monthly-controls {
    display: flex; align-items: center; gap: 16px;
    margin-top: 14px; flex-wrap: wrap;
}
.monthly-controls .control-group { display: flex; flex-direction: column; gap: 4px; }
.monthly-controls label {
    font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: #888;
}
.monthly-controls select {
    padding: 10px 16px; border: 2px solid #e0e0e0; border-radius: 8px;
    font-size: 0.95rem; font-weight: 600; font-family: inherit;
    background: white; cursor: pointer; min-width: 160px;
    transition: border-color 0.2s;
}
.monthly-controls select:focus { border-color: #C4276E; outline: none; }
.vs-label {
    font-size: 0.85rem; font-weight: 700; color: #999;
    padding-top: 18px;
}
.comparison-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px; margin-bottom: 22px;
}
.comparison-card .comparison-values {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 8px; gap: 8px;
}
.comparison-card .cmp-col { text-align: center; flex: 1; }
.comparison-card .cmp-month-label {
    font-size: 0.7rem; font-weight: 600; color: #999;
    text-transform: uppercase; margin-bottom: 2px;
}
.comparison-card .cmp-delta { flex: 0 0 auto; padding: 0 8px; }
.cmp-delta-val { font-size: 0.9rem; font-weight: 700; }
.cmp-delta-pct { font-size: 0.75rem; color: #888; }
.delta-positive .cmp-delta-val { color: #2E7D32; }
.delta-positive .cmp-delta-pct { color: #2E7D32; }
.delta-negative .cmp-delta-val { color: #C62828; }
.delta-negative .cmp-delta-pct { color: #C62828; }
"""


def html_head(title, subtitle="", timestamp=None, home_url="index.html", show_home=True, store_switch=None):
    if timestamp is None:
        from zoneinfo import ZoneInfo
        timestamp = datetime.now(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p ET")
    from config import PORTAL_BACK_JS
    _home_link = (f'<a id="portal-back" href="{home_url}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.85rem;font-weight:600">&larr; Home</a>'
        f'{PORTAL_BACK_JS}') if show_home else ''
    _switch_link = f'<br><a href="{store_switch[0]}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.78rem">&#x21C4; {store_switch[1]}</a>' if store_switch else ''
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{CSS}</style>
</head>
<body>
<div class="header">
    <div class="header-timestamp">Updated {timestamp}{_switch_link}</div>
    {_home_link}
    <h1>{esc(title)}</h1>
    <div class="subtitle">{esc(subtitle)}</div>
    <div class="brand-tag">Woof Gang Bakery & Grooming</div>
</div>
<div class="container">
"""

HTML_FOOT = """
</div>
<div class="footer">
    Generated {date} &mdash; <span>Woof Gang Bakery & Grooming</span> &mdash; Operations Intelligence Dashboard
</div>
</body>
</html>
""".format(date=datetime.now().strftime("%B %d, %Y"))


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD 1: Main Store Performance Analysis
# ═════════════════════════════════════════════════════════════════════════════

def generate_main_dashboard(df, df_orders, output_path, body_only=False, year_suffix=""):
    print("Generating main store analysis dashboard...")

    groom = df[df["is_groom"] == True]
    retail = df[df["is_retail"] == True]
    gifts = df[df["is_gift_card"] == True]

    total_net = df["net_sales"].sum()
    groom_rev = groom["net_sales"].sum()
    retail_rev = retail["net_sales"].sum()
    gift_rev = gifts["net_sales"].sum()
    total_txns = df_orders.shape[0]
    avg_txn = total_net / total_txns if total_txns else 0
    total_units = int(df["quantity"].sum())
    unique_customers = df["customer_id"].nunique()
    total_tips = df_orders["tips"].sum()
    total_discounts = df_orders["discount_total"].sum()

    # FDD-standard COGS (POS cost data is unreliable)
    retail_cogs = retail_rev * FDD_RETAIL_COGS_PCT / 100
    groom_cogs = groom_rev * FDD_GROOM_COGS_PCT / 100
    total_cogs = retail_cogs + groom_cogs
    gross_profit = total_net - total_cogs
    gross_margin_pct = gross_profit / total_net * 100 if total_net else 0

    # ── Run Rate & Forecast ──
    periods = get_periods(df)
    n_periods = len(periods)
    monthly_rev = list(periods_data(df, periods, "net_sales"))

    # Pro-rate the current partial month using day-of-week weighting from
    # trailing 90 days of historical data. A naive (MTD/days × days_in_month)
    # projection over-states months with heavy weekends remaining and
    # under-states months with slow weekdays remaining.
    _rr_prorated = False
    _rr_method = "naive"          # 'naive' | 'dow_weighted'
    _partial_raw = 0.0            # actual MTD revenue (unprojected)
    if monthly_rev and periods:
        from datetime import date as _d_rr, timedelta as _td_rr
        import calendar as _cal_rr
        _last_p = periods[-1]
        _today_rr = _d_rr.today()
        _last_data_dt = df["created"].max().date() if len(df) else _today_rr
        if _last_p["year"] == _last_data_dt.year and _last_p["month"] == _last_data_dt.month:
            _days_elapsed = _last_data_dt.day
            _days_in_month = _cal_rr.monthrange(_today_rr.year, _today_rr.month)[1]
            if _days_elapsed > 0 and _days_elapsed < _days_in_month:
                _partial_raw = monthly_rev[-1]

                # Build per-day-of-week mean revenue from trailing 90 days
                # of complete data (exclude the current partial month so
                # the partial pattern doesn't bias the weights).
                _month_start = _d_rr(_last_data_dt.year, _last_data_dt.month, 1)
                _hist_start = _month_start - _td_rr(days=90)
                _hist_mask = (df["created"].dt.date >= _hist_start) & (df["created"].dt.date < _month_start)
                _hist_df = df.loc[_hist_mask]

                if len(_hist_df) > 0:
                    # Sum revenue per calendar day, then reindex to a full
                    # date range so closed days contribute 0 to the
                    # corresponding day-of-week average (preserving the
                    # expected closure pattern). Then mean by dow.
                    _daily = _hist_df.groupby(_hist_df["created"].dt.date)["net_sales"].sum()
                    _full_idx = pd.date_range(_hist_start, _month_start - _td_rr(days=1), freq="D").date
                    _daily = _daily.reindex(_full_idx, fill_value=0.0)
                    _dow_idx = pd.DatetimeIndex(_daily.index).dayofweek
                    _dow_avg = pd.Series(_daily.values).groupby(_dow_idx).mean()
                    _overall_avg = float(_dow_avg.mean()) if len(_dow_avg) else 0.0

                    # Project remaining days using their day-of-week averages.
                    _remaining_proj = 0.0
                    for _d_offset in range(_days_elapsed + 1, _days_in_month + 1):
                        _proj_date = _d_rr(_last_data_dt.year, _last_data_dt.month, _d_offset)
                        _dow = _proj_date.weekday()
                        _remaining_proj += float(_dow_avg.get(_dow, _overall_avg))

                    monthly_rev[-1] = _partial_raw + _remaining_proj
                    _rr_prorated = True
                    _rr_method = "dow_weighted"
                else:
                    # Fallback to naive pro-rata if no usable history
                    monthly_rev[-1] = _partial_raw / _days_elapsed * _days_in_month
                    _rr_prorated = True
                    _rr_method = "naive"

    # Last-quarter average (last 3 periods or all if < 3)
    lq_count = min(3, n_periods)
    lq_months = list(monthly_rev[-lq_count:]) if lq_count else []
    lq_avg = sum(lq_months) / len(lq_months) if lq_months else 0
    lq_annualized = lq_avg * 12
    _rr_method_note = " (DOW-weighted projection)" if _rr_method == "dow_weighted" else (" (partial mo. pro-rated)" if _rr_prorated else "")
    _rr_sub = f"Last {lq_count}mo avg: {fc(lq_avg)}" + _rr_method_note

    # Linear trend — only meaningful with 6+ months of data
    months_x = list(range(1, n_periods + 1))
    slope, intercept = trend_line(months_x, monthly_rev)
    trend_values = [slope * m + intercept for m in months_x]

    _too_few_for_trend = n_periods < 6

    if _too_few_for_trend:
        # ── Seasonality-Adjusted Growth Forecast ─────────────────────────────
        # Uses last year's monthly pattern + this year's YoY growth rate to
        # project remaining months, then blends with run rate for stability.
        import run as _r
        from datetime import date as _date_fc
        import calendar as _cal_fc
        import json as _json_fc
        from collections import defaultdict as _dd

        _this_year = periods[0]["year"] if periods else 2026
        _prior_year = _this_year - 1

        # Build prior year monthly revenue dict {1: $rev, 2: $rev, ...}
        # Load from all_data.json and filter for prior year
        _prior_by_month = {}
        _data_path = _r.DATA_DIR / "all_data.json"
        if _data_path.exists():
            with open(_data_path) as _f:
                _all_raw = _json_fc.load(_f)
            _pm = _dd(float)
            _prior_prefix = str(_prior_year)
            for _item in _all_raw.get("order_items", []):
                _created = _item.get("CreatedOn", "")
                if not _created.startswith(_prior_prefix):
                    continue
                _n = _item.get("Name", "")
                if "GIFT" in _n.upper() or "DEPOSIT" in _n.upper() or "NO-SHOW" in _n.upper() or "NO SHOW" in _n.upper():
                    continue
                _mo_str = _created[:7]  # "2025-03"
                _qty = float(_item.get("Quantity", 1))
                _pr = float(_item.get("Price", 0))
                _dc = float(_item.get("Discount", 0))
                _pm[_mo_str] += _pr * _qty - _dc
            for _k, _v in _pm.items():
                try:
                    _prior_by_month[int(_k.split("-")[1])] = _v
                except (ValueError, IndexError):
                    pass

        # Detect partial month
        _today_fc = _date_fc.today()
        _last_day_fc = _cal_fc.monthrange(_today_fc.year, _today_fc.month)[1]
        _last_partial_fc = _today_fc.day < _last_day_fc

        # Build current year monthly revenue dict {1: $rev, 2: $rev, ...}
        _curr_by_month = {}
        for p, rev in zip(periods, monthly_rev):
            _curr_by_month[p["month"]] = rev

        # Pro-rate the current partial month to a full-month estimate
        # Skip if monthly_rev was already pro-rated above (avoids double pro-rating)
        if _last_partial_fc and _today_fc.month in _curr_by_month and not _rr_prorated:
            _curr_by_month[_today_fc.month] = (
                _curr_by_month[_today_fc.month] / _today_fc.day * _last_day_fc
            )

        # YoY growth rate: compare same months that exist in both years
        _overlap_months = sorted(set(_curr_by_month.keys()) & set(_prior_by_month.keys()))
        if _overlap_months:
            _ytd_curr = sum(_curr_by_month[m] for m in _overlap_months)
            _ytd_prior = sum(_prior_by_month[m] for m in _overlap_months)
            _yoy_growth = _ytd_curr / _ytd_prior if _ytd_prior > 0 else 1.0
        else:
            _yoy_growth = 1.0

        # Method 1: Seasonality-adjusted — apply growth rate to prior year's remaining months
        _actuals_sum = sum(_curr_by_month.values())
        _covered_months = set(_curr_by_month.keys())
        _seasonal_projected = 0.0
        for _mo in range(1, 13):
            if _mo not in _covered_months:
                _prior_mo_rev = _prior_by_month.get(_mo, 0)
                _seasonal_projected += _prior_mo_rev * _yoy_growth
        _seasonal_forecast = _actuals_sum + _seasonal_projected

        # Method 2: Run rate (already calculated above)
        # lq_annualized

        # Blend: 60% seasonality-adjusted, 40% run rate
        conservative_forecast = _seasonal_forecast * 0.6 + lq_annualized * 0.4

        _fc_method = f"{_yoy_growth - 1:+.0%} YoY growth × {_prior_year} seasonality + run rate"

        # Keep slope/trend for chart (use simple current-year trend for display)
        slope, intercept = trend_line(months_x, monthly_rev)
        trend_values = [slope * m + intercept for m in months_x]
    else:
        # Project next 12 months via trend
        forecast_next_yr = sum(slope * m + intercept for m in range(n_periods + 1, n_periods + 13))
        # Conservative forecast: average of LQ annualized and trend-projected
        conservative_forecast = (lq_annualized + forecast_next_yr) / 2
        _fc_method = None

    # Dynamic titles
    _store_label = STORE_NAME.split("--")[-1].strip() if "--" in STORE_NAME else STORE_NAME
    _date_range = f"{START_DATE} to {END_DATE}"
    _first_yr = periods[0]["year"] if periods else 2025
    _last_yr = periods[-1]["year"] if periods else 2025
    _yr_label = str(_first_yr) if _first_yr == _last_yr else f"{_first_yr}-{_last_yr}"

    if body_only:
        html = ""
    else:
        _home_url = "../index.html"
        html = html_head(
            f"Woof Gang {_store_label}",
            f"Store Performance Analysis — {_date_range}",
            home_url=_home_url,
        )

    # ── KPI Row 1: Core Financials ──
    html += '<div class="kpi-grid">\n'
    if _too_few_for_trend:
        kpis = [
            (f"{_yr_label} Net Sales (YTD)", fc(total_net), f"{n_periods} months of data", ""),
            (f"Full Year {_this_year} Forecast", fc(conservative_forecast), _fc_method, "green"),
            ("Recent Run Rate (Annualized)", fc(lq_annualized), _rr_sub, "accent"),
            (f"YoY Growth vs {_prior_year}", f"{_yoy_growth - 1:+.1%}", f"Same-month comparison ({len(_overlap_months)} months)", "green"),
        ]
    else:
        kpis = [
            (f"{_yr_label} Net Sales", fc(total_net), f"{n_periods} months of data", ""),
            ("Annual Forecast (Conservative)", fc(conservative_forecast), f"Trend: {fc(forecast_next_yr)}", "green"),
            ("Recent Run Rate (Annualized)", fc(lq_annualized), _rr_sub, "accent"),
            ("Monthly Growth Rate", f"+{fc(slope)}/mo", f"Trajectory: {fp(slope/monthly_rev[0]*100 if monthly_rev[0] else 0)}/mo from baseline" + _rr_method_note, "green"),
        ]
    for label, value, sub, cls in kpis:
        html += f'<div class="kpi-card {cls}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>\n'
    html += '</div>\n'

    # Commission breakdown
    groomer_commission = groom_rev * 0.50
    store_net_after_comm = groom_rev - groomer_commission

    # ── KPI Row 2: Operations ──
    html += '<div class="kpi-grid">\n'
    kpis2 = [
        ("Grooming Revenue", fc(groom_rev), f"{groom_rev/total_net*100:.1f}% of total", "accent"),
        ("Groomer Commission (50%)", fc(groomer_commission), f"Paid to groomers", ""),
        ("Store Net (Groom)", fc(store_net_after_comm), "After 50% commission", ""),
        ("Retail Revenue", fc(retail_rev), f"{retail_rev/total_net*100:.1f}% of total", "brown"),
        ("Est. Gross Profit", fc(gross_profit), f"{fp(gross_margin_pct)} margin (FDD standards)", "green"),
        ("Transactions", fi(total_txns), f"Avg ticket: {fc(avg_txn)}", ""),
        ("Unique Customers", fi(unique_customers), f"Active in period", ""),
        ("Tips Collected", fc(total_tips), f"Avg when tipped: {fc(total_tips / max(1, len(df_orders[df_orders['tips']>0])))}", ""),
        ("Discounts Given", fc(total_discounts), f"{total_discounts/total_net*100:.1f}% of net sales", "amber"),
    ]
    for label, value, sub, cls in kpis2:
        html += f'<div class="kpi-card {cls}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>\n'
    html += '</div>\n'

    # ── Revenue Trajectory + Forecast ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Revenue Trajectory & Forecast</h2>\n'
    html += f'<p class="desc">Monthly actual vs trend line — {n_periods} months of data</p>\n'
    html += '<div class="chart-container"><canvas id="trajectoryChart" style="max-height:340px"></canvas></div>\n'

    period_growth = (monthly_rev[-1] - monthly_rev[0]) / monthly_rev[0] * 100 if (monthly_rev and monthly_rev[0]) else 0
    if _too_few_for_trend:
        _prior_total = sum(_prior_by_month.values()) if _prior_by_month else 0
        _prior_rev_str = fc(_prior_total)
        _yoy_pct = (_yoy_growth - 1) * 100
        _sign = "+" if _yoy_pct >= 0 else ""
        html += f'<div class="opp-box"><strong>YTD {_this_year}: {fc(total_net)}</strong> across {n_periods} months. Full-year {_this_year} forecast: <strong>{fc(conservative_forecast)}</strong> &mdash; {_sign}{_yoy_pct:.0f}% YoY growth applied to {_prior_year} seasonal pattern (actual: {_prior_rev_str}), blended with run rate.</div>\n'
    else:
        html += f'<div class="opp-box"><strong>{"+" if period_growth>=0 else ""}{period_growth:.0f}% {periods[0]["label"]}→{periods[-1]["label"]} growth.</strong> Revenue went from {fc(monthly_rev[0])} to {fc(monthly_rev[-1])}. At this trajectory, annual forecast is <strong>{fc(conservative_forecast)}</strong>.</div>\n'
    html += '</div>\n'

    # ── Revenue Mix + Monthly (side by side) ──
    html += '<div class="grid-2">\n'
    html += '<div class="section"><h2><span class="dot"></span>Revenue Mix</h2><p class="desc">Grooming vs Retail vs Gift Cards</p><div class="chart-container"><canvas id="revMixChart"></canvas></div></div>\n'
    html += '<div class="section"><h2><span class="dot"></span>Monthly Revenue (Stacked)</h2><p class="desc">Grooming and retail by month</p><div class="chart-container"><canvas id="monthlyChart"></canvas></div></div>\n'
    html += '</div>\n'

    # ── Day-of-Week + Peak Hours ──
    html += '<div class="grid-2">\n'

    # Day-of-week heatmap
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Day-of-Week Revenue</h2>\n'
    html += '<p class="desc">Staffing optimization — where to allocate hours</p>\n'

    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_rev = {}
    dow_txns = {}
    for d in dow_order:
        d_items = df[df["dow"] == d]
        d_orders = df_orders[df_orders["dow"] == d]
        dow_rev[d] = d_items["net_sales"].sum()
        dow_txns[d] = d_orders.shape[0]

    max_dow_rev = max(dow_rev.values()) if dow_rev else 1

    html += '<div class="heatmap-grid">\n'
    for d in dow_order:
        intensity = (dow_rev[d] / max_dow_rev) if max_dow_rev and max_dow_rev > 0 else 0
        r = int(196 * intensity + 253 * (1 - intensity))
        g = int(39 * intensity + 240 * (1 - intensity))
        b = int(110 * intensity + 245 * (1 - intensity))
        text_color = "white" if intensity > 0.6 else "#333"
        html += f'<div class="heatmap-cell" style="background:rgb({r},{g},{b});color:{text_color}">'
        html += f'<div class="heatmap-label">{d[:3]}</div>{fc(dow_rev[d])}<br><span style="font-size:0.6rem">{fi(dow_txns[d])} txns</span></div>\n'
    html += '</div>\n'

    peak_day = max(dow_rev, key=dow_rev.get)
    slow_day = min(dow_rev, key=dow_rev.get)
    html += f'<div class="insight-box"><strong>{peak_day}</strong> is the peak day ({fc(dow_rev[peak_day])}). <strong>{slow_day}</strong> is slowest ({fc(dow_rev[slow_day])}). Consider promotions or reduced staffing on {slow_day}s.</div>\n'
    html += '</div>\n'

    # Peak hours
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Peak Hours</h2>\n'
    html += '<p class="desc">Transaction volume by hour — optimize scheduling</p>\n'
    html += '<div class="chart-container"><canvas id="hoursChart"></canvas></div>\n'
    html += '</div>\n'
    html += '</div>\n'

    # ── Service Type Mix ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Grooming Service Mix</h2>\n'
    html += '<p class="desc">Revenue and volume by service type</p>\n'
    html += '<div class="grid-2">\n<div>\n'

    groom_total_rev = groom["net_sales"].sum()
    svc_mix = groom.groupby("service_type").agg(
        units=("quantity", "sum"), revenue=("net_sales", "sum")
    ).reset_index().sort_values("revenue", ascending=False)

    html += '<table><thead><tr><th>Service Type</th><th>Units</th><th>Revenue</th><th>% Rev</th><th>Avg Ticket</th></tr></thead><tbody>\n'
    for _, row in svc_mix.head(15).iterrows():
        pct = row["revenue"] / groom_total_rev * 100 if groom_total_rev else 0
        avg = row["revenue"] / row["units"] if row["units"] else 0
        html += f'<tr><td>{esc(row["service_type"])}</td><td class="num">{fi(row["units"])}</td>'
        html += f'<td class="num">{fc(row["revenue"])}</td><td class="num">{fp(pct)}</td><td class="num">{fc(avg)}</td></tr>\n'
    html += '</tbody></table>\n</div>\n'
    html += '<div><div class="chart-container"><canvas id="svcMixChart"></canvas></div></div>\n'
    html += '</div></div>\n'

    # ── Retail Attachment Rate (NEW) ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Retail Attachment to Grooming</h2>\n'
    html += '<p class="desc">What percentage of grooming visits include a retail purchase?</p>\n'

    # Calculate per-order: does it have groom + retail?
    order_has_groom = set(groom["order_id"].unique())
    order_has_retail = set(retail["order_id"].unique())
    both = order_has_groom & order_has_retail
    groom_only_orders = order_has_groom - order_has_retail
    attach_rate = len(both) / len(order_has_groom) * 100 if order_has_groom else 0

    # Revenue from attached retail
    attached_retail_rev = retail[retail["order_id"].isin(both)]["net_sales"].sum()
    avg_attached_retail = attached_retail_rev / len(both) if both else 0

    # Opportunity: if we increase attach rate by 10 pts
    target_attach = attach_rate + 10
    additional_orders = len(order_has_groom) * 0.10
    attach_opportunity = additional_orders * avg_attached_retail

    html += '<div class="kpi-grid" style="margin-bottom:14px">\n'
    html += f'<div class="kpi-card amber"><div class="kpi-label">Current Attach Rate</div><div class="kpi-value">{fp(attach_rate)}</div><div class="kpi-sub">{fi(len(both))} of {fi(len(order_has_groom))} groom orders</div></div>\n'
    html += f'<div class="kpi-card"><div class="kpi-label">Groom-Only Orders</div><div class="kpi-value">{fi(len(groom_only_orders))}</div><div class="kpi-sub">{fp(len(groom_only_orders)/len(order_has_groom)*100 if order_has_groom else 0)} leave with zero retail</div></div>\n'
    html += f'<div class="kpi-card"><div class="kpi-label">Avg Retail on Attached</div><div class="kpi-value">{fc(avg_attached_retail)}</div><div class="kpi-sub">Per groom order with retail</div></div>\n'
    html += f'<div class="kpi-card green"><div class="kpi-label">+10pt Attach Opportunity</div><div class="kpi-value">{fc(attach_opportunity)}</div><div class="kpi-sub">{fi(int(additional_orders))} more orders × {fc(avg_attached_retail)} avg</div></div>\n'
    html += '</div>\n'

    html += f'<div class="alert-box"><strong>{fp(len(groom_only_orders)/len(order_has_groom)*100 if order_has_groom else 0)} of groom clients leave empty-handed.</strong> Every grooming checkout is a retail opportunity. A treat display at the grooming pickup counter, or a "Pick a treat for your pup on us" with $5 minimum purchase, could capture <strong>{fc(attach_opportunity)}</strong> in additional annual revenue.</div>\n'
    html += '</div>\n'

    # ── Dog Size + Doodle Premium ──
    core = df[(df["groom_category"] == "core") & (df["dog_size"].notna())]
    if not core.empty:
        html += '<div class="grid-2">\n'
        html += '<div class="section"><h2><span class="dot"></span>Dog Size Distribution</h2><p class="desc">Core groom appointments by weight class</p><div class="chart-container"><canvas id="dogSizeChart"></canvas></div></div>\n'

        html += '<div class="section"><h2><span class="dot"></span>Standard vs Doodle Premium</h2><p class="desc">Doodle breeds command a price premium</p>\n'
        html += '<table><thead><tr><th>Size</th><th>Standard Avg</th><th>Doodle Avg</th><th>Premium</th><th>Doodle %</th></tr></thead><tbody>\n'

        size_order = ["0-20 lbs", "21-40 lbs", "41-75 lbs", "76-100 lbs", "Over 100 lbs"]
        std = core[core["is_doodle"] == False]
        dood = core[core["is_doodle"] == True]

        for size in size_order:
            s_data = std[std["dog_size"] == size]
            d_data = dood[dood["dog_size"] == size]
            s_avg = s_data["price"].mean()
            d_avg = d_data["price"].mean()
            total_size = len(s_data) + len(d_data)
            doodle_pct = len(d_data) / total_size * 100 if total_size else 0
            if pd.notna(s_avg):
                prem = d_avg - s_avg if pd.notna(d_avg) else 0
                html += f'<tr><td>{size}</td><td class="num">{fc(s_avg)}</td>'
                html += f'<td class="num">{fc(d_avg) if pd.notna(d_avg) else "N/A"}</td>'
                html += f'<td class="num green">{"+" + fc(prem) if prem > 0 else "N/A"}</td>'
                html += f'<td class="num">{fp(doodle_pct)}</td></tr>\n'

        html += '</tbody></table></div>\n'
        html += '</div>\n'

    # ── Dog Size → Spend Correlation & Attachment Rates by Size ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Dog Size vs. Retail & Add-On Attachment</h2>\n'
    html += '<p class="desc">Do bigger dogs mean bigger baskets? Attachment rates by weight class</p>\n'

    size_order_att = ["0-20 lbs", "21-40 lbs", "41-75 lbs", "76-100 lbs", "Over 100 lbs"]
    # For each dog size, find core groom orders, then check retail/addon attach
    core_with_size = df[(df["groom_category"] == "core") & (df["dog_size"].notna())].copy()
    spa_df = df[df["groom_category"] == "spa"]
    addon_df = df[df["groom_category"] == "addon"]

    size_attach_rows = []
    for size in size_order_att:
        size_core = core_with_size[core_with_size["dog_size"] == size]
        size_orders = set(size_core["order_id"].unique())
        if not size_orders:
            continue
        n_orders = len(size_orders)
        # Retail attachment
        retail_attached = len(size_orders & set(retail["order_id"].unique()))
        retail_att_rate = retail_attached / n_orders * 100
        retail_rev_att = retail[retail["order_id"].isin(size_orders & set(retail["order_id"].unique()))]["net_sales"].sum()
        avg_retail_per_order = retail_rev_att / retail_attached if retail_attached else 0
        # Add-on attachment
        addon_attached = len(size_orders & set(addon_df["order_id"].unique()))
        addon_att_rate = addon_attached / n_orders * 100
        # SPA attachment
        spa_attached = len(size_orders & set(spa_df["order_id"].unique()))
        spa_att_rate = spa_attached / n_orders * 100
        # Avg total ticket for this size
        size_order_totals = df_orders[df_orders["order_id"].isin(size_orders)]
        avg_total_ticket = size_order_totals["subtotal"].mean()
        # Unique customers
        unique_custs = size_core["customer_id"].nunique()

        size_attach_rows.append({
            "size": size, "orders": n_orders, "unique_customers": unique_custs,
            "retail_att": retail_att_rate, "addon_att": addon_att_rate, "spa_att": spa_att_rate,
            "avg_retail_spend": avg_retail_per_order, "avg_total_ticket": avg_total_ticket,
        })

    html += '<table><thead><tr><th>Dog Size</th><th>Groom Orders</th><th>Unique Clients</th><th>Retail Attach %</th><th>Add-On Attach %</th><th>SPA Attach %</th><th>Avg Retail Spend</th><th>Avg Total Ticket</th></tr></thead><tbody>\n'
    for sr in size_attach_rows:
        # Color code the retail attach rate
        rat_cls = "green" if sr["retail_att"] > 20 else "red" if sr["retail_att"] < 10 else ""
        html += f'<tr><td>{esc(sr["size"])}</td><td class="num">{fi(sr["orders"])}</td><td class="num">{fi(sr["unique_customers"])}</td>'
        html += f'<td class="num {rat_cls}">{fp(sr["retail_att"])}</td>'
        html += f'<td class="num">{fp(sr["addon_att"])}</td><td class="num">{fp(sr["spa_att"])}</td>'
        html += f'<td class="num">{fc(sr["avg_retail_spend"])}</td><td class="num">{fc(sr["avg_total_ticket"])}</td></tr>\n'
    html += '</tbody></table>\n'

    # Insight: find correlation
    if len(size_attach_rows) >= 3:
        sizes_numeric = list(range(len(size_attach_rows)))
        retail_atts = [r["retail_att"] for r in size_attach_rows]
        total_tickets = [r["avg_total_ticket"] for r in size_attach_rows]
        # Simple directional check
        bigger_spend_more = total_tickets[-1] > total_tickets[0] if total_tickets else False
        bigger_attach_more = retail_atts[-1] > retail_atts[0] if retail_atts else False
        best_attach = max(size_attach_rows, key=lambda x: x["retail_att"])
        worst_attach = min(size_attach_rows, key=lambda x: x["retail_att"])

        html += f'<div class="insight-box"><strong>Insight:</strong> '
        if bigger_spend_more:
            html += f'Larger dogs correlate with higher total tickets ({fc(total_tickets[0])} for {size_attach_rows[0]["size"]} vs {fc(total_tickets[-1])} for {size_attach_rows[-1]["size"]}). '
        html += f'<strong>{best_attach["size"]}</strong> dogs have the highest retail attach rate at {fp(best_attach["retail_att"])}, '
        html += f'while <strong>{worst_attach["size"]}</strong> dogs are lowest at {fp(worst_attach["retail_att"])}. '
        html += f'Target retail upsell efforts on {worst_attach["size"]} dog parents at checkout.</div>\n'

    html += '</div>\n'

    # ── Rotational Customer Base ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Rotational Customer Base</h2>\n'
    html += '<p class="desc">How many unique grooming customers are actively returning? Split by groom vs. bath, with 10-week and full-year retention windows.</p>\n'

    data_end_ts = df["created"].max()
    bath_types = {"Luxury Bath", "Classic Bath"}

    # ── Helper: build retention stats for a customer segment ──
    def _retention_stats(segment_df, label):
        """Given line items for a segment (groom or bath), compute retention KPIs."""
        cust_ids = set(segment_df["customer_id"].unique())
        seg_orders = df_orders[df_orders["customer_id"].isin(cust_ids)]
        agg = seg_orders.groupby("customer_id").agg(
            visits=("created", lambda x: int(x.dt.date.nunique())),
            total_spend=("subtotal", "sum"),
            first_visit=("created", "min"),
            last_visit=("created", "max"),
        ).reset_index()
        agg["visits"] = agg["visits"].astype(int)
        agg["days_since"] = (data_end_ts - agg["last_visit"]).dt.days
        agg["tenure_months"] = ((agg["last_visit"] - agg["first_visit"]).dt.days / 30.44).round(1)

        total = len(agg)
        rot = agg[agg["visits"].astype(int) >= 2]
        oad = agg[agg["visits"].astype(int) == 1]

        # 10-week window (70 days): customers whose first visit was >=70 days ago who came back
        appeared_before_10w = agg[agg["first_visit"] <= (data_end_ts - pd.Timedelta(days=70))]
        ret_10w = appeared_before_10w[appeared_before_10w["visits"] >= 2]
        ret_10w_pct = len(ret_10w) / len(appeared_before_10w) * 100 if len(appeared_before_10w) else 0

        # Full-year window: customers whose first visit was >=365 days ago who came back
        appeared_before_1y = agg[agg["first_visit"] <= (data_end_ts - pd.Timedelta(days=365))]
        ret_1y = appeared_before_1y[appeared_before_1y["visits"] >= 2]
        ret_1y_pct = len(ret_1y) / len(appeared_before_1y) * 100 if len(appeared_before_1y) else 0

        overall_ret = len(rot) / total * 100 if total else 0
        return {
            "label": label, "total": total, "rotational": rot, "one_and_done": oad,
            "overall_ret": overall_ret,
            "ret_10w_pct": ret_10w_pct, "ret_10w_num": len(ret_10w), "ret_10w_base": len(appeared_before_10w),
            "ret_1y_pct": ret_1y_pct, "ret_1y_num": len(ret_1y), "ret_1y_base": len(appeared_before_1y),
            "agg": agg,
            "active_30": agg[(agg["visits"].astype(int) >= 2) & (agg["days_since"] <= 30)],
            "active_70": agg[(agg["visits"].astype(int) >= 2) & (agg["days_since"] <= 70)],
            "active_90": agg[(agg["visits"].astype(int) >= 2) & (agg["days_since"] <= 90)],
        }

    groom_items = df[(df["groom_category"] == "core") & (~df["service_type"].isin(bath_types))]
    bath_items = df[(df["groom_category"] == "core") & (df["service_type"].isin(bath_types))]
    gs = _retention_stats(groom_items, "Groom")
    bs = _retention_stats(bath_items, "Bath")

    # Combined totals for the top-level KPIs
    all_groom_custs = set(df[df["is_groom"]]["customer_id"].unique())
    cust_ord = df_orders[df_orders["customer_id"].isin(all_groom_custs)]
    cust_agg = cust_ord.groupby("customer_id").agg(
        visits=("created", lambda x: int(x.dt.date.nunique())),
        total_spend=("subtotal", "sum"),
        first_visit=("created", "min"),
        last_visit=("created", "max"),
    ).reset_index()
    cust_agg["days_since"] = (data_end_ts - cust_agg["last_visit"]).dt.days
    cust_agg["tenure_months"] = ((cust_agg["last_visit"] - cust_agg["first_visit"]).dt.days / 30.44).round(1)
    rotational = cust_agg[cust_agg["visits"].astype(int) >= 2]
    one_and_done = cust_agg[cust_agg["visits"].astype(int) == 1]

    html += '<div class="kpi-grid" style="margin-bottom:14px">\n'
    html += f'<div class="kpi-card green"><div class="kpi-label">Rotational Base (2+ visits)</div><div class="kpi-value">{fi(len(rotational))}</div><div class="kpi-sub">of {fi(len(cust_agg))} total customers</div></div>\n'
    html += f'<div class="kpi-card accent"><div class="kpi-label">Groom Retention (Overall)</div><div class="kpi-value">{fp(gs["overall_ret"])}</div><div class="kpi-sub">{fi(len(gs["rotational"]))} of {fi(gs["total"])}</div></div>\n'
    html += f'<div class="kpi-card"><div class="kpi-label">Bath Retention (Overall)</div><div class="kpi-value">{fp(bs["overall_ret"])}</div><div class="kpi-sub">{fi(len(bs["rotational"]))} of {fi(bs["total"])}</div></div>\n'
    html += f'<div class="kpi-card amber"><div class="kpi-label">One-and-Done</div><div class="kpi-value">{fi(len(one_and_done))}</div><div class="kpi-sub">{fp(len(one_and_done)/len(cust_agg)*100 if len(cust_agg) else 0)} never returned</div></div>\n'
    html += f'<div class="kpi-card brown"><div class="kpi-label">Avg Tenure (Rotational)</div><div class="kpi-value">{rotational["tenure_months"].mean():.1f} mo</div></div>\n'
    html += '</div>\n'

    # ── Formula callout ──
    html += f'<div style="background:{C["pink_bg"]};border-left:4px solid {C["magenta"]};padding:12px 16px;border-radius:6px;margin-bottom:18px;font-size:0.85rem;color:#444;line-height:1.7">\n'
    html += '<strong>Retention Formula:</strong> <code style="background:#fff;padding:2px 6px;border-radius:3px;font-size:0.82rem">Retention Rate = Customers with 2+ visits &divide; Total unique customers (within window)</code><br>\n'
    html += '<strong>10-Week Window:</strong> Only customers whose first visit was &ge;70 days before the data end date are eligible &mdash; gives them enough time to return.<br>\n'
    html += '<strong>Full-Year Window:</strong> Only customers whose first visit was &ge;365 days ago are eligible &mdash; true annual retention.<br>\n'
    html += '<strong>Overall:</strong> All customers in the dataset regardless of when they first appeared.\n'
    html += '</div>\n'

    # ── Groom vs Bath retention side-by-side ──
    def _retention_card(s):
        t = ''
        t += f'<h3 style="color:{C["brown"]};margin:14px 0 8px;font-size:1rem">{s["label"]} Customers</h3>\n'
        t += '<table><thead><tr><th>Window</th><th>Eligible</th><th>Retained</th><th>Rate</th></tr></thead><tbody>\n'
        t += f'<tr><td>10-Week</td><td class="num">{fi(s["ret_10w_base"])}</td><td class="num">{fi(s["ret_10w_num"])}</td><td class="num" style="font-weight:700">{fp(s["ret_10w_pct"])}</td></tr>\n'
        t += f'<tr><td>Full Year</td><td class="num">{fi(s["ret_1y_base"])}</td><td class="num">{fi(s["ret_1y_num"])}</td><td class="num" style="font-weight:700">{fp(s["ret_1y_pct"])}</td></tr>\n'
        t += f'<tr style="border-top:2px solid {C["teal"]}"><td>Overall</td><td class="num">{fi(s["total"])}</td><td class="num">{fi(len(s["rotational"]))}</td><td class="num" style="font-weight:700">{fp(s["overall_ret"])}</td></tr>\n'
        t += '</tbody></table>\n'
        t += f'<div style="font-size:0.82rem;color:#666;margin-top:8px">Active last 30d: <strong>{fi(len(s["active_30"]))}</strong> &bull; Last 70d: <strong>{fi(len(s["active_70"]))}</strong> &bull; Last 90d: <strong>{fi(len(s["active_90"]))}</strong></div>\n'
        return t

    html += '<div class="grid-2">\n'
    html += '<div>' + _retention_card(gs) + '</div>\n'
    html += '<div>' + _retention_card(bs) + '</div>\n'
    html += '</div>\n'

    # ── Rotational by Dog Size — split groom vs bath ──
    groom_with_size = df[(df["groom_category"] == "core") & (df["dog_size"].notna()) & (~df["service_type"].isin(bath_types))].copy()
    bath_with_size = df[(df["groom_category"] == "core") & (df["dog_size"].notna()) & (df["service_type"].isin(bath_types))].copy()

    cust_size_groom = groom_with_size.groupby("customer_id")["dog_size"].agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else "Unknown").reset_index()
    cust_size_groom.columns = ["customer_id", "primary_size"]
    rot_groom = gs["rotational"].merge(cust_size_groom, on="customer_id", how="inner")

    cust_size_bath = bath_with_size.groupby("customer_id")["dog_size"].agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else "Unknown").reset_index()
    cust_size_bath.columns = ["customer_id", "primary_size"]
    rot_bath = bs["rotational"].merge(cust_size_bath, on="customer_id", how="inner")

    def _size_table(rot_df, base_count, label):
        t = ''
        t += f'<h3 style="color:{C["brown"]};margin:14px 0 8px;font-size:1rem">{label}</h3>\n'
        t += '<table><thead><tr><th>Dog Size</th><th>Customers</th><th>% of Base</th><th>Avg Visits</th><th>Avg Spend</th><th>Avg Tenure (mo)</th></tr></thead><tbody>\n'
        for size in size_order_att + ["Unknown"]:
            s_custs = rot_df[rot_df["primary_size"] == size]
            if len(s_custs) == 0:
                continue
            pct = len(s_custs) / base_count * 100 if base_count else 0
            t += f'<tr><td>{esc(size)}</td><td class="num">{fi(len(s_custs))}</td>'
            t += f'<td class="num">{fp(pct)}</td><td class="num">{s_custs["visits"].mean():.1f}</td>'
            t += f'<td class="num">{fc(s_custs["total_spend"].mean())}</td>'
            t += f'<td class="num">{s_custs["tenure_months"].mean():.1f}</td></tr>\n'
        t += '</tbody></table>\n'
        return t

    html += '<div class="grid-2">\n'
    html += '<div>' + _size_table(rot_groom, len(rot_groom), "Rotational Groom Customers by Dog Size") + '</div>\n'
    html += '<div>' + _size_table(rot_bath, len(rot_bath), "Rotational Bath Customers by Dog Size") + '</div>\n'
    html += '</div>\n'

    overall_ret = len(rotational) / len(cust_agg) * 100 if len(cust_agg) else 0
    html += f'<div class="insight-box"><strong>{fp(overall_ret)} overall retention rate.</strong> Groom clients retain at <strong>{fp(gs["overall_ret"])}</strong> vs. bath clients at <strong>{fp(bs["overall_ret"])}</strong>. The rotational base of <strong>{fi(len(rotational))}</strong> customers is the recurring revenue engine. {fi(len(one_and_done))} customers ({fp(len(one_and_done)/len(cust_agg)*100 if len(cust_agg) else 0)}) came once and never returned — first-visit conversion opportunity.</div>\n'
    html += '</div>\n'

    # ── Groomer Productivity Scorecard ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Groomer Productivity Scorecard</h2>\n'
    html += '<p class="desc">Active groomers only (worked within last 60 days). Includes retention rate and performance grade.</p>\n'

    core_all = df[df["groom_category"] == "core"]
    spa = df[df["groom_category"] == "spa"]
    addon = df[df["groom_category"] == "addon"]
    active_cutoff = data_end_ts - pd.Timedelta(days=60)

    groomer_stats = []
    for name, grp in core_all.groupby("salesperson"):
        if not name:
            continue
        # Filter: only groomers with activity in last 60 days
        if grp["created"].max() < active_cutoff:
            continue

        appts = grp["quantity"].sum()
        rev = grp["net_sales"].sum()
        avg_t = grp["price"].mean()
        first_seen = grp["created"].min()
        last_seen = grp["created"].max()
        # Months active = calendar months from first to last appearance
        tenure_days = (last_seen - first_seen).days
        months_active = max(1, round(tenure_days / 30.44))
        appts_per_month = appts / months_active if months_active else 0

        # Upsell: what % of this groomer's core orders also have spa/addon
        groomer_orders = set(grp["order_id"].unique())
        spa_orders = set(spa[spa["order_id"].isin(groomer_orders)]["order_id"].unique())
        addon_orders = set(addon[addon["order_id"].isin(groomer_orders)]["order_id"].unique())
        upsold = groomer_orders & (spa_orders | addon_orders)
        upsell_rate = len(upsold) / len(groomer_orders) * 100 if groomer_orders else 0

        # Tips
        groomer_order_tips = df_orders[df_orders["order_id"].isin(groomer_orders)]["tips"].sum()
        tip_rate = groomer_order_tips / rev * 100 if rev else 0

        # Retention: of unique customers this groomer served, how many came back 2+ times
        groomer_custs = set(grp["customer_id"].unique())
        gc_agg = cust_agg[cust_agg["customer_id"].isin(groomer_custs)]
        gc_retained = gc_agg[gc_agg["visits"].astype(int) >= 2]
        retention = len(gc_retained) / len(gc_agg) * 100 if len(gc_agg) else 0

        # Performance grade (weighted composite: 30% volume, 25% upsell, 25% retention, 20% ticket)
        groomer_stats.append({
            "name": name, "appts": appts, "revenue": rev, "avg_ticket": avg_t,
            "first_seen": first_seen, "months_active": months_active,
            "appts_per_month": appts_per_month,
            "upsell_rate": upsell_rate, "tips": groomer_order_tips, "tip_rate": tip_rate,
            "retention": retention, "unique_custs": len(gc_agg), "retained_custs": len(gc_retained),
        })

    groomer_stats.sort(key=lambda x: x["revenue"], reverse=True)

    # Compute performance grades using percentile ranks within the active team
    if groomer_stats:
        vol_vals = [g["appts_per_month"] for g in groomer_stats]
        ups_vals = [g["upsell_rate"] for g in groomer_stats]
        ret_vals = [g["retention"] for g in groomer_stats]
        tkt_vals = [g["avg_ticket"] for g in groomer_stats]

        def _pctile(val, vals):
            """Percentile rank of val within vals (0-100)."""
            if len(vals) <= 1:
                return 50
            below = sum(1 for v in vals if v < val)
            return below / (len(vals) - 1) * 100

        for g in groomer_stats:
            score = (
                0.30 * _pctile(g["appts_per_month"], vol_vals) +
                0.25 * _pctile(g["upsell_rate"], ups_vals) +
                0.25 * _pctile(g["retention"], ret_vals) +
                0.20 * _pctile(g["avg_ticket"], tkt_vals)
            )
            g["perf_score"] = score
            if score >= 80: g["grade"], g["grade_cls"] = "A", "badge-green"
            elif score >= 60: g["grade"], g["grade_cls"] = "B", "badge-teal"
            elif score >= 40: g["grade"], g["grade_cls"] = "C", "badge-yellow"
            else: g["grade"], g["grade_cls"] = "D", "badge-red"

    html += '<table><thead><tr><th>Groomer</th><th>Grade</th><th>Started</th><th>Tenure</th><th>Appts</th><th>Revenue</th><th>Avg Ticket</th><th>Appts/Mo</th><th>Upsell</th><th>Retention</th><th>Tips</th></tr></thead><tbody>\n'
    for g in groomer_stats:
        if g["upsell_rate"] >= 20: upsell_cls = "green"
        elif g["upsell_rate"] >= 10: upsell_cls = ""
        else: upsell_cls = "red"
        if g["retention"] >= 60: ret_cls = "green"
        elif g["retention"] >= 40: ret_cls = ""
        else: ret_cls = "red"
        started = g["first_seen"].strftime("%b '%y")
        tenure = f'{g["months_active"]}mo' if g["months_active"] < 12 else f'{g["months_active"]//12}y {g["months_active"]%12}mo'
        html += f'<tr><td>{esc(g["name"])}</td>'
        html += f'<td><span class="badge {g["grade_cls"]}">{g["grade"]}</span></td>'
        html += f'<td>{started}</td><td>{tenure}</td>'
        html += f'<td class="num">{fi(g["appts"])}</td>'
        html += f'<td class="num">{fc(g["revenue"])}</td><td class="num">{fc(g["avg_ticket"])}</td>'
        html += f'<td class="num">{g["appts_per_month"]:.0f}</td>'
        html += f'<td class="num {upsell_cls}">{fp(g["upsell_rate"])}</td>'
        html += f'<td class="num {ret_cls}">{fp(g["retention"])}</td>'
        html += f'<td class="num">{fc(g["tips"])}</td></tr>\n'
    html += '</tbody></table>\n'

    html += f'<div style="font-size:0.82rem;color:#666;margin-top:8px;line-height:1.6">'
    html += f'<strong>Grade formula:</strong> 30% volume (appts/mo) + 25% upsell rate + 25% customer retention + 20% avg ticket — percentile-ranked against active peers.<br>'
    html += f'<strong>Retention:</strong> % of unique customers served by this groomer who returned 2+ times to the store.'
    html += f'</div>\n'

    avg_upsell = sum(g["upsell_rate"] for g in groomer_stats) / len(groomer_stats) if groomer_stats else 0
    top_upseller = max(groomer_stats, key=lambda x: x["upsell_rate"]) if groomer_stats else None
    low_upseller = min(groomer_stats, key=lambda x: x["upsell_rate"]) if groomer_stats else None
    if top_upseller and low_upseller:
        html += f'<div class="opp-box"><strong>Coaching opportunity:</strong> {esc(top_upseller["name"])} leads upselling at {fp(top_upseller["upsell_rate"])} while {esc(low_upseller["name"])} is at {fp(low_upseller["upsell_rate"])}. Closing this gap across all groomers would significantly boost ticket values.</div>\n'
    html += '</div>\n'

    # ── Revenue Concentration (Pareto) ──
    html += '<div class="grid-2">\n'
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Revenue Concentration</h2>\n'
    html += '<p class="desc">Pareto analysis — customer dependency risk</p>\n'

    cust_spend = df.groupby("customer_id")["net_sales"].sum().sort_values(ascending=False)
    total_cust_rev = cust_spend.sum()
    n_customers = len(cust_spend)
    top_10_n = max(1, n_customers // 10)
    top_20_n = max(1, n_customers // 5)
    top_10_rev = cust_spend.head(top_10_n).sum()
    top_20_rev = cust_spend.head(top_20_n).sum()
    top_10_pct = top_10_rev / total_cust_rev * 100
    top_20_pct = top_20_rev / total_cust_rev * 100

    html += f'<div style="margin-bottom:12px">\n'
    html += f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:3px"><span>Top 10% ({fi(top_10_n)} customers)</span><span><strong>{fp(top_10_pct)}</strong> of revenue</span></div>\n'
    html += f'<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{top_10_pct}%;background:{C["magenta"]}"></div></div>\n'
    html += f'</div>\n'
    html += f'<div style="margin-bottom:12px">\n'
    html += f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:3px"><span>Top 20% ({fi(top_20_n)} customers)</span><span><strong>{fp(top_20_pct)}</strong> of revenue</span></div>\n'
    html += f'<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{top_20_pct}%;background:{C["teal"]}"></div></div>\n'
    html += f'</div>\n'
    html += f'<div style="margin-bottom:12px">\n'
    html += f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:3px"><span>Bottom 80% ({fi(n_customers - top_20_n)} customers)</span><span><strong>{fp(100 - top_20_pct)}</strong> of revenue</span></div>\n'
    html += f'<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{100-top_20_pct}%;background:{C["brown"]}"></div></div>\n'
    html += f'</div>\n'

    if top_10_pct > 40:
        html += f'<div class="alert-box"><strong>Concentration risk:</strong> Your top 10% of customers drive {fp(top_10_pct)} of revenue. Losing even a few high-value clients would materially impact the business. Prioritize retention programs for VIP customers.</div>\n'
    html += '</div>\n'

    # ── Customer Segments ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Customer Intelligence</h2>\n'
    html += '<p class="desc">Visit frequency and lifetime value distribution</p>\n'

    groom_custs = set(df[df["is_groom"]]["customer_id"].unique())
    cust_orders_df = df_orders[df_orders["customer_id"].isin(groom_custs)]
    cust_visits = cust_orders_df.groupby("customer_id").agg(
        visits=("created", lambda x: int(x.dt.date.nunique())),
        total_spend=("subtotal", "sum"),
        last_visit=("created", "max"),
    ).reset_index()

    def segment(v):
        if v == 1: return "1 visit"
        elif v == 2: return "2 visits"
        elif v <= 4: return "3-4 visits"
        elif v <= 8: return "5-8 visits"
        elif v <= 12: return "9-12 visits"
        return "13+ visits"

    cust_visits["segment"] = cust_visits["visits"].apply(segment)
    seg_order = ["1 visit", "2 visits", "3-4 visits", "5-8 visits", "9-12 visits", "13+ visits"]
    seg_summary = cust_visits.groupby("segment").agg(
        customers=("customer_id", "count"), total_spend=("total_spend", "sum"), avg_spend=("total_spend", "mean")
    ).reindex(seg_order).reset_index()
    total_custs_seg = seg_summary["customers"].sum()

    html += '<div class="grid-2"><div>\n'
    html += '<table><thead><tr><th>Segment</th><th>Customers</th><th>%</th><th>Revenue</th><th>Avg Spend</th></tr></thead><tbody>\n'
    for _, row in seg_summary.iterrows():
        pct = row["customers"] / total_custs_seg * 100 if total_custs_seg else 0
        html += f'<tr><td>{esc(row["segment"])}</td><td class="num">{fi(row["customers"])}</td>'
        html += f'<td class="num">{fp(pct)}</td><td class="num">{fc(row["total_spend"])}</td>'
        html += f'<td class="num">{fc(row["avg_spend"])}</td></tr>\n'
    html += '</tbody></table></div>\n'
    html += '<div><div class="chart-container"><canvas id="custSegChart"></canvas></div></div></div>\n'

    # At-risk customers (last visit >90 days ago from end of data)
    data_end = df["created"].max()
    cust_visits["days_since"] = (data_end - cust_visits["last_visit"]).dt.days
    at_risk = cust_visits[(cust_visits["visits"].astype(int) >= 3) & (cust_visits["days_since"] > 90)]
    at_risk_rev = at_risk["total_spend"].sum()
    html += f'<div class="alert-box"><strong>{fi(len(at_risk))} loyal customers (3+ visits) haven\'t returned in 90+ days.</strong> They represent {fc(at_risk_rev)} in historical spend. A re-engagement campaign (text/email with a small incentive) could recapture this segment.</div>\n'
    html += '</div>\n'
    html += '</div>\n'  # end grid-2

    # ── Monthly Performance Table ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Monthly Performance Detail</h2>\n'
    html += '<p class="desc">Revenue, margins, transactions, and tips by month</p>\n'
    html += '<table><thead><tr><th>Month</th><th>Net Revenue</th><th>Grooming</th><th>Commission (50%)</th><th>Retail</th><th>Gross Margin</th><th>Txns</th><th>Avg Ticket</th><th>Tips</th><th>MoM</th></tr></thead><tbody>\n'

    prev_rev = None
    for p in periods:
        pm = p["period"]
        m_df = df[df["ym"] == pm]
        m_groom = groom[groom["ym"] == pm]
        m_retail = retail[retail["ym"] == pm]
        m_orders = df_orders[df_orders["ym"] == pm]
        g_r = m_groom["net_sales"].sum()
        r_r = m_retail["net_sales"].sum()
        t_r = g_r + r_r
        comm_m = g_r * 0.50
        cogs_m = g_r * FDD_GROOM_COGS_PCT / 100 + r_r * FDD_RETAIL_COGS_PCT / 100
        gm = (t_r - cogs_m) / t_r * 100 if t_r else 0
        tix = m_orders.shape[0]
        avg_t = t_r / tix if tix else 0
        tips_m = m_orders["tips"].sum()
        g_pct = g_r / t_r * 100 if t_r else 0
        mom = ((t_r - prev_rev) / prev_rev * 100) if prev_rev and prev_rev > 0 else None
        mn = p["label"]
        mom_str = f'<td class="num {"green" if mom and mom > 0 else "red" if mom and mom < 0 else ""}">{("+" if mom and mom > 0 else "") + fp(mom) if mom is not None else "—"}</td>'
        html += f'<tr><td>{mn}</td><td class="num">{fc(t_r)}</td><td class="num">{fc(g_r)}</td><td class="num">{fc(comm_m)}</td><td class="num">{fc(r_r)}</td><td class="num">{fp(gm)}</td><td class="num">{fi(tix)}</td><td class="num">{fc(avg_t)}</td><td class="num">{fc(tips_m)}</td>{mom_str}</tr>\n'
        prev_rev = t_r
    html += '</tbody></table></div>\n'

    # ── Top 20 Retail + Category Summary ──
    html += '<div class="grid-2">\n'

    html += '<div class="section"><h2><span class="dot"></span>Top 20 Retail Products</h2><p class="desc">Ranked by net revenue</p>\n'
    html += '<table><thead><tr><th>#</th><th>Product</th><th>Category</th><th>Units</th><th>Revenue</th><th>Avg Price</th></tr></thead><tbody>\n'

    retail_prod = retail.groupby(["name", "retail_category"]).agg(
        units=("quantity", "sum"), revenue=("net_sales", "sum")
    ).reset_index().sort_values("revenue", ascending=False).head(20)

    for i, (_, row) in enumerate(retail_prod.iterrows(), 1):
        avg_p = row["revenue"] / row["units"] if row["units"] else 0
        html += f'<tr><td>{i}</td><td>{esc(row["name"][:50])}</td><td>{esc(row["retail_category"])}</td>'
        html += f'<td class="num">{fi(row["units"])}</td><td class="num">{fc(row["revenue"])}</td>'
        html += f'<td class="num">{fc(avg_p)}</td></tr>\n'
    html += '</tbody></table></div>\n'

    html += '<div class="section"><h2><span class="dot"></span>Retail Categories</h2><p class="desc">Revenue and margin by category</p><div class="chart-container"><canvas id="catChart"></canvas></div></div>\n'

    html += '</div>\n'

    # ── Retail Revenue vs. Stock Levels ──
    stock_path = DATA_DIR / "retail_stock.json"
    if stock_path.exists():
        import json as _json
        stock_data = _json.loads(stock_path.read_text())
        has_stock = [s for s in stock_data if s["stock"] is not None]

        html += '<div class="section">\n'
        html += '<h2><span class="dot"></span>Retail Revenue vs. Current Stock Levels</h2>\n'
        html += '<p class="desc">Top-selling retail SKUs with live FranPOS stock counts. Negative stock = POS tracking not maintained.</p>\n'

        # Split into categories
        in_stock = [s for s in has_stock if s["stock"] > 0]
        out_of_stock = [s for s in has_stock if s["stock"] == 0]
        negative_stock = [s for s in has_stock if s["stock"] < 0]

        html += '<div class="kpi-grid" style="margin-bottom:14px">\n'
        html += f'<div class="kpi-card green"><div class="kpi-label">In Stock</div><div class="kpi-value">{fi(len(in_stock))}</div><div class="kpi-sub">of {fi(len(has_stock))} top SKUs</div></div>\n'
        html += f'<div class="kpi-card red"><div class="kpi-label">Out of Stock</div><div class="kpi-value">{fi(len(out_of_stock))}</div><div class="kpi-sub">Lost revenue risk</div></div>\n'
        html += f'<div class="kpi-card amber"><div class="kpi-label">Negative Stock (Untracked)</div><div class="kpi-value">{fi(len(negative_stock))}</div><div class="kpi-sub">POS inventory not maintained</div></div>\n'
        tracked_rev = sum(s["revenue"] for s in in_stock)
        untracked_rev = sum(s["revenue"] for s in negative_stock)
        html += f'<div class="kpi-card"><div class="kpi-label">Revenue at Risk (OOS)</div><div class="kpi-value">{fc(sum(s["revenue"] for s in out_of_stock))}</div></div>\n'
        html += '</div>\n'

        # Table: Top sellers with stock status
        html += '<table><thead><tr><th>#</th><th>Product</th><th>Units Sold</th><th>Revenue</th><th>Current Stock</th><th>Status</th></tr></thead><tbody>\n'
        for i, s in enumerate(has_stock[:30], 1):
            stk = s["stock"]
            if stk > 5:
                badge, cls = "Stocked", "badge-green"
            elif stk > 0:
                badge, cls = "Low", "badge-yellow"
            elif stk == 0:
                badge, cls = "Out", "badge-red"
            else:
                badge, cls = "Untracked", "badge-red"
            stk_display = f'{stk:.0f}' if stk == int(stk) else f'{stk:.1f}'
            html += f'<tr><td>{i}</td><td>{esc(s["name"][:50])}</td>'
            html += f'<td class="num">{s["units_sold"]:.0f}</td><td class="num">{fc(s["revenue"])}</td>'
            html += f'<td class="num">{stk_display}</td>'
            html += f'<td><span class="badge {cls}">{badge}</span></td></tr>\n'
        html += '</tbody></table>\n'

        # Correlation insight
        properly_tracked = [s for s in has_stock if s["stock"] >= 0]
        if properly_tracked:
            avg_rev_stocked = sum(s["revenue"] for s in in_stock) / len(in_stock) if in_stock else 0
            avg_rev_oos = sum(s["revenue"] for s in out_of_stock) / len(out_of_stock) if out_of_stock else 0
            html += f'<div class="insight-box">'
            html += f'<strong>Stock Tracking Gap:</strong> {fi(len(negative_stock))} of the top {fi(len(has_stock))} retail SKUs ({fp(len(negative_stock)/len(has_stock)*100)}) show negative inventory — meaning stock is not being tracked in the POS for these items. '
            html += f'These untracked SKUs generated <strong>{fc(untracked_rev)}</strong> in revenue. '
            if out_of_stock:
                html += f'{fi(len(out_of_stock))} SKUs show zero stock, representing <strong>{fc(sum(s["revenue"] for s in out_of_stock))}</strong> in sales now at risk of stockouts. '
            html += f'<strong>Recommendation:</strong> Conduct a physical inventory count and reset POS stock levels — accurate inventory data enables reorder alerts and prevents lost sales.</div>\n'
        html += '</div>\n'

    # ══════════════════════════════════════════════════════════════════════════
    # ── CANCELLATIONS, NO-SHOWS & RETURNS — MULTI-VIEW ANALYSIS ──
    # ══════════════════════════════════════════════════════════════════════════
    refunds = df[df["quantity"] < 0].copy()
    if not refunds.empty:
        refunds["refund_value"] = (refunds["price"] * refunds["quantity"]).abs()
        total_refund_val = refunds["refund_value"].sum()
        total_refund_items = len(refunds)
        refund_order_ids = refunds["order_id"].nunique()
        total_orders_count = df_orders.shape[0]
        refund_rate = refund_order_ids / total_orders_count * 100 if total_orders_count else 0
        positive_rev = df[df["quantity"] > 0]["net_sales"].sum()
        refund_pct_rev = total_refund_val / positive_rev * 100 if positive_rev else 0

        # Classify refunds as groom vs retail
        groom_refunds = refunds[refunds["is_groom"] == True]
        retail_refunds = refunds[refunds["is_groom"] == False]
        groom_refund_val = groom_refunds["refund_value"].sum() if not groom_refunds.empty else 0
        retail_refund_val = retail_refunds["refund_value"].sum() if not retail_refunds.empty else 0
        avg_refund = total_refund_val / total_refund_items if total_refund_items else 0

        # ── Grooming cancellation / no-show detection ──
        groom_items_all = df[df["is_groom"] == True]
        groom_positive = groom_items_all[groom_items_all["quantity"] > 0]
        total_groom_appts = groom_positive["quantity"].sum()
        groom_order_ids_set = set(groom_items_all["order_id"].unique())
        groom_orders_df = df_orders[df_orders["order_id"].isin(groom_order_ids_set)]
        total_groom_orders = len(groom_orders_df)

        # Type 1: Groom service voids (negative-qty groom line items)
        void_order_ids = set(groom_refunds["order_id"].unique())
        # Type 2: Zero-total orders containing groom items (no-shows / walkaways)
        zero_groom_orders = groom_orders_df[groom_orders_df["total"] == 0]
        zero_order_ids = set(zero_groom_orders["order_id"].unique())
        # Deduplicated cancellation events
        all_cancel_order_ids = void_order_ids | zero_order_ids
        total_cancel_events = len(all_cancel_order_ids)
        cancel_rate = total_cancel_events / total_groom_orders * 100 if total_groom_orders else 0

        # Revenue lost to cancellations
        cancel_rev_voided = groom_refund_val
        zero_order_groom_rev = 0.0
        for zoid in zero_order_ids:
            z_items = groom_items_all[(groom_items_all["order_id"] == zoid) & (groom_items_all["quantity"] > 0)]
            zero_order_groom_rev += z_items["net_sales"].sum()
        total_cancel_rev = cancel_rev_voided + zero_order_groom_rev

        # Monthly cancellation trend (uses periods)
        cancel_monthly = {}
        for p in periods:
            pm = p["period"]
            void_ids_m = set(groom_refunds[groom_refunds["ym"] == pm]["order_id"].unique()) if "ym" in groom_refunds.columns else set()
            zero_ids_m = set(zero_groom_orders[zero_groom_orders["ym"] == pm]["order_id"].unique()) if "ym" in zero_groom_orders.columns else set()
            groom_ord_m = len(groom_orders_df[groom_orders_df["ym"] == pm]) if "ym" in groom_orders_df.columns else 0
            cancel_monthly[p["label"]] = {
                "voids": len(void_ids_m),
                "zeros": len(zero_ids_m),
                "total": len(void_ids_m | zero_ids_m),
                "groom_orders": groom_ord_m,
            }
            gt = cancel_monthly[p["label"]]["groom_orders"]
            cancel_monthly[p["label"]]["rate"] = cancel_monthly[p["label"]]["total"] / gt * 100 if gt else 0

        # Teeth Brushing add-on analysis
        teeth_voids = groom_refunds[groom_refunds["name"].str.contains("Teeth Brushing", case=False, na=False)]
        non_teeth_groom_refunds = groom_refunds[~groom_refunds["name"].str.contains("Teeth Brushing", case=False, na=False)]
        teeth_void_count = len(teeth_voids)
        core_cancel_count = len(non_teeth_groom_refunds["order_id"].unique())

        # Day-of-week analysis
        refunds["dow"] = refunds["created"].dt.day_name()
        dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dow_refunds = refunds.groupby("dow").agg(count=("refund_value", "size"), value=("refund_value", "sum"))
        dow_total_orders = df_orders.groupby("dow").size()

        # First-half vs second-half comparison (splits data periods in half)
        mid = n_periods // 2 if n_periods > 1 else 1
        first_half_periods = set(p["period"] for p in periods[:mid])
        second_half_periods = set(p["period"] for p in periods[mid:])
        h1_refunds = refunds[refunds["ym"].isin(first_half_periods)]
        h2_refunds = refunds[refunds["ym"].isin(second_half_periods)]
        h1_val = h1_refunds["refund_value"].sum()
        h2_val = h2_refunds["refund_value"].sum()
        h1_cnt = len(h1_refunds)
        h2_cnt = len(h2_refunds)
        h1_label = f"First {mid}mo"
        h2_label = f"Last {n_periods - mid}mo"

        # ═══════════════════════════════════════════════════════════
        # BEGIN HTML OUTPUT
        # ═══════════════════════════════════════════════════════════
        html += '<div class="section">\n'
        html += '<h2><span class="dot"></span>Cancellations, No-Shows & Returns</h2>\n'
        html += '<p class="desc">Multi-view analysis of all refund activity, grooming cancellations, no-shows, and retail returns. '
        html += 'Data derived from POS transaction voids, negative-quantity line items, and zero-total orders.</p>\n'

        # Methodology callout
        html += f'<div style="background:{C["pink_bg"]};border-left:4px solid {C["magenta"]};padding:12px 16px;border-radius:6px;margin-bottom:18px;font-size:0.85rem;color:#444;line-height:1.7">\n'
        html += '<strong>How We Identify Cancellations:</strong><br>\n'
        html += '<code style="background:#fff;padding:2px 6px;border-radius:3px;font-size:0.82rem">Service Voids</code> &mdash; Groom line items with negative quantity = service was rung up then refunded (cancelled, redo, or pricing adjustment).<br>\n'
        html += '<code style="background:#fff;padding:2px 6px;border-radius:3px;font-size:0.82rem">Zero-Total Groom Orders</code> &mdash; Orders containing groom services that closed at $0 = likely no-shows or walk-aways.<br>\n'
        html += '<code style="background:#fff;padding:2px 6px;border-radius:3px;font-size:0.82rem">Retail Returns</code> &mdash; Product return line items with return reason &amp; disposition recorded in POS.<br>\n'
        html += '<strong>Note:</strong> FranPOS booking API does not expose historical cancellation logs. This analysis uses POS transaction data as the source of truth.\n'
        html += '</div>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 1: HEADLINE KPIs
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:18px 0 10px">Overview</h3>\n'
        html += '<div class="kpi-grid" style="margin-bottom:14px">\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Total Refund Value</div><div class="kpi-value">{fc(total_refund_val)}</div><div class="kpi-sub">{fi(total_refund_items)} line items across {fi(refund_order_ids)} orders</div></div>\n'
        html += f'<div class="kpi-card green"><div class="kpi-label">Refund % of Revenue</div><div class="kpi-value">{fp(refund_pct_rev)}</div><div class="kpi-sub">Industry avg: 1&ndash;3%</div></div>\n'
        html += f'<div class="kpi-card green"><div class="kpi-label">Groom Cancel Rate</div><div class="kpi-value">{fp(cancel_rate)}</div><div class="kpi-sub">{fi(total_cancel_events)} of {fi(total_groom_orders)} groom orders</div></div>\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Revenue Lost to Cancels</div><div class="kpi-value">{fc(total_cancel_rev)}</div><div class="kpi-sub">Voids + no-show orders</div></div>\n'
        html += '</div>\n'

        # Second KPI row
        html += '<div class="kpi-grid" style="margin-bottom:18px">\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Order Refund Rate</div><div class="kpi-value">{fp(refund_rate)}</div><div class="kpi-sub">{fi(refund_order_ids)} of {fi(total_orders_count)} total orders</div></div>\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Avg Refund Value</div><div class="kpi-value">{fc(avg_refund)}</div><div class="kpi-sub">Per line item</div></div>\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Grooming Voided</div><div class="kpi-value">{fc(groom_refund_val)}</div><div class="kpi-sub">{fi(len(groom_refunds))} service line items</div></div>\n'
        html += f'<div class="kpi-card"><div class="kpi-label">Retail Returned</div><div class="kpi-value">{fc(retail_refund_val)}</div><div class="kpi-sub">{fi(len(retail_refunds))} product line items</div></div>\n'
        html += '</div>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 2: GROOMING CANCELLATION DEEP-DIVE
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Grooming Cancellations & No-Shows</h3>\n'
        html += '<p class="desc">Isolating grooming-specific cancellation events — the most operationally impactful category.</p>\n'

        # Cancellation type breakdown
        html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:16px">\n'
        html += f'<div style="background:#fff;border-radius:10px;padding:16px;border-left:4px solid {C["red"]}">'
        html += f'<div style="font-size:0.8rem;color:#888">Service Voids</div>'
        html += f'<div style="font-size:1.6rem;font-weight:700;color:{C["brown"]}">{fi(len(void_order_ids))}</div>'
        html += f'<div style="font-size:0.78rem;color:#666">{fi(len(groom_refunds))} line items &bull; {fc(cancel_rev_voided)}</div></div>\n'
        html += f'<div style="background:#fff;border-radius:10px;padding:16px;border-left:4px solid {C["amber"]}">'
        html += f'<div style="font-size:0.8rem;color:#888">Zero-Total / No-Shows</div>'
        html += f'<div style="font-size:1.6rem;font-weight:700;color:{C["brown"]}">{fi(len(zero_order_ids))}</div>'
        html += f'<div style="font-size:0.78rem;color:#666">{fc(zero_order_groom_rev)} in unpaid services</div></div>\n'
        html += f'<div style="background:#fff;border-radius:10px;padding:16px;border-left:4px solid {C["green"]}">'
        html += f'<div style="font-size:0.8rem;color:#888">Industry Benchmark</div>'
        html += f'<div style="font-size:1.6rem;font-weight:700;color:{C["green"]}">5&ndash;15%</div>'
        html += f'<div style="font-size:0.78rem;color:#666">Typical grooming cancel rate</div></div>\n'
        html += '</div>\n'

        # Add-on vs Core cancellation distinction
        html += f'<div style="background:{C["pink_bg"]};border-left:4px solid {C["teal"]};padding:12px 16px;border-radius:6px;margin-bottom:16px;font-size:0.85rem;color:#444;line-height:1.7">\n'
        html += f'<strong>Add-On vs. Core Service Voids:</strong> Of the {fi(len(groom_refunds))} grooming void line items, '
        html += f'<strong>{fi(teeth_void_count)}</strong> are Teeth Brushing add-on removals ($10 each) &mdash; these are typically customers declining at checkout, not true cancellations. '
        html += f'Excluding add-on removals, <strong>{fi(core_cancel_count)} orders</strong> had a core groom service voided.</div>\n'

        # Monthly cancellation trend
        html += '<h4 style="margin:16px 0 8px">Monthly Cancellation Trend</h4>\n'
        html += '<div class="chart-wrap"><canvas id="cancelTrendChart"></canvas></div>\n'
        html += '<table><thead><tr><th>Month</th><th>Groom Orders</th><th>Service Voids</th><th>No-Shows</th><th>Total Cancels</th><th>Cancel Rate</th></tr></thead><tbody>\n'
        for plabel, cm in cancel_monthly.items():
            rate_cls = ' style="color:#C62828;font-weight:700"' if cm["rate"] > 3.0 else (' style="color:#2E7D32;font-weight:700"' if cm["rate"] < 1.0 else '')
            html += f'<tr><td>{plabel}</td><td class="num">{fi(cm["groom_orders"])}</td>'
            html += f'<td class="num">{fi(cm["voids"])}</td><td class="num">{fi(cm["zeros"])}</td>'
            html += f'<td class="num">{fi(cm["total"])}</td><td class="num"{rate_cls}>{fp(cm["rate"])}</td></tr>\n'
        # Totals row
        t_voids = sum(cm["voids"] for cm in cancel_monthly.values())
        t_zeros = sum(cm["zeros"] for cm in cancel_monthly.values())
        html += f'<tr style="border-top:2px solid {C["teal"]};font-weight:700"><td>Total</td><td class="num">{fi(total_groom_orders)}</td>'
        html += f'<td class="num">{fi(t_voids)}</td><td class="num">{fi(t_zeros)}</td>'
        html += f'<td class="num">{fi(total_cancel_events)}</td><td class="num">{fp(cancel_rate)}</td></tr>\n'
        html += '</tbody></table>\n'

        # Cancelled services detail side-by-side
        svc_cancel = groom_refunds.groupby("name").agg(
            count=("refund_value", "size"), value=("refund_value", "sum")
        ).sort_values("value", ascending=False)

        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0">\n'
        html += '<div><h4>Services Voided</h4>\n'
        html += '<table><thead><tr><th>Service</th><th>Voids</th><th>Value Lost</th></tr></thead><tbody>\n'
        for svc, row in svc_cancel.iterrows():
            html += f'<tr><td>{esc(str(svc))}</td><td class="num">{fi(int(row["count"]))}</td><td class="num">{fc(row["value"])}</td></tr>\n'
        html += '</tbody></table></div>\n'

        # Zero-total groom order detail
        html += '<div><h4>No-Show / Zero-Total Orders</h4>\n'
        html += '<table><thead><tr><th>Date</th><th>Service</th><th>Value</th><th>Employee</th></tr></thead><tbody>\n'
        for zoid in sorted(zero_order_ids):
            z_items = groom_items_all[(groom_items_all["order_id"] == zoid) & (groom_items_all["quantity"] > 0)]
            for _, zi in z_items.iterrows():
                dstr = zi["created"].strftime("%b %d") if pd.notna(zi["created"]) else ""
                html += f'<tr><td>{dstr}</td><td>{esc(str(zi["name"]))}</td><td class="num">{fc(zi["net_sales"])}</td><td>{esc(str(zi["salesperson"]))}</td></tr>\n'
        html += '</tbody></table></div>\n'
        html += '</div>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 3: RETAIL RETURNS ANALYSIS
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Retail Product Returns</h3>\n'

        ret_reasons = retail_refunds.groupby(retail_refunds["return_reason"].fillna("Not Specified")).agg(
            count=("refund_value", "size"), value=("refund_value", "sum")
        ).sort_values("count", ascending=False) if not retail_refunds.empty else pd.DataFrame()

        ret_disps = retail_refunds.groupby(retail_refunds["return_disposition"].fillna("Not Specified")).agg(
            count=("refund_value", "size"), value=("refund_value", "sum")
        ).sort_values("count", ascending=False) if not retail_refunds.empty else pd.DataFrame()

        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">\n'

        html += '<div><h4>Return Reasons</h4>\n'
        html += '<table><thead><tr><th>Reason</th><th>Count</th><th>Value</th><th>%</th></tr></thead><tbody>\n'
        for reason, row in ret_reasons.iterrows():
            pct = row["count"] / len(retail_refunds) * 100 if len(retail_refunds) else 0
            html += f'<tr><td>{esc(str(reason))}</td><td class="num">{fi(int(row["count"]))}</td>'
            html += f'<td class="num">{fc(row["value"])}</td><td class="num">{fp(pct)}</td></tr>\n'
        html += '</tbody></table></div>\n'

        html += '<div><h4>Return Dispositions</h4>\n'
        html += '<table><thead><tr><th>Disposition</th><th>Count</th><th>Value</th><th>%</th></tr></thead><tbody>\n'
        for disp, row in ret_disps.iterrows():
            pct = row["count"] / len(retail_refunds) * 100 if len(retail_refunds) else 0
            html += f'<tr><td>{esc(str(disp))}</td><td class="num">{fi(int(row["count"]))}</td>'
            html += f'<td class="num">{fc(row["value"])}</td><td class="num">{fp(pct)}</td></tr>\n'
        html += '</tbody></table></div>\n'
        html += '</div>\n'

        # Top Returned Products
        top_ref = refunds.groupby("name").agg(
            count=("refund_value", "size"), value=("refund_value", "sum"), is_groom=("is_groom", "first")
        ).sort_values("value", ascending=False).head(15)

        html += '<h4>Top Returned Products (by Value)</h4>\n'
        html += '<table><thead><tr><th>#</th><th>Product</th><th>Type</th><th>Returns</th><th>Value</th></tr></thead><tbody>\n'
        for i, (prod, row) in enumerate(top_ref.iterrows(), 1):
            ptype = "Groom" if row["is_groom"] else "Retail"
            badge = "badge-yellow" if row["is_groom"] else "badge-green"
            html += f'<tr><td>{i}</td><td>{esc(str(prod)[:55])}</td><td><span class="badge {badge}">{ptype}</span></td>'
            html += f'<td class="num">{fi(int(row["count"]))}</td><td class="num">{fc(row["value"])}</td></tr>\n'
        html += '</tbody></table>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 4: MONTHLY TREND — ALL REFUNDS
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Monthly Refund Trend</h3>\n'
        monthly_ref = refunds.groupby("ym").agg(
            count=("refund_value", "size"), value=("refund_value", "sum")
        )
        monthly_pos_rev = df[df["quantity"] > 0].groupby("ym")["net_sales"].sum()

        html += '<div class="chart-wrap"><canvas id="refundTrendChart"></canvas></div>\n'
        html += '<table><thead><tr><th>Month</th><th>Items</th><th>Refund Value</th><th>Revenue</th><th>Refund % Rev</th><th>Avg/Item</th></tr></thead><tbody>\n'
        for p in periods:
            pm = p["period"]
            cnt = int(monthly_ref.loc[pm, "count"]) if pm in monthly_ref.index else 0
            val = float(monthly_ref.loc[pm, "value"]) if pm in monthly_ref.index else 0.0
            rev_m = float(monthly_pos_rev.get(pm, 0))
            pct_m = val / rev_m * 100 if rev_m else 0
            avg_v = val / cnt if cnt else 0
            pct_cls = ' style="color:#C62828;font-weight:700"' if pct_m > 1.5 else ''
            html += f'<tr><td>{p["label"]}</td><td class="num">{fi(cnt)}</td>'
            html += f'<td class="num">{fc(val)}</td><td class="num">{fc(rev_m)}</td>'
            html += f'<td class="num"{pct_cls}>{fp(pct_m)}</td><td class="num">{fc(avg_v)}</td></tr>\n'
        html += f'<tr style="border-top:2px solid {C["teal"]};font-weight:700"><td>Total</td><td class="num">{fi(total_refund_items)}</td>'
        html += f'<td class="num">{fc(total_refund_val)}</td><td class="num">{fc(positive_rev)}</td>'
        html += f'<td class="num">{fp(refund_pct_rev)}</td><td class="num">{fc(avg_refund)}</td></tr>\n'
        html += '</tbody></table>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 5: H1 vs H2 & DAY-OF-WEEK
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Seasonality & Patterns</h3>\n'
        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">\n'

        html += '<div><h4>First Half vs. Second Half</h4>\n'
        html += '<table><thead><tr><th>Period</th><th>Items</th><th>Value</th><th>Avg/Item</th></tr></thead><tbody>\n'
        h1_avg = h1_val / h1_cnt if h1_cnt else 0
        h2_avg = h2_val / h2_cnt if h2_cnt else 0
        html += f'<tr><td>{h1_label}</td><td class="num">{fi(h1_cnt)}</td><td class="num">{fc(h1_val)}</td><td class="num">{fc(h1_avg)}</td></tr>\n'
        html += f'<tr><td>{h2_label}</td><td class="num">{fi(h2_cnt)}</td><td class="num">{fc(h2_val)}</td><td class="num">{fc(h2_avg)}</td></tr>\n'
        h_diff = h2_val - h1_val
        h_dir = "higher" if h_diff > 0 else "lower"
        html += f'<tr style="border-top:2px solid {C["teal"]}"><td colspan="2"><em>H2 vs H1 difference</em></td><td class="num">{fc(abs(h_diff))} {h_dir}</td><td></td></tr>\n'
        html += '</tbody></table></div>\n'

        html += '<div><h4>Returns by Day of Week</h4>\n'
        html += '<table><thead><tr><th>Day</th><th>Returns</th><th>Value</th><th>Refund Rate</th></tr></thead><tbody>\n'
        for d in dow_order:
            d_cnt = int(dow_refunds.loc[d, "count"]) if d in dow_refunds.index else 0
            d_val = float(dow_refunds.loc[d, "value"]) if d in dow_refunds.index else 0.0
            d_orders = int(dow_total_orders.get(d, 0))
            d_rate = d_cnt / d_orders * 100 if d_orders else 0
            html += f'<tr><td>{d}</td><td class="num">{fi(d_cnt)}</td><td class="num">{fc(d_val)}</td><td class="num">{fp(d_rate)}</td></tr>\n'
        html += '</tbody></table></div>\n'
        html += '</div>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 6: EMPLOYEE REFUND ANALYSIS
        # ════════════════════════════════════════════════════════
        html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Refunds by Employee</h3>\n'
        html += '<p class="desc">Refund volume relative to each employee\'s total transaction count — identifies potential process or training gaps.</p>\n'

        emp_ref = refunds.groupby("salesperson").agg(
            ref_count=("refund_value", "size"), ref_value=("refund_value", "sum")
        ).sort_values("ref_count", ascending=False)
        emp_total_items = df[df["quantity"] > 0].groupby("salesperson").size()

        html += '<table><thead><tr><th>Employee</th><th>Refund Items</th><th>Refund Value</th><th>Total Items Sold</th><th>Refund Rate</th><th>Avg Refund</th></tr></thead><tbody>\n'
        for emp, row in emp_ref.iterrows():
            total_items_emp = int(emp_total_items.get(emp, 0))
            rate_emp = row["ref_count"] / total_items_emp * 100 if total_items_emp else 0
            avg_emp = row["ref_value"] / row["ref_count"] if row["ref_count"] else 0
            rate_flag = ' style="color:#C62828;font-weight:700"' if rate_emp > 2.0 else ''
            html += f'<tr><td>{esc(str(emp))}</td><td class="num">{fi(int(row["ref_count"]))}</td>'
            html += f'<td class="num">{fc(row["ref_value"])}</td><td class="num">{fi(total_items_emp)}</td>'
            html += f'<td class="num"{rate_flag}>{fp(rate_emp)}</td><td class="num">{fc(avg_emp)}</td></tr>\n'
        html += '</tbody></table>\n'

        # ════════════════════════════════════════════════════════
        # VIEW 7: GROOMING VOID LOG
        # ════════════════════════════════════════════════════════
        if not groom_refunds.empty:
            html += f'<h3 style="color:{C["brown"]};margin:22px 0 10px">Grooming Void Log</h3>\n'
            html += '<p class="desc">Every voided grooming service — review for patterns in employee, service type, timing, or customer.</p>\n'
            html += '<table><thead><tr><th>Date</th><th>Service</th><th>SKU</th><th>Value</th><th>Employee</th><th>Order Total</th></tr></thead><tbody>\n'
            for _, r in groom_refunds.sort_values("created", ascending=False).iterrows():
                o_match = df_orders[df_orders["order_id"] == r["order_id"]]
                o_total = o_match.iloc[0]["total"] if not o_match.empty else 0
                html += f'<tr><td>{r["created"].strftime("%b %d, %Y") if pd.notna(r["created"]) else ""}</td>'
                html += f'<td>{esc(str(r["name"]))}</td><td>{esc(str(r["sku"]))}</td>'
                html += f'<td class="num">{fc(r["refund_value"])}</td>'
                html += f'<td>{esc(str(r.get("salesperson","")))}</td>'
                html += f'<td class="num">{fc(o_total)}</td></tr>\n'
            html += '</tbody></table>\n'

        # ════════════════════════════════════════════════════════
        # GUIDANCE & INSIGHT
        # ════════════════════════════════════════════════════════
        html += '<div class="insight-box">'
        html += f'<strong>Elite Analyst Assessment:</strong><br><br>'

        if refund_pct_rev < 0.5:
            grade_label = "Exceptional"
            grade_color = C["green"]
        elif refund_pct_rev < 1.0:
            grade_label = "Strong"
            grade_color = C["green"]
        elif refund_pct_rev < 2.0:
            grade_label = "Normal"
            grade_color = C["amber"]
        else:
            grade_label = "Needs Attention"
            grade_color = C["red"]

        html += f'<span style="display:inline-block;background:{grade_color};color:#fff;padding:3px 12px;border-radius:12px;font-size:0.82rem;margin-bottom:8px">{grade_label}</span><br>'
        html += f'<strong>Refund/Revenue ratio: {fp(refund_pct_rev)}</strong> &mdash; '
        if refund_pct_rev < 1.0:
            html += f'well below the 1&ndash;3% industry average. This store demonstrates excellent product knowledge and customer communication at the counter.<br><br>'
        else:
            html += f'within the 1&ndash;3% industry average. Manageable but worth monitoring for trends.<br><br>'

        html += f'<strong>Grooming cancellation rate: {fp(cancel_rate)}</strong> vs. 5&ndash;15% industry norm &mdash; '
        if cancel_rate < 2.0:
            html += 'outstanding. Appointment management and client communication are working extremely well. '
        elif cancel_rate < 5.0:
            html += 'good performance, below industry average. '
        else:
            html += 'approaching industry average — review scheduling and confirmation processes. '

        html += f'Only <strong>{fi(total_cancel_events)} groom orders</strong> out of {fi(total_groom_orders)} resulted in a void or no-show for the entire year.<br><br>'

        html += '<strong>Key Findings:</strong><br>'
        html += f'&bull; <strong>Teeth Brushing add-on</strong> accounts for {fi(teeth_void_count)} of {fi(len(groom_refunds))} grooming voids &mdash; these are checkout declines, not cancellations. Consider not pre-loading this add-on to reduce unnecessary processing.<br>'

        ws_count = 0
        if not ret_reasons.empty and "Wrong Size" in ret_reasons.index:
            ws_count = int(ret_reasons.loc["Wrong Size", "count"])
        if ws_count > 0:
            html += f'&bull; <strong>&ldquo;Wrong Size&rdquo;</strong> is the #1 retail return reason ({fi(ws_count)} items). Staff-assisted sizing at point of sale &mdash; especially for collars, harnesses, and apparel &mdash; would reduce these.<br>'

        bwp_count = 0
        if not ret_reasons.empty and "Bought wrong product" in ret_reasons.index:
            bwp_count = int(ret_reasons.loc["Bought wrong product", "count"])
        if bwp_count > 0:
            html += f'&bull; <strong>&ldquo;Bought wrong product&rdquo;</strong> ({fi(bwp_count)} items) suggests customers need better guidance. Shelf signage and staff recommendations can help.<br>'

        max_month_key = max(cancel_monthly, key=lambda m: cancel_monthly[m]["total"])
        max_month_total = cancel_monthly[max_month_key]["total"]
        if max_month_total > 3:
            html += f'&bull; <strong>{max_month_key}</strong> had {fi(max_month_total)} cancellation events ({fp(cancel_monthly[max_month_key]["rate"])} rate) &mdash; investigate whether this was weather, staffing, or scheduling related.<br>'

        if abs(h_diff) > 100:
            html += f'&bull; <strong>H2 refund value was {fc(abs(h_diff))} {h_dir}</strong> than H1 &mdash; {"holiday season returns likely drove the increase." if h_diff > 0 else "returns improved in the second half of the year."}<br>'

        html += '<br><strong>Recommendations:</strong><br>'
        html += '&bull; <strong>Teeth Brushing workflow:</strong> Only add the Teeth Brushing add-on after confirming with the customer. This alone eliminates ~38% of grooming voids.<br>'
        if ws_count > 5:
            html += '&bull; <strong>Sizing assistance program:</strong> Implement a &ldquo;measure first&rdquo; policy for collars, harnesses, and apparel. Stock size charts at the register.<br>'
        html += '&bull; <strong>No-show policy:</strong> At this volume a formal deposit/late-cancel policy is not urgent, but consider implementing one as the business grows to protect groomer schedules.<br>'
        html += '&bull; <strong>Track in POS:</strong> Start using the FranPOS booking &ldquo;Cancel&rdquo; workflow so cancellation data flows to the appointment record. Currently voids/zero-orders are the only signal.<br>'

        html += '</div>\n'
        html += '</div>\n'

    # ── Prepare chart data for refunds & cancellations ──
    _refunds_tmp = df[df["quantity"] < 0].copy()
    if not _refunds_tmp.empty:
        _refunds_tmp["refund_value"] = (_refunds_tmp["price"] * _refunds_tmp["quantity"]).abs()
        _ref_monthly = _refunds_tmp.groupby("ym")["refund_value"].sum()
        refund_monthly_vals = [round(to_py(_ref_monthly.get(p["period"], 0)), 2) for p in periods]
        _ref_cnt_monthly = _refunds_tmp.groupby("ym").size()
        refund_monthly_cnts = [int(to_py(_ref_cnt_monthly.get(p["period"], 0))) for p in periods]
        # Cancel trend data for chart
        _groom_refunds_tmp = _refunds_tmp[_refunds_tmp["is_groom"] == True]
        _groom_orders_tmp = df_orders[df_orders["order_id"].isin(set(df[df["is_groom"] == True]["order_id"].unique()))]
        cancel_chart_events = []
        cancel_chart_rates = []
        for p in periods:
            pm = p["period"]
            void_m = set(_groom_refunds_tmp[_groom_refunds_tmp["ym"] == pm]["order_id"].unique()) if not _groom_refunds_tmp.empty else set()
            zero_m = set(_groom_orders_tmp[(_groom_orders_tmp["total"] == 0) & (_groom_orders_tmp["ym"] == pm)]["order_id"].unique()) if not _groom_orders_tmp.empty else set()
            total_m = len(void_m | zero_m)
            groom_total_m = len(_groom_orders_tmp[_groom_orders_tmp["ym"] == pm]) if not _groom_orders_tmp.empty else 0
            cancel_chart_events.append(total_m)
            cancel_chart_rates.append(round(total_m / groom_total_m * 100, 2) if groom_total_m else 0)
    else:
        refund_monthly_vals = [0] * n_periods
        refund_monthly_cnts = [0] * n_periods
        cancel_chart_events = [0] * n_periods
        cancel_chart_rates = [0] * n_periods


    # ── Charts JavaScript ──
    # Mark last month as partial if END_DATE is not end-of-month
    import run as _r2
    from datetime import datetime as _dt2
    import calendar as _cal2
    try:
        _end_dt2 = _dt2.strptime(_r2.END_DATE, "%Y-%m-%d")
        _last_day2 = _cal2.monthrange(_end_dt2.year, _end_dt2.month)[1]
        _last_partial = _end_dt2.day < _last_day2
    except Exception:
        _last_partial = False

    months_labels = []
    for _i2, _p2 in enumerate(periods):
        _lbl = _p2["label"]
        if _i2 == len(periods) - 1 and _rr_prorated:
            _lbl = _lbl + " (est.)"
        elif _i2 == len(periods) - 1 and _last_partial:
            _lbl = _lbl + " (partial)"
        months_labels.append(_lbl)

    groom_monthly = [round(to_py(groom[groom["ym"] == p["period"]]["net_sales"].sum()), 2) for p in periods]
    retail_monthly = [round(to_py(retail[retail["ym"] == p["period"]]["net_sales"].sum()), 2) for p in periods]

    # Pro-rate groom/retail monthly for partial month too
    if _rr_prorated and groom_monthly and retail_monthly:
        _pr_factor = monthly_rev[-1] / _partial_raw if _partial_raw > 0 else 1.0
        groom_monthly[-1] = round(groom_monthly[-1] * _pr_factor, 2)
        retail_monthly[-1] = round(retail_monthly[-1] * _pr_factor, 2)

    monthly_rev_js = [round(to_py(v), 2) for v in monthly_rev]
    # For partial years, trend_values has been rebuilt using combined prior+current trend
    trend_vals_js = [round(to_py(v), 2) for v in trend_values]

    svc_top = svc_mix.head(8)
    svc_labels = [str(r["service_type"]) for _, r in svc_top.iterrows()]
    svc_values = [round(to_py(r["revenue"]), 2) for _, r in svc_top.iterrows()]

    size_order_list = ["0-20 lbs", "21-40 lbs", "41-75 lbs", "76-100 lbs", "Over 100 lbs"]
    dog_data = core.groupby("dog_size")["quantity"].sum() if not core.empty else pd.Series()
    dog_values = [round(to_py(dog_data.get(s, 0)), 0) for s in size_order_list]

    cat_data = retail.groupby("retail_category")["net_sales"].sum().sort_values(ascending=False)
    cat_labels = [str(c) for c in cat_data.index[:10]]
    cat_values = [round(to_py(v), 2) for v in cat_data.values[:10]]

    seg_labels_js = [str(row["segment"]) for _, row in seg_summary.iterrows()]
    seg_values_js = [int(to_py(row["customers"])) for _, row in seg_summary.iterrows()]

    # Hours data
    hour_counts = df_orders.groupby("hour").size()
    hours_labels = list(range(8, 20))
    hours_values = [int(to_py(hour_counts.get(h, 0))) for h in hours_labels]
    hours_labels_str = [f"{h}:00" for h in hours_labels]

    cc = str(C["chart"]).replace("'", '"')

    html += f"""
<script>
const cc = {cc};
const mg = '{C["magenta"]}', tl = '{C["teal"]}', br = '{C["brown"]}', gn = '{C["green"]}';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.color = '#666';

(function() {{
    var _trendData = {trend_vals_js};
    var _revColors = {monthly_rev_js}.map(function(v,i,a) {{ return (i === a.length - 1 && {'true' if _rr_prorated else 'false'}) ? mg+'66' : mg+'cc'; }});
    var _datasets = [
        {{ label: 'Actual Revenue', data: {monthly_rev_js}, backgroundColor: _revColors, borderRadius: 6 }}
    ];
    if (_trendData.length > 0) {{
        _datasets.push({{ label: 'Trend Line', data: _trendData, type: 'line', borderColor: tl, borderWidth: 2, borderDash: [6,3], pointRadius: 0, fill: false }});
    }}
    new Chart(document.getElementById('trajectoryChart'), {{
        type: 'bar',
        data: {{ labels: {months_labels}, datasets: _datasets }},
        options: {{ responsive: true, maintainAspectRatio: true,
            scales: {{ y: {{ ticks: {{ callback: v => '$'+v.toLocaleString() }} }} }},
            plugins: {{ legend: {{ position: 'bottom' }},
                tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {{minimumFractionDigits:0}}) }} }} }} }}
    }});
}})();

new Chart(document.getElementById('revMixChart'), {{
    type: 'doughnut',
    data: {{ labels: ['Grooming','Retail','Gift Cards'],
        datasets: [{{ data: [{to_py(groom_rev):.2f},{to_py(retail_rev):.2f},{to_py(gift_rev):.2f}], backgroundColor: [mg,tl,br], borderWidth: 0, hoverOffset: 8 }}] }},
    options: {{ responsive: true, cutout: '65%',
        plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: ctx => ctx.label+': $'+ctx.parsed.toLocaleString() }} }} }} }}
}});

new Chart(document.getElementById('monthlyChart'), {{
    type: 'bar',
    data: {{ labels: {months_labels},
        datasets: [
            {{ label: 'Grooming', data: {groom_monthly}, backgroundColor: mg, borderRadius: 4 }},
            {{ label: 'Retail', data: {retail_monthly}, backgroundColor: tl, borderRadius: 4 }}
        ] }},
    options: {{ responsive: true, interaction: {{ mode: 'index' }},
        scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, ticks: {{ callback: v => '$'+v.toLocaleString() }} }} }},
        plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('hoursChart'), {{
    type: 'bar',
    data: {{ labels: {hours_labels_str},
        datasets: [{{ data: {hours_values}, backgroundColor: cc.slice(0,{len(hours_values)}).map((c,i) => {{
            const max = Math.max(...{hours_values});
            const ratio = {hours_values}[i] / max;
            return ratio > 0.8 ? mg : ratio > 0.5 ? tl : br+'88';
        }}), borderRadius: 6 }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }},
        scales: {{ y: {{ ticks: {{ callback: v => v.toLocaleString() }} }} }} }}
}});

new Chart(document.getElementById('svcMixChart'), {{
    type: 'bar',
    data: {{ labels: {svc_labels}, datasets: [{{ data: {svc_values}, backgroundColor: cc.slice(0,{len(svc_values)}), borderRadius: 6 }}] }},
    options: {{ responsive: true, indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
        scales: {{ x: {{ ticks: {{ callback: v => '$'+v.toLocaleString() }} }} }} }}
}});

new Chart(document.getElementById('dogSizeChart'), {{
    type: 'bar',
    data: {{ labels: {size_order_list}, datasets: [{{ data: {dog_values}, backgroundColor: cc.slice(0,5), borderRadius: 6 }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('catChart'), {{
    type: 'doughnut',
    data: {{ labels: {cat_labels}, datasets: [{{ data: {cat_values}, backgroundColor: cc.slice(0,{len(cat_values)}), borderWidth: 0 }}] }},
    options: {{ responsive: true, cutout: '55%', plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 10 }} }} }} }} }}
}});

new Chart(document.getElementById('custSegChart'), {{
    type: 'bar',
    data: {{ labels: {seg_labels_js}, datasets: [{{ data: {seg_values_js}, backgroundColor: cc.slice(0,{len(seg_values_js)}), borderRadius: 6 }}] }},
    options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
}});

if (document.getElementById('refundTrendChart')) {{
    new Chart(document.getElementById('refundTrendChart'), {{
        type: 'bar',
        data: {{
            labels: {months_labels},
            datasets: [
                {{ label: 'Refund Value', data: {refund_monthly_vals}, backgroundColor: '{C["red"]}88', borderRadius: 4, yAxisID: 'y' }},
                {{ label: 'Items Returned', data: {refund_monthly_cnts}, type: 'line', borderColor: '{C["teal"]}', borderWidth: 2, pointBackgroundColor: '{C["teal"]}', fill: false, yAxisID: 'y1' }}
            ]
        }},
        options: {{ responsive: true, interaction: {{ mode: 'index' }},
            scales: {{
                y: {{ position: 'left', ticks: {{ callback: v => '$'+v.toLocaleString() }} }},
                y1: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Items' }} }}
            }},
            plugins: {{ legend: {{ position: 'bottom' }},
                tooltip: {{ callbacks: {{ label: ctx => {{
                    if (ctx.dataset.yAxisID === 'y') return 'Refund Value: $' + ctx.parsed.y.toLocaleString();
                    return 'Items: ' + ctx.parsed.y;
                }} }} }}
            }}
        }}
    }});
}}

if (document.getElementById('cancelTrendChart')) {{
    new Chart(document.getElementById('cancelTrendChart'), {{
        type: 'bar',
        data: {{
            labels: {months_labels},
            datasets: [
                {{ label: 'Cancel Events', data: {cancel_chart_events}, backgroundColor: '{C["red"]}99', borderRadius: 4, yAxisID: 'y' }},
                {{ label: 'Cancel Rate %', data: {cancel_chart_rates}, type: 'line', borderColor: '{C["magenta"]}', borderWidth: 2, pointBackgroundColor: '{C["magenta"]}', fill: false, yAxisID: 'y1' }}
            ]
        }},
        options: {{ responsive: true, interaction: {{ mode: 'index' }},
            scales: {{
                y: {{ position: 'left', title: {{ display: true, text: 'Events' }}, beginAtZero: true }},
                y1: {{ position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Rate %' }}, beginAtZero: true }}
            }},
            plugins: {{ legend: {{ position: 'bottom' }},
                tooltip: {{ callbacks: {{ label: ctx => {{
                    if (ctx.dataset.yAxisID === 'y1') return 'Cancel Rate: ' + ctx.parsed.y.toFixed(1) + '%';
                    return 'Events: ' + ctx.parsed.y;
                }} }} }}
            }}
        }}
    }});
}}
</script>
"""
    if body_only:
        if year_suffix:
            for cid in ["trajectoryChart","revMixChart","monthlyChart","hoursChart",
                        "svcMixChart","dogSizeChart","catChart","custSegChart",
                        "cancelTrendChart","refundTrendChart"]:
                html = html.replace(f'id="{cid}"', f'id="{cid}_{year_suffix}"')
                html = html.replace(f"getElementById('{cid}')", f"getElementById('{cid}_{year_suffix}')")
                html = html.replace(f'getElementById("{cid}")', f"getElementById('{cid}_{year_suffix}')")
        return html
    html += HTML_FOOT
    with open(output_path, "w") as f:
        f.write(html)
    print(f"  Saved: {output_path}")


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD 2: Price Increase Impact Analysis
# ═════════════════════════════════════════════════════════════════════════════

def generate_price_increase_dashboard(df, df_orders, output_path):
    print("Generating price increase impact dashboard...")

    core = df[df["groom_category"] == "core"].copy()
    core_rev = core["net_sales"].sum()
    core_appts = core["quantity"].sum()
    avg_ticket = core["price"].mean()

    # Revenue impact
    incremental = core_appts * 5
    # Estimate future-period appointments (use last 2/3 of data)
    _pi_periods = get_periods(core)
    _pi_cutoff = len(_pi_periods) // 3
    _pi_future = set(p["period"] for p in _pi_periods[_pi_cutoff:])
    may_dec_appts = core[core["ym"].isin(_pi_future)]["quantity"].sum()
    may_dec_inc = may_dec_appts * 5
    groomer_uplift = incremental * 0.50
    store_uplift = incremental * 0.50

    # Customer sensitivity
    groom_customers = set(df[df["is_groom"]]["customer_id"].unique())
    cust_orders = df_orders[df_orders["customer_id"].isin(groom_customers)]
    cust_visits = cust_orders.groupby("customer_id").agg(
        visits=("created", lambda x: int(x.dt.date.nunique())),
        total_spend=("subtotal", "sum"),
    ).reset_index()

    def churn_risk(row):
        v, spv = row["visits"], row["total_spend"] / row["visits"] if row["visits"] else 0
        if v >= 5 and spv >= 80: return "Very Low"
        elif v >= 3 and spv >= 60: return "Low"
        elif v >= 2: return "Moderate"
        return "Higher"

    cust_visits["churn_risk"] = cust_visits.apply(churn_risk, axis=1)
    total_custs = len(cust_visits)
    total_spend = cust_visits["total_spend"].sum()
    low_risk = cust_visits[cust_visits["churn_risk"].isin(["Very Low", "Low"])]
    low_risk_pct = low_risk["total_spend"].sum() / total_spend * 100 if total_spend else 0

    risk_summary = cust_visits.groupby("churn_risk").agg(
        customers=("customer_id", "count"), total_spend=("total_spend", "sum")
    ).reset_index()
    risk_summary["pct_customers"] = risk_summary["customers"] / total_custs * 100
    risk_summary["pct_revenue"] = risk_summary["total_spend"] / total_spend * 100

    # ── Break-even churn analysis (NEW) ──
    # How many customers can we lose and still be revenue-neutral?
    breakeven_churn_pct = incremental / core_rev * 100 if core_rev else 0
    breakeven_customers = int(total_custs * breakeven_churn_pct / 100)
    avg_groom_spend_per_cust = core_rev / total_custs if total_custs else 0

    _home_url_pi = "../index.html"
    html = html_head(
        "Price Increase Impact Analysis",
        f"{STORE_NAME} — $5 Groom/Bath Increase Effective May 1",
        home_url=_home_url_pi,
    )

    # ── KPI Cards ──
    html += '<div class="kpi-grid">\n'
    kpis = [
        ("Incremental Revenue (Gross)", fc(incremental), f"+$5 × {fi(core_appts)} appointments", ""),
        ("Store Net Uplift (50%)", fc(store_uplift), f"+$2.50/appt after commission", "green"),
        ("Groomer Commission Uplift (50%)", fc(groomer_uplift), f"+$2.50/appt to groomers", "green"),
        ("May-Dec Store Net", fc(may_dec_inc * 0.50), f"8-month implementation", "accent"),
        ("Revenue Lift", fp(incremental / core_rev * 100 if core_rev else 0), f"On {fc(core_rev)} base", ""),
        ("Break-Even Churn Tolerance", fp(breakeven_churn_pct), f"Can lose {fi(breakeven_customers)} of {fi(total_custs)} customers", ""),
        ("Low-Risk Revenue", fp(low_risk_pct), "From loyal customers (3+ visits)", ""),
    ]
    for label, value, sub, cls in kpis:
        html += f'<div class="kpi-card {cls}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>\n'
    html += '</div>\n'

    # Pre-compute groomer data (needed in guidance and groomer impact sections)
    by_groomer = core.groupby("salesperson").agg(appts=("quantity", "sum"), revenue=("net_sales", "sum"), avg=("price", "mean")).reset_index().sort_values("revenue", ascending=False)
    by_groomer = by_groomer[by_groomer["salesperson"] != ""]

    # ── Move Assessment & Grade ──
    # Grading criteria: % ticket change, churn buffer, revenue trajectory, customer loyalty mix
    pct_change = 5 / avg_ticket * 100
    churn_buffer = breakeven_churn_pct
    # Score components (each 0-25 for max 100)
    # 1. Price change magnitude: <5% = 25, 5-8% = 20, 8-12% = 15, >12% = 5
    if pct_change < 5: score_pct = 25
    elif pct_change < 8: score_pct = 20
    elif pct_change < 12: score_pct = 15
    else: score_pct = 5
    # 2. Churn buffer: >8% = 25, 5-8% = 20, 3-5% = 15, <3% = 5
    if churn_buffer > 8: score_churn = 25
    elif churn_buffer > 5: score_churn = 20
    elif churn_buffer > 3: score_churn = 15
    else: score_churn = 5
    # 3. Low-risk revenue concentration: >80% = 25, 60-80% = 20, 40-60% = 15, <40% = 5
    if low_risk_pct > 80: score_loyal = 25
    elif low_risk_pct > 60: score_loyal = 20
    elif low_risk_pct > 40: score_loyal = 15
    else: score_loyal = 5
    # 4. Growth trajectory: store is growing = 25, flat = 15, declining = 5
    monthly_core = core.groupby("month")["net_sales"].sum()
    if len(monthly_core) >= 6:
        h2_core = monthly_core[monthly_core.index > 6].mean()
        h1_core = monthly_core[monthly_core.index <= 6].mean()
        if h2_core > h1_core * 1.05: score_growth = 25
        elif h2_core > h1_core * 0.95: score_growth = 15
        else: score_growth = 5
    else:
        score_growth = 15

    total_score = score_pct + score_churn + score_loyal + score_growth
    if total_score >= 85: grade, grade_color = "A", C["green"]
    elif total_score >= 70: grade, grade_color = "B+", C["green"]
    elif total_score >= 55: grade, grade_color = "B", C["teal"]
    elif total_score >= 40: grade, grade_color = "C", C["amber"]
    else: grade, grade_color = "D", C["red"]

    # ── Revenue Impact by Service Type ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Revenue Impact by Service Type</h2>\n'
    html += '<p class="desc">$5 increase applied to all core groom and bath services</p>\n'
    html += '<div class="grid-2"><div>\n'

    by_type = core.groupby("service_type").agg(
        appts=("quantity", "sum"), revenue=("net_sales", "sum"), avg_ticket=("price", "mean")
    ).reset_index().sort_values("revenue", ascending=False)

    html += '<table><thead><tr><th>Service</th><th>Appts</th><th>Current Avg</th><th>New Avg</th><th>% Inc</th><th>Gross Incr.</th><th>Commission (50%)</th><th>Store Net (50%)</th></tr></thead><tbody>\n'
    for _, row in by_type.iterrows():
        inc_r = row["appts"] * 5
        pct = 5 / row["avg_ticket"] * 100 if row["avg_ticket"] else 0
        html += f'<tr><td>{esc(row["service_type"])}</td><td class="num">{fi(row["appts"])}</td>'
        html += f'<td class="num">{fc(row["avg_ticket"])}</td><td class="num green">{fc(row["avg_ticket"]+5)}</td>'
        html += f'<td class="num">{fp(pct)}</td><td class="num">+{fc(inc_r)}</td>'
        html += f'<td class="num">{fc(inc_r * 0.50)}</td><td class="num green">+{fc(inc_r * 0.50)}</td></tr>\n'

    html += f'<tr style="font-weight:700;border-top:2px solid {C["teal"]}"><td>TOTAL</td><td class="num">{fi(core_appts)}</td>'
    html += f'<td class="num">{fc(avg_ticket)}</td><td class="num green">{fc(avg_ticket+5)}</td>'
    html += f'<td></td><td class="num">+{fc(incremental)}</td>'
    html += f'<td class="num">{fc(groomer_uplift)}</td><td class="num green">+{fc(store_uplift)}</td></tr>\n'
    html += '</tbody></table></div>\n'
    html += '<div><div class="chart-container"><canvas id="impactChart"></canvas></div></div></div></div>\n'

    # ── Pricing Matrix ──
    html += '<div class="section">\n'
    html += '<h2><span class="dot"></span>Current Pricing Matrix</h2>\n'
    html += '<p class="desc">Average prices by service type and dog size, with post-increase projection</p>\n'

    matrix = core[core["dog_size"].notna()].groupby(["service_type", "dog_size"]).agg(
        avg_price=("price", "mean"), appts=("quantity", "sum"), revenue=("net_sales", "sum")
    ).reset_index()

    html += '<table><thead><tr><th>Service</th><th>Size</th><th>Current Avg</th><th>After +$5</th><th>Appts</th><th>Revenue</th></tr></thead><tbody>\n'
    for _, row in matrix.sort_values(["service_type", "dog_size"]).iterrows():
        html += f'<tr><td>{esc(row["service_type"])}</td><td>{esc(row["dog_size"])}</td>'
        html += f'<td class="num">{fc(row["avg_price"])}</td><td class="num green">{fc(row["avg_price"]+5)}</td>'
        html += f'<td class="num">{fi(row["appts"])}</td><td class="num">{fc(row["revenue"])}</td></tr>\n'
    html += '</tbody></table></div>\n'

    # ── Groomer Impact + Churn Risk (side by side) ──
    html += '<div class="grid-2">\n'

    # Groomer impact
    html += '<div class="section"><h2><span class="dot"></span>Per-Groomer Commission Impact</h2><p class="desc">$5 increase splits 50/50 — groomer gets +$2.50, store keeps +$2.50 per appt</p>\n'
    html += '<table><thead><tr><th>Groomer</th><th>Appts</th><th>Gross Incr.</th><th>Groomer Gets (50%)</th><th>Store Keeps (50%)</th><th>Groomer +/mo</th></tr></thead><tbody>\n'
    for _, row in by_groomer.iterrows():
        gross = row["appts"] * 5
        comm = gross * 0.50
        store = gross * 0.50
        html += f'<tr><td>{esc(row["salesperson"])}</td><td class="num">{fi(row["appts"])}</td>'
        html += f'<td class="num">{fc(gross)}</td>'
        html += f'<td class="num green">+{fc(comm)}</td><td class="num green">+{fc(store)}</td>'
        html += f'<td class="num green">+{fc(comm/12)}/mo</td></tr>\n'
    total_gross = by_groomer["appts"].sum() * 5
    html += f'<tr style="font-weight:700;border-top:2px solid {C["teal"]}"><td>TOTAL</td><td class="num">{fi(by_groomer["appts"].sum())}</td>'
    html += f'<td class="num">{fc(total_gross)}</td><td class="num green">+{fc(total_gross*0.50)}</td><td class="num green">+{fc(total_gross*0.50)}</td><td></td></tr>\n'
    html += '</tbody></table>\n'
    avg_monthly = by_groomer["appts"].mean() * 5 * 0.50 / 12 if len(by_groomer) else 0
    html += f'<div class="insight-box"><strong>Talking point:</strong> "This means an extra <strong>{fc(avg_monthly)}/month</strong> per groomer on average — we\'re investing in you."</div></div>\n'

    # Churn risk
    html += '<div class="section"><h2><span class="dot"></span>Customer Retention Risk</h2><p class="desc">Churn probability from a $5 increase</p>\n'
    html += '<div class="chart-container"><canvas id="riskChart"></canvas></div>\n'

    html += '<table style="margin-top:14px"><thead><tr><th>Risk</th><th>Customers</th><th>% Cust</th><th>Revenue</th><th>% Rev</th></tr></thead><tbody>\n'
    risk_colors = {"Very Low": "badge-green", "Low": "badge-teal", "Moderate": "badge-yellow", "Higher": "badge-red"}
    risk_order = ["Very Low", "Low", "Moderate", "Higher"]
    risk_summary["_o"] = risk_summary["churn_risk"].map({s: i for i, s in enumerate(risk_order)})
    risk_summary = risk_summary.sort_values("_o")

    for _, row in risk_summary.iterrows():
        html += f'<tr><td><span class="badge {risk_colors.get(row["churn_risk"], "")}">{esc(row["churn_risk"])}</span></td>'
        html += f'<td class="num">{fi(row["customers"])}</td><td class="num">{fp(row["pct_customers"])}</td>'
        html += f'<td class="num">{fc(row["total_spend"])}</td><td class="num">{fp(row["pct_revenue"])}</td></tr>\n'
    html += '</tbody></table>\n'
    html += f'<div class="insight-box"><strong>{fp(low_risk_pct)}</strong> of revenue from low-risk customers. A {5/avg_ticket*100:.1f}% price change is well within normal annual adjustments.</div></div>\n'
    html += '</div>\n'

    # ── Ticket Trend ──
    html += '<div class="section"><h2><span class="dot"></span>Average Core Groom Ticket — Monthly Trend</h2><p class="desc">Natural ticket growth before any formal increase</p>\n'
    html += '<div class="chart-container"><canvas id="trendChart"></canvas></div>\n'

    _pi_all_periods = get_periods(core)
    monthly_trend = core.groupby("ym").agg(avg=("price", "mean"), med=("price", "median"), appts=("quantity", "sum")).reset_index()
    monthly_trend["month_label"] = monthly_trend["ym"].apply(lambda p: p.strftime("%b '%y"))
    _pi_mid = len(_pi_all_periods) // 2 if len(_pi_all_periods) > 1 else 1
    _pi_first = set(p["period"] for p in _pi_all_periods[:_pi_mid])
    _pi_second = set(p["period"] for p in _pi_all_periods[_pi_mid:])
    h1_avg = core[core["ym"].isin(_pi_first)]["price"].mean()
    h2_avg = core[core["ym"].isin(_pi_second)]["price"].mean()
    t_pct = (h2_avg - h1_avg) / h1_avg * 100 if h1_avg else 0
    html += f'<div class="insight-box"><strong>First half:</strong> {fc(h1_avg)} → <strong>Second half:</strong> {fc(h2_avg)} — Natural trend: <strong>{"+" if t_pct>=0 else ""}{fp(t_pct)}</strong></div></div>\n'

    # ── Upsell Opportunity ──
    spa = df[df["groom_category"] == "spa"]
    addon = df[df["groom_category"] == "addon"]
    core_oids = set(core["order_id"].unique())
    spa_oids = set(spa["order_id"].unique())
    addon_oids = set(addon["order_id"].unique())
    cc_count = len(core_oids)
    any_up = len(core_oids & (spa_oids | addon_oids))
    spa_ct = len(core_oids & spa_oids)
    addon_ct = len(core_oids & addon_oids)
    upsell_rate = any_up / cc_count * 100 if cc_count else 0
    spa_rev = spa["net_sales"].sum()
    addon_rev = addon["net_sales"].sum()
    avg_up_val = (spa_rev + addon_rev) / any_up if any_up else 0
    up_opp = cc_count * 0.05 * avg_up_val

    html += '<div class="section"><h2><span class="dot"></span>Upsell Companion Strategy</h2><p class="desc">Pair the price increase with stronger SPA/add-on upselling</p>\n'
    html += '<div class="kpi-grid" style="margin-bottom:14px">\n'
    html += f'<div class="kpi-card"><div class="kpi-label">SPA Attach Rate</div><div class="kpi-value">{fp(spa_ct/cc_count*100 if cc_count else 0)}</div></div>\n'
    html += f'<div class="kpi-card"><div class="kpi-label">Add-On Attach Rate</div><div class="kpi-value">{fp(addon_ct/cc_count*100 if cc_count else 0)}</div></div>\n'
    html += f'<div class="kpi-card accent"><div class="kpi-label">Overall Upsell Rate</div><div class="kpi-value">{fp(upsell_rate)}</div></div>\n'
    html += f'<div class="kpi-card green"><div class="kpi-label">Combined Annual Uplift</div><div class="kpi-value">{fc(incremental + up_opp)}</div><div class="kpi-sub">$5 increase + 5pt upsell improvement</div></div>\n'
    html += '</div></div>\n'

    # ── Rollout Talking Points ──
    html += '<div class="section"><h2><span class="dot"></span>Rollout Talking Points</h2><p class="desc">Data-backed scripts for Kyle\'s team meeting and client conversations</p>\n'

    html += f'<h3 style="color:{C["brown"]};margin:14px 0 8px;font-size:1.05rem">For the Team Meeting (Week 6)</h3>\n'
    for pt in [
        f"This $5 increase generates <strong>{fc(incremental)}</strong> gross — the store nets <strong>{fc(store_uplift)}</strong> after your 50% commission.",
        f"At 50% commission, that's <strong>$2.50 per appointment</strong> more in your pocket.",
        f"For a groomer doing 50 dogs/week: ~$125/week or ~$500/month more in commission.",
        f"Our avg core groom is <strong>{fc(avg_ticket)}</strong> — this is just a {5/avg_ticket*100:.1f}% adjustment.",
        f"We serve <strong>{total_custs:,}</strong> grooming customers. {fp(low_risk_pct)} of revenue is from loyal, low-risk clients.",
        f"Even if we lost {fp(breakeven_churn_pct)} of customers ({fi(breakeven_customers)} people), we'd still break even. Actual churn will be far below this.",
        "This keeps us competitive as a premium grooming destination in the NY metro area.",
    ]:
        html += f'<div class="talking-point">{pt}</div>\n'

    html += f'<h3 style="color:{C["brown"]};margin:20px 0 8px;font-size:1.05rem">For Client Conversations (Weeks 4-2)</h3>\n'
    for pt in [
        "\"We're adjusting grooming prices by $5 starting May 1 — reflecting our investment in training, safety, and quality of care.\"",
        "\"This adjustment helps us retain our best groomers and maintain the experience you expect.\"",
        "\"We wanted to give you plenty of notice — no surprises. We value having you in the Woof Gang family.\"",
        "\"Want to lock in current pricing? Pre-book your next few appointments before May 1.\"",
    ]:
        html += f'<div class="talking-point" style="font-style:italic">{esc(pt)}</div>\n'

    html += f'<h3 style="color:{C["brown"]};margin:20px 0 8px;font-size:1.05rem">Objection Handling</h3>\n'
    for q, a in [
        ("\"That's too expensive.\"", f"\"Our average groom is {fc(avg_ticket)} — this is less than {5/avg_ticket*100:.1f}%. The quality and safety your dog receives is worth it.\""),
        ("\"I can find cheaper.\"", "\"You can. What sets us apart is certified groomers, premium products, and the trust with your groomer. We'd love to keep caring for [dog's name].\""),
        ("\"Why now?\"", "\"Operating costs — especially retaining great staff — have increased. This helps us keep the team you know and love.\""),
    ]:
        html += f'<div class="objection"><div class="q">{esc(q)}</div><div class="a">{esc(a)}</div></div>\n'
    html += '</div>\n'

    # ── Move Assessment (reveal at bottom) ──
    # Determine verdict line based on score
    if total_score >= 85:
        verdict = "This is a strong, well-supported move."
    elif total_score >= 70:
        verdict = "The data supports this move with high confidence."
    elif total_score >= 55:
        verdict = "A reasonable move — monitor closely post-launch."
    elif total_score >= 40:
        verdict = "Proceed with caution — consider a smaller increase first."
    else:
        verdict = "The data suggests this may not be the right time."

    html += f'''
<div class="section reveal-section" id="gradeSection" style="text-align:center;padding:40px 30px 50px">
  <h2 style="font-size:1.4rem;margin-bottom:6px"><span class="dot"></span>Move Assessment</h2>
  <p class="desc" style="margin-bottom:30px">Data-driven grade for the $5 price increase based on four risk factors</p>

  <!-- Grade circle — starts hidden, reveals on scroll -->
  <div id="gradeReveal" style="opacity:0;transform:scale(0.3);transition:none">
    <div style="width:160px;height:160px;border-radius:50%;background:{grade_color};display:inline-flex;align-items:center;justify-content:center;
         box-shadow:0 0 0 0 {grade_color}44;margin-bottom:18px" id="gradeCircle">
      <span style="font-size:4rem;font-weight:900;color:white;letter-spacing:-2px">{grade}</span>
    </div>
    <div style="font-size:1.5rem;font-weight:800;color:{C["brown"]};margin-bottom:4px">{total_score} <span style="font-size:1rem;font-weight:400;color:#888">/ 100</span></div>
    <div style="font-size:1.05rem;color:#555;margin-bottom:30px">{verdict}</div>
  </div>

  <!-- Scoring breakdown — cascades in -->
  <div id="scoreRows" style="max-width:640px;margin:0 auto;text-align:left">
    <table><thead><tr><th>Factor</th><th>Score</th><th>Assessment</th></tr></thead><tbody>
    <tr class="score-row" style="opacity:0;transform:translateY(12px)"><td>Price Change Magnitude</td><td class="num" style="font-weight:700">{score_pct}/25</td><td>{fp(pct_change)} increase — {"minimal" if pct_change < 5 else "moderate" if pct_change < 8 else "noticeable"} to customers</td></tr>
    <tr class="score-row" style="opacity:0;transform:translateY(12px)"><td>Churn Buffer (Break-Even)</td><td class="num" style="font-weight:700">{score_churn}/25</td><td>Can absorb {fp(churn_buffer)} churn before revenue-negative</td></tr>
    <tr class="score-row" style="opacity:0;transform:translateY(12px)"><td>Customer Loyalty Mix</td><td class="num" style="font-weight:700">{score_loyal}/25</td><td>{fp(low_risk_pct)} of revenue from low-risk repeat customers</td></tr>
    <tr class="score-row" style="opacity:0;transform:translateY(12px)"><td>Revenue Trajectory</td><td class="num" style="font-weight:700">{score_growth}/25</td><td>Store is {"growing strongly" if score_growth == 25 else "stable" if score_growth == 15 else "declining"} — momentum absorbs friction</td></tr>
    <tr class="score-row" style="opacity:0;transform:translateY(12px);font-weight:700;border-top:2px solid {C["teal"]}"><td>TOTAL</td><td class="num">{total_score}/100</td><td>Grade: {grade}</td></tr>
    </tbody></table>
  </div>

  <!-- Summary + Guidance — fades in last -->
  <div id="guidanceReveal" style="opacity:0;transform:translateY(16px);max-width:720px;margin:24px auto 0;text-align:left">
    <div style="font-size:0.88rem;color:#555;line-height:1.7;margin-bottom:20px">
      A ${to_py(avg_ticket):.0f} &rarr; ${to_py(avg_ticket)+5:.0f} adjustment is a <strong>{pct_change:.1f}%</strong> change &mdash; comfortably within the 5-8% annual adjustment range that premium service businesses absorb without meaningful churn.
      The store can lose up to <strong>{fi(breakeven_customers)}</strong> customers ({fp(breakeven_churn_pct)} of the base) and still break even on revenue.
      With <strong>{fp(low_risk_pct)}</strong> of grooming revenue coming from loyal, low-risk clients who won\'t leave over $5, actual churn will be a fraction of that threshold.
    </div>
    <h3 style="color:{C["brown"]};margin:0 0 10px;font-size:1rem">Guidance</h3>
    <div style="font-size:0.88rem;color:#444;line-height:1.8">
    <ul style="padding-left:20px">
      <li><strong>Timing is right.</strong> The store is on an upward revenue trajectory (Jan&rarr;Dec nearly tripled). Price adjustments during growth periods are absorbed far more easily than during flat or declining phases.</li>
      <li><strong>Commission framing matters.</strong> Lead the team meeting with the groomer win: +$2.50/appointment = ~${to_py(by_groomer["appts"].mean() if len(by_groomer) else 0)*5*0.50/12:.0f}/month per groomer. When groomers see personal upside, they become advocates instead of resistors.</li>
      <li><strong>Pre-book strategy.</strong> Offering existing clients the chance to &ldquo;lock in current pricing&rdquo; by pre-booking 2-3 appointments before May 1 accelerates near-term revenue and deepens commitment.</li>
      <li><strong>Monitor, don\'t fear.</strong> Track weekly appointment volume for 8 weeks post-implementation. If bookings drop more than 5% from the trailing 4-week average, consider a targeted loyalty offer for at-risk segments &mdash; but don\'t preemptively discount.</li>
      <li><strong>Pair with value.</strong> Announce the increase alongside a tangible quality improvement: new products, extended appointment slots, complimentary teeth check, or a loyalty perk. The narrative is &ldquo;more value&rdquo; not &ldquo;higher price.&rdquo;</li>
    </ul></div>
  </div>
</div>
'''

    # ── Charts ──
    impact_labels = [str(r["service_type"]) for _, r in by_type.iterrows()]
    impact_current = [float(r["revenue"]) for _, r in by_type.iterrows()]
    impact_inc = [float(r["appts"] * 5) for _, r in by_type.iterrows()]

    risk_labels = [str(row["churn_risk"]) for _, row in risk_summary.iterrows()]
    risk_values = [int(row["customers"]) for _, row in risk_summary.iterrows()]
    risk_cc = []
    for _, row in risk_summary.iterrows():
        rn = row["churn_risk"]
        risk_cc.append("#4CAF50" if rn == "Very Low" else "#26A69A" if rn == "Low" else "#FFC107" if rn == "Moderate" else "#EF5350")

    trend_labels = [str(r["month_label"]) for _, r in monthly_trend.iterrows()]
    trend_avg = [round(float(r["avg"]), 2) for _, r in monthly_trend.iterrows()]
    trend_med = [round(float(r["med"]), 2) for _, r in monthly_trend.iterrows()]

    html += f"""
<script>
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.color = '#666';
const mg='{C["magenta"]}', tl='{C["teal"]}', gn='{C["green"]}';

new Chart(document.getElementById('impactChart'), {{
    type: 'bar',
    data: {{ labels: {impact_labels},
        datasets: [
            {{ label: 'Current Revenue', data: {impact_current}, backgroundColor: tl, borderRadius: 4 }},
            {{ label: 'Incremental (+$5)', data: {impact_inc}, backgroundColor: gn, borderRadius: 4 }}
        ] }},
    options: {{ responsive: true, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, ticks: {{ callback: v => '$'+v.toLocaleString() }} }} }},
        plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('riskChart'), {{
    type: 'doughnut',
    data: {{ labels: {risk_labels}, datasets: [{{ data: {risk_values}, backgroundColor: {risk_cc}, borderWidth: 0 }}] }},
    options: {{ responsive: true, cutout: '60%', plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('trendChart'), {{
    type: 'line',
    data: {{ labels: {trend_labels},
        datasets: [
            {{ label: 'Avg Ticket', data: {trend_avg}, borderColor: mg, backgroundColor: mg+'22', fill: true, tension: 0.3, pointRadius: 5, pointBackgroundColor: mg }},
            {{ label: 'Median', data: {trend_med}, borderColor: tl, borderDash: [5,5], tension: 0.3, pointRadius: 4, pointBackgroundColor: tl }}
        ] }},
    options: {{ responsive: true, scales: {{ y: {{ ticks: {{ callback: v => '$'+v }} }} }},
        plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

// ── Grade Reveal Animation ──
(function() {{
  const section = document.getElementById('gradeSection');
  const circle  = document.getElementById('gradeReveal');
  const rows    = document.querySelectorAll('.score-row');
  const guide   = document.getElementById('guidanceReveal');
  const ring    = document.getElementById('gradeCircle');
  let fired = false;

  const observer = new IntersectionObserver(entries => {{
    entries.forEach(entry => {{
      if (entry.isIntersecting && !fired) {{
        fired = true;
        // 1. Grade circle — scale + fade in with overshoot
        circle.style.transition = 'opacity 0.6s ease, transform 0.7s cubic-bezier(0.34, 1.56, 0.64, 1)';
        circle.style.opacity = '1';
        circle.style.transform = 'scale(1)';
        // pulse ring
        setTimeout(() => {{
          ring.style.transition = 'box-shadow 0.8s ease';
          ring.style.boxShadow = '0 0 0 18px {grade_color}22';
        }}, 600);
        setTimeout(() => {{
          ring.style.boxShadow = '0 0 0 0 {grade_color}00';
        }}, 1400);
        // 2. Score rows — cascade in
        rows.forEach((row, i) => {{
          setTimeout(() => {{
            row.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
            row.style.opacity = '1';
            row.style.transform = 'translateY(0)';
          }}, 800 + i * 120);
        }});
        // 3. Guidance — fade in last
        setTimeout(() => {{
          guide.style.transition = 'opacity 0.6s ease, transform 0.5s ease';
          guide.style.opacity = '1';
          guide.style.transform = 'translateY(0)';
        }}, 800 + rows.length * 120 + 200);
      }}
    }});
  }}, {{ threshold: 0.15 }});
  observer.observe(section);
}})();
</script>
"""
    html += HTML_FOOT
    with open(output_path, "w") as f:
        f.write(html)
    print(f"  Saved: {output_path}")


# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    df, df_orders, emp_map = load_data()
    print(f"Loaded: {len(df)} items, {len(df_orders)} orders")

    out_dir = Path(__file__).parent.parent / "port-washington"
    generate_main_dashboard(df, df_orders, out_dir / "WoofGang_PortWashington_2025_Dashboard.html")
    generate_price_increase_dashboard(df, df_orders, out_dir / "WoofGang_PortWashington_PriceIncrease_Dashboard.html")
    print("\nDone.")
