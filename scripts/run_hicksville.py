#!/usr/bin/env python3
"""
Run the full Woof Gang analysis pipeline for Hicksville, NY (#265).
Patches run.py config then executes extraction + Excel + dashboards.
"""
import time
from pathlib import Path

# Patch run.py config BEFORE importing pipeline functions
import run
run.TOKEN = "E57ACC082340B7FF58B5ABA5A99BE77D9501852A6C8F4D759A64D02EAED38ABE214A4026CA9A5D962ED5A141B7C061BC9BAA609365F8FE21F2F9EEF80DE04CA8"
run.LOCATION_ID = 205993
run.STORE_NAME = "Woof Gang Bakery & Grooming -- Hicksville, NY (#265)"
run.START_DATE = "2025-12-01"
run.END_DATE = "2026-03-06"
PROJ = Path(__file__).resolve().parent.parent
run.DATA_DIR = PROJ / "hicksville" / "data"
run.OUTPUT_DIR = PROJ / "hicksville"

# Also patch generate_dashboards.py imports
import generate_dashboards
generate_dashboards.STORE_NAME = run.STORE_NAME
generate_dashboards.DATA_DIR = run.DATA_DIR
generate_dashboards.START_DATE = run.START_DATE
generate_dashboards.END_DATE = run.END_DATE

if __name__ == "__main__":
    start_time = time.time()

    # 1. Extract data from FranPOS API
    raw_data = run.extract_all_data()

    # 2. Transform + generate Excel workbook
    transformed = run.transform_data(raw_data)
    run.generate_workbook(transformed)

    # 3. Generate HTML dashboards
    df, df_orders, emp_map = generate_dashboards.load_data()

    main_path = run.OUTPUT_DIR / "WoofGang_Hicksville_Dashboard.html"
    generate_dashboards.generate_main_dashboard(df, df_orders, main_path)

    price_path = run.OUTPUT_DIR / "WoofGang_Hicksville_PriceIncrease_Dashboard.html"
    generate_dashboards.generate_price_increase_dashboard(df, df_orders, price_path)

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.0f}s")
