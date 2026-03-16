"""
Data transformation: converts raw FranPOS JSON into analysis-ready DataFrames.
"""

import pandas as pd

from classifier import classify_item


def transform_data(raw):
    """Transform raw API data into analysis DataFrames."""
    print("\n" + "=" * 60)
    print("TRANSFORMING DATA")
    print("=" * 60)

    emp_map = {}
    for e in raw.get("employees", []):
        eid = e.get("Id")
        name = f"{e.get('FirstName', '')} {e.get('LastName', '')}".strip()
        emp_map[eid] = name

    items = raw.get("order_items", [])
    print(f"Processing {len(items)} line items...")

    rows = []
    for item in items:
        name = item.get("Name", "")
        sku = item.get("Sku", "")
        cls = classify_item(name, sku)

        net_sales = float(item.get("Price", 0)) * float(item.get("Quantity", 1)) - float(item.get("Discount", 0))

        rows.append({
            "order_id": item.get("OrderId"),
            "item_id": item.get("OrderItemId"),
            "customer_id": item.get("CustomerId"),
            "created": item.get("CreatedOn"),
            "name": name,
            "sku": sku,
            "price": float(item.get("Price", 0)),
            "quantity": float(item.get("Quantity", 1)),
            "discount": float(item.get("Discount", 0)),
            "net_sales": net_sales,
            "cost": float(item.get("Cost", 0)),
            "salesperson": item.get("SalesPerson", ""),
            **cls,
        })

    df_items = pd.DataFrame(rows)
    df_items["created"] = pd.to_datetime(df_items["created"], utc=True).dt.tz_convert("America/New_York")
    df_items["month"] = df_items["created"].dt.month
    df_items["month_name"] = df_items["created"].dt.strftime("%b")
    df_items["day_of_week"] = df_items["created"].dt.day_name()

    orders = raw.get("orders", [])
    print(f"Processing {len(orders)} orders...")

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
        df_orders["created"] = pd.to_datetime(df_orders["created"], utc=True).dt.tz_convert("America/New_York")
        df_orders["month"] = df_orders["created"].dt.month
    else:
        print("  No orders from API — reconstructing from line items...")
        grp = df_items.groupby("order_id").agg(
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
        print(f"  Reconstructed {len(df_orders)} orders from line items")

    clocks = raw.get("time_clocks", [])
    clock_rows = []
    for c in clocks:
        if isinstance(c, dict):
            clock_rows.append({
                "employee_id": c.get("EmployeeId"),
                "employee_name": c.get("EmployeeName", ""),
                "clock_in": c.get("ClockIn"),
                "clock_out": c.get("ClockOut"),
                "hours": float(c.get("TotalHours", 0) or 0),
            })
    df_clocks = pd.DataFrame(clock_rows) if clock_rows else pd.DataFrame(columns=["employee_id", "employee_name", "hours"])

    print(f"Items: {len(df_items)}, Orders: {len(df_orders)}, Clock records: {len(df_clocks)}")

    df_valid = df_items[df_items["groom_category"] != "exclude"].copy()

    return {
        "df_items": df_valid,
        "df_orders": df_orders,
        "df_clocks": df_clocks,
        "emp_map": emp_map,
        "all_items": df_items,
    }
