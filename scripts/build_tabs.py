#!/usr/bin/env python3
"""Builds the tabbed all-years dashboard by calling generate_dashboards per-year."""
import sys, types, json, re
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
BASE_DIR    = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))

from config import get_store
from classifier import classify_item  # noqa: F401

store = get_store("port-washington")
out_dir  = store.output_dir
data_dir = store.data_dir

# Set up fake 'run' module so generate_dashboards can import from it
fake_run = types.ModuleType('run')
fake_run.STORE_NAME = store.name
fake_run.START_DATE = store.start_date
fake_run.END_DATE   = store.end_date
fake_run.DATA_DIR   = data_dir
fake_run.classify_item = classify_item
sys.modules["run"] = fake_run

import generate_dashboards as gd

# ── Merge all years ──────────────────────────────────────────────────────────
merged = {"order_items": [], "orders": [], "employees": []}
seen_item_ids = set()
seen_order_ids = set()
for yr in ["2024", "2025", "2026"]:
    p = data_dir / f"all_data_{yr}.json"
    if p.exists():
        with open(p) as f:
            d = json.load(f)
        for item in d.get("order_items", []):
            iid = item.get("OrderItemId") or (str(item.get("OrderId","")) + str(item.get("Name","")))
            if iid not in seen_item_ids:
                seen_item_ids.add(iid)
                merged["order_items"].append(item)
        for order in d.get("orders", []):
            oid = order.get("OrderId")
            if oid not in seen_order_ids:
                seen_order_ids.add(oid)
                merged["orders"].append(order)
        if not merged["employees"] and d.get("employees"):
            merged["employees"] = d["employees"]

with open(data_dir / "all_data.json", "w") as f:
    json.dump(merged, f)

df_all, df_orders_all, emp_map = gd.load_data()
print(f"Loaded: {len(df_all)} items, {len(df_orders_all)} orders")

# ── Per-year bodies ──────────────────────────────────────────────────────────
YEARS = {
    "2024": ("2024-09-26", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "2026": ("2026-01-01", "2026-03-07"),
}
YEAR_LABELS = {
    "2024": "2024 (Sep\u2013Dec)",
    "2025": "2025 (Full Year)",
    "2026": "2026 (YTD)",
}

bodies = {}
for yr, (start, end) in YEARS.items():
    import run as _r
    _r.START_DATE = start
    _r.END_DATE = end
    df_yr = df_all[df_all["year"] == int(yr)]
    df_ord_yr = df_orders_all[df_orders_all["year"] == int(yr)]
    body = gd.generate_main_dashboard(df_yr, df_ord_yr, None, body_only=True, year_suffix=yr)
    bodies[yr] = body
    print(f"  Built {yr}: {len(df_yr)} items, {len(df_ord_yr)} orders")

# ── Tabbed wrapper CSS ───────────────────────────────────────────────────────
tabbed_css = """
.yr-tab-bar {
    background: #C4276E;
    padding: 0 24px;
    display: flex;
    gap: 4px;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}
.yr-tab {
    padding: 14px 32px;
    border: none;
    background: transparent;
    color: rgba(255,255,255,0.6);
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    transition: all 0.2s;
    font-family: inherit;
    letter-spacing: 0.01em;
}
.yr-tab:hover { color: white; background: rgba(255,255,255,0.08); }
.yr-tab.active { color: white; border-bottom-color: #C4276E; }
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
    }
}
window.addEventListener('DOMContentLoaded', function() { showYear('2025'); });
"""

# ── Build final HTML ─────────────────────────────────────────────────────────
import pandas as _pd2
_all_dates = []
for _yr in ["2024", "2025", "2026"]:
    _p = data_dir / f"all_data_{_yr}.json"
    if _p.exists():
        with open(_p) as _f:
            _d = json.load(_f)
        for _item in _d.get("order_items", []):
            if _item.get("created"):
                _all_dates.append(_item["created"])
_last_date = _pd2.to_datetime(_all_dates).max().strftime("%-m/%-d/%Y") if _all_dates else "today"
html = gd.html_head("Woof Gang Port Washington", f"Store Performance Analysis  \u00b7  Sales through {_last_date}")
html = html.replace("</style>", tabbed_css + "\n</style>", 1)

# Tab bar
html += '<div class="yr-tab-bar">\n'
for yr in ["2024", "2025", "2026"]:
    active = "active" if yr == "2025" else ""
    html += f'  <button class="yr-tab {active}" onclick="showYear(\'{yr}\')" id="tab-{yr}">{YEAR_LABELS[yr]}</button>\n'
html += '</div>\n'

# Tab panels — wrap each year's chart scripts in lazy init functions
for yr in ["2024", "2025", "2026"]:
    display = "block" if yr == "2025" else "none"
    html += f'<div class="yr-panel" id="panel-{yr}" style="display:{display}>\n'
    body = bodies[yr]
    def _wrap(m, _yr=yr):
        inner = m.group(0)[len("<script>\n"):-len("</script>")]
        return f"<script>\nfunction initCharts_{_yr}() {{\n{inner}\n}}\n</script>"
    body = re.sub(r'<script>\nconst cc = .*?</script>', _wrap, body, flags=re.DOTALL)
    html += body
    html += '</div>\n'

html += f'<script>\n{tabbed_js}\n</script>\n'
html += gd.HTML_FOOT

out_path = out_dir / "WoofGang_PortWashington_AllYears_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"\nSaved: {out_path}")
