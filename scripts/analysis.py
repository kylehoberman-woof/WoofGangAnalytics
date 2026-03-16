"""
Analysis functions: compute business metrics from transformed DataFrames.
"""

import pandas as pd


def compute_executive_summary(df, df_orders):
    groom = df[df["is_groom"] == True]
    retail = df[df["is_retail"] == True]
    gifts = df[df["is_gift_card"] == True]

    total_net = df["net_sales"].sum()
    total_gross = (df["price"] * df["quantity"]).sum()
    total_returns = df[df["net_sales"] < 0]["net_sales"].sum()
    total_discounts = df["discount"].sum()
    total_units = int(df["quantity"].sum())
    total_txns = df_orders.shape[0]
    unique_skus = df["sku"].nunique()
    avg_price = total_gross / total_units if total_units else 0
    avg_txn = total_net / total_txns if total_txns else 0

    return {
        "total_net_sales": total_net,
        "total_gross_sales": total_gross,
        "total_returns": total_returns,
        "total_discounts": total_discounts,
        "total_units": total_units,
        "total_transactions": total_txns,
        "total_skus": unique_skus,
        "avg_price_per_item": avg_price,
        "avg_transaction_value": avg_txn,
        "grooming_revenue": groom["net_sales"].sum(),
        "retail_revenue": retail["net_sales"].sum(),
        "gift_card_revenue": gifts["net_sales"].sum(),
        "grooming_pct": groom["net_sales"].sum() / total_net * 100 if total_net else 0,
        "retail_pct": retail["net_sales"].sum() / total_net * 100 if total_net else 0,
    }


def compute_service_mix(df):
    groom = df[df["is_groom"] == True].copy()
    if groom.empty:
        return pd.DataFrame()

    mix = groom.groupby("service_type").agg(
        units=("quantity", "sum"),
        revenue=("net_sales", "sum"),
        sku_count=("sku", "nunique"),
    ).reset_index()
    mix["pct_units"] = mix["units"] / mix["units"].sum() * 100
    mix["pct_revenue"] = mix["revenue"] / mix["revenue"].sum() * 100
    mix["avg_ticket"] = mix["revenue"] / mix["units"]
    mix = mix.sort_values("revenue", ascending=False)
    return mix


def compute_dog_sizes(df):
    core = df[(df["groom_category"] == "core") & (df["dog_size"].notna())].copy()
    if core.empty:
        return {}, {}, {}

    all_sizes = core.groupby("dog_size").agg(
        units=("quantity", "sum"),
        revenue=("net_sales", "sum"),
    ).reset_index()
    all_sizes["pct_units"] = all_sizes["units"] / all_sizes["units"].sum() * 100
    all_sizes["pct_revenue"] = all_sizes["revenue"] / all_sizes["revenue"].sum() * 100
    all_sizes["avg_ticket"] = all_sizes["revenue"] / all_sizes["units"]

    std = core[core["is_doodle"] == False]
    dood = core[core["is_doodle"] == True]

    std_sizes = std.groupby("dog_size").agg(
        units=("quantity", "sum"), revenue=("net_sales", "sum")
    ).reset_index()
    std_sizes["avg_ticket"] = std_sizes["revenue"] / std_sizes["units"]

    dood_sizes = dood.groupby("dog_size").agg(
        units=("quantity", "sum"), revenue=("net_sales", "sum")
    ).reset_index()
    dood_sizes["avg_ticket"] = dood_sizes["revenue"] / dood_sizes["units"]

    size_order = ["0-20 lbs", "21-40 lbs", "41-75 lbs", "76-100 lbs", "Over 100 lbs"]
    for frame in [all_sizes, std_sizes, dood_sizes]:
        frame["_order"] = frame["dog_size"].map({s: i for i, s in enumerate(size_order)})
        frame.sort_values("_order", inplace=True)
        frame.drop("_order", axis=1, inplace=True)

    return all_sizes, std_sizes, dood_sizes


def compute_monthly_performance(df, df_orders):
    groom = df[df["is_groom"] == True]
    retail = df[df["is_retail"] == True]

    df["_ym"] = df["created"].dt.to_period("M")
    if "created" in df_orders.columns:
        df_orders["_ym"] = df_orders["created"].dt.to_period("M")
    groom["_ym"] = groom["created"].dt.to_period("M")
    retail["_ym"] = retail["created"].dt.to_period("M")

    all_periods = sorted(df["_ym"].dropna().unique())

    months = []
    for p in all_periods:
        m_groom = groom[groom["_ym"] == p]
        m_retail = retail[retail["_ym"] == p]
        m_orders = df_orders[df_orders["_ym"] == p] if "_ym" in df_orders.columns else pd.DataFrame()

        groom_rev = m_groom["net_sales"].sum()
        retail_rev = m_retail["net_sales"].sum()
        total_rev = groom_rev + retail_rev
        tickets = m_orders.shape[0]

        months.append({
            "month": p.month,
            "month_name": p.strftime("%b %Y") if len(all_periods) > 12 or p.year != all_periods[0].year else p.strftime("%b"),
            "net_revenue": total_rev,
            "grooming_rev": groom_rev,
            "retail_rev": retail_rev,
            "groom_pct": groom_rev / total_rev * 100 if total_rev else 0,
            "retail_pct": retail_rev / total_rev * 100 if total_rev else 0,
            "tickets": tickets,
            "avg_ticket": total_rev / tickets if tickets else 0,
        })

    df.drop("_ym", axis=1, inplace=True, errors="ignore")
    df_orders.drop("_ym", axis=1, inplace=True, errors="ignore")

    df_monthly = pd.DataFrame(months)
    df_monthly["mom_growth"] = df_monthly["net_revenue"].pct_change() * 100
    return df_monthly


def compute_top_retail(df, n=50):
    retail = df[df["is_retail"] == True].copy()
    if retail.empty:
        return pd.DataFrame(), pd.DataFrame()

    total_retail = retail["net_sales"].sum()

    by_rev = retail.groupby(["sku", "name", "retail_category"]).agg(
        units=("quantity", "sum"),
        net_sales=("net_sales", "sum"),
    ).reset_index()
    by_rev["pct_retail"] = by_rev["net_sales"] / total_retail * 100
    by_rev["avg_price"] = by_rev["net_sales"] / by_rev["units"]

    top_by_rev = by_rev.sort_values("net_sales", ascending=False).head(n).reset_index(drop=True)
    top_by_rev.index = top_by_rev.index + 1

    top_by_units = by_rev.sort_values("units", ascending=False).head(n).reset_index(drop=True)
    top_by_units.index = top_by_units.index + 1

    return top_by_rev, top_by_units


def compute_grooming_skus(df):
    groom = df[df["is_groom"] == True].copy()
    if groom.empty:
        return pd.DataFrame()

    by_sku = groom.groupby(["sku", "name", "service_type"]).agg(
        units=("quantity", "sum"),
        net_sales=("net_sales", "sum"),
    ).reset_index()
    total_groom = groom["net_sales"].sum()
    by_sku["pct_mix"] = by_sku["net_sales"] / total_groom * 100
    by_sku["avg_ticket"] = by_sku["net_sales"] / by_sku["units"]
    by_sku = by_sku.sort_values("net_sales", ascending=False).reset_index(drop=True)
    by_sku.index = by_sku.index + 1
    return by_sku


def compute_category_top10s(df):
    retail = df[df["is_retail"] == True].copy()
    if retail.empty:
        return {}

    results = {}
    for cat in retail["retail_category"].unique():
        if cat in ("Other", None):
            continue
        cat_df = retail[retail["retail_category"] == cat]
        cat_total = cat_df["net_sales"].sum()

        by_product = cat_df.groupby(["name"]).agg(
            units=("quantity", "sum"),
            revenue=("net_sales", "sum"),
        ).reset_index()

        top_rev = by_product.sort_values("revenue", ascending=False).head(10)
        top_units = by_product.sort_values("units", ascending=False).head(10)

        results[cat] = {
            "total": cat_total,
            "top_by_revenue": top_rev,
            "top_by_units": top_units,
        }

    return results


def compute_brand_breakdown(df):
    retail = df[df["is_retail"] == True].copy()
    if retail.empty:
        return {}

    def extract_brand(name):
        n = (name or "").strip()
        brands = [
            ("WGB ", "WGB / Woof Gang"), ("Woof Gang", "WGB / Woof Gang"),
            ("Farmina", "Farmina"), ("KONG ", "KONG"), ("Kong ", "KONG"),
            ("Tall Tails", "Tall Tails"), ("Multipet", "Multipet"),
            ("BIXBI", "BIXBI"), ("Vital Essentials", "Vital Essentials"),
            ("Himalayan", "Himalayan"), ("H&K", "H&K / Haute Diggity Dog"),
            ("Haute Diggity", "H&K / Haute Diggity Dog"),
            ("PlaqueOff", "PlaqueOff"), ("Nootie", "Nootie"),
            ("goDog", "goDog"), ("Pets First", "Pets First"),
            ("Earth Animal", "Earth Animal"), ("Grandma Lucy", "Grandma Lucy's"),
            ("Puppy Cake", "Puppy Cake"), ("Swell Gelato", "Swell Gelato"),
            ("Lucky B", "Lucky B"), ("Preppy Puppy", "Preppy Puppy"),
            ("Birdie Dawg", "Birdie Dawg"),
            ("Cookie", "Store/Bakery Items"), ("Bakery Bulk", "Store/Bakery Items"),
            ("Mini Birthday", "Store/Bakery Items"), ("Birthday Cupcake", "Store/Bakery Items"),
            ("Mini PB", "Store/Bakery Items"),
            ("Wunderball", "Wunderball"),
        ]
        for prefix, brand in brands:
            if prefix.lower() in n.lower():
                return brand
        if n.startswith("Miscellaneous"):
            return "Other/Unbranded"
        return "Other/Unbranded"

    retail["brand"] = retail["name"].apply(extract_brand)

    results = {}
    for cat in retail["retail_category"].unique():
        if cat in ("Other", None):
            continue
        cat_df = retail[retail["retail_category"] == cat]
        cat_total = cat_df["net_sales"].sum()

        by_brand = cat_df.groupby("brand").agg(
            net_sales=("net_sales", "sum"),
            units=("quantity", "sum"),
            sku_count=("sku", "nunique"),
        ).reset_index()
        by_brand["pct_category"] = by_brand["net_sales"] / cat_total * 100
        by_brand["avg_price"] = by_brand["net_sales"] / by_brand["units"]
        by_brand["revenue_per_sku"] = by_brand["net_sales"] / by_brand["sku_count"]
        by_brand = by_brand.sort_values("net_sales", ascending=False).head(10)

        results[cat] = {"total": cat_total, "brands": by_brand}

    return results


def compute_top_customers(df, df_orders):
    order_summary = df_orders.groupby("customer_id").agg(
        total_spend=("subtotal", "sum"),
        transactions=("order_id", "count"),
        visit_days=("created", lambda x: x.dt.date.nunique()),
    ).reset_index()
    order_summary["spend_per_visit"] = order_summary["total_spend"] / order_summary["visit_days"]
    order_summary["avg_txn"] = order_summary["total_spend"] / order_summary["transactions"]

    def tier(row):
        spv = row["spend_per_visit"]
        if spv >= 150: return "Premium"
        elif spv >= 100: return "High"
        elif spv >= 60: return "Standard"
        return "Value"
    order_summary["tier"] = order_summary.apply(tier, axis=1)

    top = order_summary.sort_values("total_spend", ascending=False).head(50)
    return top


def compute_customer_intelligence(df, df_orders):
    order_items_agg = df.groupby("order_id").agg(
        has_groom=("is_groom", "any"),
        has_retail=("is_retail", "any"),
        has_gift=("is_gift_card", "any"),
        total=("net_sales", "sum"),
    ).reset_index()

    def ticket_type(row):
        if row["has_groom"] and row["has_retail"]: return "Hybrid (Groom + Retail)"
        elif row["has_groom"]: return "Grooming Only"
        elif row["has_retail"]: return "Retail Only"
        elif row["has_gift"]: return "Gift Card Only"
        return "Other"

    order_items_agg["ticket_type"] = order_items_agg.apply(ticket_type, axis=1)

    ticket_comp = order_items_agg.groupby("ticket_type").agg(
        tickets=("order_id", "count"),
        revenue=("total", "sum"),
    ).reset_index()
    total_tickets = ticket_comp["tickets"].sum()
    total_rev = ticket_comp["revenue"].sum()
    ticket_comp["pct_tickets"] = ticket_comp["tickets"] / total_tickets * 100
    ticket_comp["pct_revenue"] = ticket_comp["revenue"] / total_rev * 100
    ticket_comp["avg_ticket"] = ticket_comp["revenue"] / ticket_comp["tickets"]

    named_orders = df_orders[df_orders["customer_id"] > 0]
    cust_visits = named_orders.groupby("customer_id").agg(
        visits=("created", lambda x: x.dt.date.nunique()),
        total_spend=("subtotal", "sum"),
    ).reset_index()

    def freq_bucket(v):
        if v == 1: return "1 visit (trial)"
        elif v == 2: return "2 visits"
        elif v <= 4: return "3-4 visits (developing)"
        elif v <= 8: return "5-8 visits (loyal)"
        elif v <= 12: return "9-12 visits (committed)"
        return "13+ visits (champions)"

    cust_visits["bucket"] = cust_visits["visits"].apply(freq_bucket)
    freq = cust_visits.groupby("bucket").agg(
        customers=("customer_id", "count"),
        revenue=("total_spend", "sum"),
    ).reset_index()
    freq["pct_customers"] = freq["customers"] / freq["customers"].sum() * 100
    freq["pct_revenue"] = freq["revenue"] / freq["revenue"].sum() * 100
    freq["avg_spend"] = freq["revenue"] / freq["customers"]

    end_date = pd.Timestamp.now().normalize()
    last_visit = named_orders.groupby("customer_id")["created"].max().reset_index()
    last_visit["created"] = last_visit["created"].dt.tz_localize(None) if last_visit["created"].dt.tz is not None else last_visit["created"]
    last_visit["days_since"] = (end_date - last_visit["created"]).dt.days
    cust_spend = named_orders.groupby("customer_id")["subtotal"].sum().reset_index()
    last_visit = last_visit.merge(cust_spend, on="customer_id")

    def churn_segment(days):
        if days <= 30: return "Active (last 30 days)"
        elif days <= 60: return "Recent (31-60 days)"
        elif days <= 90: return "At Risk (61-90 days)"
        elif days <= 180: return "Lapsing (91-180 days)"
        return "Lost (180+ days)"

    last_visit["segment"] = last_visit["days_since"].apply(churn_segment)
    churn = last_visit.groupby("segment").agg(
        customers=("customer_id", "count"),
        total_spend=("subtotal", "sum"),
    ).reset_index()
    churn["pct"] = churn["customers"] / churn["customers"].sum() * 100
    churn["avg_spend"] = churn["total_spend"] / churn["customers"]

    dow_items = df.copy()
    dow_items["dow"] = dow_items["created"].dt.day_name()
    dow_orders = df_orders.copy()
    dow_orders["dow"] = dow_orders["created"].dt.day_name()

    dow_rev = dow_items.groupby("dow")["net_sales"].sum()
    dow_groom_rev = dow_items[dow_items["is_groom"]].groupby("dow")["net_sales"].sum()
    dow_retail_rev = dow_items[dow_items["is_retail"]].groupby("dow")["net_sales"].sum()
    dow_tickets = dow_orders.groupby("dow")["order_id"].count()

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_df = pd.DataFrame({"day": day_order})
    dow_df["revenue"] = dow_df["day"].map(dow_rev).fillna(0)
    dow_df["grooming_rev"] = dow_df["day"].map(dow_groom_rev).fillna(0)
    dow_df["retail_rev"] = dow_df["day"].map(dow_retail_rev).fillna(0)
    dow_df["tickets"] = dow_df["day"].map(dow_tickets).fillna(0).astype(int)
    total_week_rev = dow_df["revenue"].sum()
    dow_df["pct_week"] = dow_df["revenue"] / total_week_rev * 100
    dow_df["avg_ticket"] = dow_df["revenue"] / dow_df["tickets"].replace(0, 1)
    dow_df["retail_pct"] = dow_df["retail_rev"] / dow_df["revenue"].replace(0, 1) * 100

    return {
        "ticket_composition": ticket_comp,
        "visit_frequency": freq,
        "churn_risk": churn,
        "day_of_week": dow_df,
    }


def compute_team_performance(df, df_orders, df_clocks):
    groom = df[df["is_groom"] == True].copy()

    team = groom.groupby("salesperson").agg(
        revenue=("net_sales", "sum"),
        appointments=("order_id", "nunique"),
        items=("quantity", "sum"),
    ).reset_index()
    team = team[team["salesperson"] != ""].copy()
    team["per_appt"] = team["revenue"] / team["appointments"]
    team = team.sort_values("revenue", ascending=False)

    core = groom[groom["groom_category"] == "core"]
    spa = groom[groom["groom_category"] == "spa"]
    addon = groom[groom["groom_category"] == "addon"]

    core_by_groomer = core.groupby("salesperson")["order_id"].nunique().reset_index()
    core_by_groomer.columns = ["salesperson", "core_visits"]

    spa_by_groomer = spa.groupby("salesperson")["order_id"].nunique().reset_index()
    spa_by_groomer.columns = ["salesperson", "spa_orders"]

    addon_by_groomer = addon.groupby("salesperson")["order_id"].nunique().reset_index()
    addon_by_groomer.columns = ["salesperson", "addon_orders"]

    teeth = groom[groom["service_type"] == "Teeth Brushing"]
    teeth_by_groomer = teeth.groupby("salesperson")["order_id"].nunique().reset_index()
    teeth_by_groomer.columns = ["salesperson", "teeth_orders"]

    team = team.merge(core_by_groomer, on="salesperson", how="left")
    team = team.merge(spa_by_groomer, on="salesperson", how="left")
    team = team.merge(addon_by_groomer, on="salesperson", how="left")
    team = team.merge(teeth_by_groomer, on="salesperson", how="left")

    team["spa_orders"] = team["spa_orders"].fillna(0)
    team["addon_orders"] = team["addon_orders"].fillna(0)
    team["teeth_orders"] = team["teeth_orders"].fillna(0)
    team["core_visits"] = team["core_visits"].fillna(0)

    team["spa_pct"] = (team["spa_orders"] / team["core_visits"].replace(0, 1) * 100)
    team["addon_pct"] = (team["addon_orders"] / team["core_visits"].replace(0, 1) * 100)
    team["teeth_pct"] = (team["teeth_orders"] / team["core_visits"].replace(0, 1) * 100)

    if not df_clocks.empty:
        hours_by_emp = df_clocks.groupby("employee_name")["hours"].sum().reset_index()
        team = team.merge(hours_by_emp, left_on="salesperson", right_on="employee_name", how="left")
        team["per_hour"] = team["revenue"] / team["hours"].replace(0, float("nan"))
    else:
        team["hours"] = float("nan")
        team["per_hour"] = float("nan")

    return team
