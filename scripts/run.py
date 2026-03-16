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
    }
}
window.addEventListener('DOMContentLoaded', function() { showYear('2025'); });
"""

    html = gd.html_head("Woof Gang Port Washington", f"Store Performance Analysis \u00b7 Sales through {through}")
    html = html.replace("</style>", tabbed_css + "\n</style>", 1)
    html += '<div class="yr-tab-bar">\n'
    for yr in ["2024", "2025", "2026"]:
        active = "active" if yr == "2025" else ""
        html += f'  <button class="yr-tab {active}" onclick="showYear(\'{yr}\')" id="tab-{yr}">{YEAR_LABELS[yr]}</button>\n'
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

    html += f'<script>\n{tabbed_js}\n</script>\n'
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
