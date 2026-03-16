"""
Workbook generator: orchestrates analysis computation and Excel sheet writing.
"""

from pathlib import Path

import pandas as pd
import openpyxl

from analysis import (
    compute_executive_summary, compute_service_mix, compute_dog_sizes,
    compute_grooming_skus, compute_top_retail, compute_category_top10s,
    compute_brand_breakdown, compute_monthly_performance,
    compute_top_customers, compute_customer_intelligence, compute_team_performance,
)
from excel_writers import (
    write_executive_summary, write_service_mix, write_dog_sizes,
    write_grooming_skus, write_top_retail, write_category_top10s,
    write_brand_breakdown, write_monthly_performance,
    write_top_customers, write_customer_intelligence, write_team_performance,
)
from formatting import fmt_currency, fmt_int


def generate_workbook(transformed, store):
    """Generate the full Excel analysis workbook."""
    print("\n" + "=" * 60)
    print("GENERATING EXCEL WORKBOOK")
    print("=" * 60)

    df = transformed["df_items"]
    df_orders = transformed["df_orders"]
    df_clocks = transformed["df_clocks"]
    store_name = store.name
    start_date = store.start_date
    end_date = store.end_date

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    print("  [1/10] Executive Summary...")
    summary = compute_executive_summary(df, df_orders)
    write_executive_summary(wb, summary, df, store_name=store_name, start_date=start_date, end_date=end_date)

    print("  [2/10] Service Type Mix...")
    mix = compute_service_mix(df)
    if not mix.empty:
        write_service_mix(wb, mix)

    print("  [3/10] Dog Size Breakdown...")
    all_sizes, std_sizes, dood_sizes = compute_dog_sizes(df)
    if isinstance(all_sizes, pd.DataFrame) and not all_sizes.empty:
        write_dog_sizes(wb, all_sizes, std_sizes, dood_sizes)

    print("  [4/10] Grooming SKU Detail...")
    groom_skus = compute_grooming_skus(df)
    if not groom_skus.empty:
        write_grooming_skus(wb, groom_skus)

    print("  [5/10] Top 50 Retail...")
    top_rev, top_units = compute_top_retail(df)
    if not top_rev.empty:
        write_top_retail(wb, top_rev, top_units)

    print("  [6/10] Category Top 10s...")
    cat_data = compute_category_top10s(df)
    if cat_data:
        write_category_top10s(wb, cat_data)

    print("  [7/10] Brand Breakdown...")
    brand_data = compute_brand_breakdown(df)
    if brand_data:
        write_brand_breakdown(wb, brand_data)

    print("  [8/10] Monthly Performance...")
    monthly = compute_monthly_performance(df, df_orders)
    write_monthly_performance(wb, monthly, store_name=store_name, start_date=start_date, end_date=end_date)

    print("  [9/10] Top Customers...")
    top_cust = compute_top_customers(df, df_orders)
    write_top_customers(wb, top_cust)

    print("  [10/10] Customer Intelligence...")
    ci = compute_customer_intelligence(df, df_orders)
    write_customer_intelligence(wb, ci, store_name=store_name, start_date=start_date, end_date=end_date)

    print("  [11/11] Team Performance...")
    team = compute_team_performance(df, df_orders, df_clocks)
    write_team_performance(wb, team)

    year_range = f"{start_date[:4]}" if start_date[:4] == end_date[:4] else f"{start_date[:4]}-{end_date[:4]}"
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)
    output_file = desktop / f"WoofGang_{store.short_name}_{year_range}_Analysis.xlsx"
    wb.save(output_file)
    print(f"\n{'=' * 60}")
    print(f"REPORT SAVED: {output_file}")
    print(f"{'=' * 60}")

    print(f"\nStore: {store_name}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Total Net Sales: {fmt_currency(summary['total_net_sales'])}")
    print(f"  Grooming: {fmt_currency(summary['grooming_revenue'])} ({summary['grooming_pct']:.1f}%)")
    print(f"  Retail: {fmt_currency(summary['retail_revenue'])} ({summary['retail_pct']:.1f}%)")
    print(f"Total Transactions: {fmt_int(summary['total_transactions'])}")
    print(f"Avg Transaction: {fmt_currency(summary['avg_transaction_value'])}")
    print(f"Sheets: {len(wb.sheetnames)}")

    return output_file
