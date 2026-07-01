#!/usr/bin/env python3
"""
Retail Products Dashboard — Woof Gang
Generates a standalone HTML dashboard showing top-selling retail products,
category trends, monthly bestsellers, weekly revenue trend, and brand performance.

Usage:
    python generate_retail_dashboard.py                # Port Washington
    python generate_retail_dashboard.py hicksville     # Hicksville
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, PORTAL_BACK_JS, get_store_display, get_store_fn, get_other_stores
from classifier import classify_item
import pandas as pd

# ── Store setup ───────────────────────────────────────────────────────────────
_sn   = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
_st   = get_store(_sn)
_disp = get_store_display(_sn)
_fn   = get_store_fn(_sn)
_other_keys = get_other_stores(_sn)
_ofn  = get_store_fn(_other_keys[0]) if _other_keys else ""
_odir = f"../{_other_keys[0]}" if _other_keys else ".."
DATA_DIR   = _st.data_dir
OUTPUT_DIR = _st.output_dir

# ── Load data ─────────────────────────────────────────────────────────────────
print(f"Loading data for {_disp}...")
with open(DATA_DIR / "all_data.json") as f:
    all_data = json.load(f)
_brands = {}
bp = DATA_DIR / "sku_brands.json"
if bp.exists():
    with open(bp) as f:
        _brands = json.load(f)

et = ZoneInfo("America/New_York")
now_str = datetime.now(et).strftime("%B %d, %Y at %I:%M %p ET")

# ── Build retail DataFrame ────────────────────────────────────────────────────
rows = []
for item in all_data["order_items"]:
    name = item.get("Name", "")
    sku  = str(item.get("Sku", ""))
    cls  = classify_item(name, sku)
    if not cls["is_retail"]:
        continue
    price = float(item.get("Price", 0))
    qty   = float(item.get("Quantity", 1))
    disc  = float(item.get("Discount", 0))
    net   = max(0, price * qty - disc)
    bi    = _brands.get(sku, {})
    brand = (bi.get("brand", "") or "") if isinstance(bi, dict) else ""
    rows.append({
        "sku":      sku,
        "name":     name[:55],
        "brand":    brand or "–",
        "category": cls["retail_category"] or "Other",
        "quantity": qty,
        "net_sales": net,
        "order_id": item.get("OrderId"),
        "created":  item.get("CreatedOn", ""),
    })

df = pd.DataFrame(rows)
if df.empty:
    print("No retail items found!")
    sys.exit(1)

df["created"] = pd.to_datetime(df["created"])
df["year"]  = df["created"].dt.year
df["ym"]    = df["created"].dt.to_period("M")
df["yw"]    = (df["created"].dt.isocalendar().year.astype(str) + "-W" +
               df["created"].dt.isocalendar().week.astype(str).str.zfill(2))

print(f"  Retail rows: {len(df):,}  |  Revenue: ${df['net_sales'].sum():,.0f}")

# ── KPI aggregations ─────────────────────────────────────────────────────────
YEAR = datetime.now().year
ytd  = df[df["year"] == YEAR]

total_rev   = df["net_sales"].sum()
ytd_rev     = ytd["net_sales"].sum()
total_units = df["quantity"].sum()
unique_skus = df["sku"].nunique()
avg_basket  = df.groupby("order_id")["net_sales"].sum().mean()

# ── Category palette ─────────────────────────────────────────────────────────
CAT_COLORS = {
    "Treats & Chews":      "#C4276E",
    "Natural Chews":       "#8B4513",
    "Toys":                "#1565C0",
    "Bakery & Birthday":   "#AD1457",
    "Apparel & Lifestyle": "#7B1FA2",
    "Grooming Supplies":   "#1B6B6B",
    "Supplements & Health":"#2E7D32",
    "Accessories":         "#FB8C00",
    "Wet Food":            "#0097A7",
    "Toppers & Mix-ins":   "#00838F",
    "Other":               "#9E9E9E",
}

# ── Category donut ───────────────────────────────────────────────────────────
cat_rev    = df.groupby("category")["net_sales"].sum().sort_values(ascending=False)
cat_labels = list(cat_rev.index)
cat_vals   = [round(float(v), 2) for v in cat_rev.values]
cat_colors = [CAT_COLORS.get(c, "#9E9E9E") for c in cat_labels]

# ── Monthly stacked bar — last 12 months, top 8 categories ───────────────────
all_periods = sorted(df["ym"].unique())
last_12     = all_periods[-12:]
m_labels    = [p.strftime("%b '%y") for p in last_12]
top8_cats   = cat_labels[:8]

m_data = (df[df["ym"].isin(last_12)]
          .groupby(["ym", "category"])["net_sales"].sum()
          .unstack(fill_value=0))

monthly_ds_parts = []
for cat in top8_cats:
    vals = []
    for p in last_12:
        v = float(m_data.loc[p, cat]) if (p in m_data.index and cat in m_data.columns) else 0.0
        vals.append(round(v, 2))
    c = CAT_COLORS.get(cat, "#9E9E9E")
    monthly_ds_parts.append(
        f'{{"label":{json.dumps(cat)},"data":{vals},"backgroundColor":"{c}","stack":"s"}}'
    )
monthly_datasets_js = "[" + ",".join(monthly_ds_parts) + "]"

# ── Top products by revenue ───────────────────────────────────────────────────
top_rev = (df.groupby(["sku", "name", "brand", "category"])
             .agg(revenue=("net_sales", "sum"), units=("quantity", "sum"),
                  orders=("order_id", "nunique"))
             .reset_index().sort_values("revenue", ascending=False).head(30))
top_rev["avg_price"] = (top_rev["revenue"] / top_rev["units"]).round(2)
ytd_sku = ytd.groupby("sku")["net_sales"].sum()
top_rev["ytd"] = top_rev["sku"].map(ytd_sku).fillna(0)

# ── Top products by units ─────────────────────────────────────────────────────
top_units_df = (df.groupby(["sku", "name", "brand", "category"])
                  .agg(units=("quantity", "sum"), revenue=("net_sales", "sum"))
                  .reset_index().sort_values("units", ascending=False).head(30))

# ── Monthly bestsellers — last 6 months ──────────────────────────────────────
last_6  = all_periods[-6:]
monthly_tops = {}
for m in last_6:
    mdf = df[df["ym"] == m]
    monthly_tops[m] = (mdf.groupby("name")["net_sales"].sum()
                          .reset_index().sort_values("net_sales", ascending=False).head(10))

# ── Weekly revenue trend — last 16 weeks ─────────────────────────────────────
all_weeks = sorted(df["yw"].unique())
last_16   = all_weeks[-16:]
weekly    = df[df["yw"].isin(last_16)].groupby("yw")["net_sales"].sum()
w_labels  = [w for w in last_16]  # full "2025-W12" strings for tooltips
w_vals    = [round(float(weekly.get(w, 0)), 2) for w in last_16]

# Short label: "W12" or "W3 '24" if crosses year boundary
def week_label(w):
    parts = w.split("-W")
    yr, wk = parts[0], parts[1].lstrip("0") or "0"
    yr_short = "'" + yr[2:]
    return f"W{wk} {yr_short}"

w_short_labels = [week_label(w) for w in last_16]

# ── Brand performance ─────────────────────────────────────────────────────────
brand_p = (df[df["brand"] != "–"]
             .groupby("brand")
             .agg(revenue=("net_sales", "sum"), units=("quantity", "sum"),
                  skus=("sku", "nunique"))
             .reset_index().sort_values("revenue", ascending=False).head(20))
brand_p["avg_price"] = (brand_p["revenue"] / brand_p["units"]).round(2)

# ── Monthly SKU matrix — top 200 SKUs × last 12 months ───────────────────────
matrix_src = df[df["ym"].isin(last_12)]
top200_rev = (matrix_src.groupby(["sku", "name", "category"])
              .agg(total_rev=("net_sales", "sum"), total_units=("quantity", "sum"))
              .reset_index().sort_values("total_rev", ascending=False).head(200))
top200_set = set(top200_rev["sku"])
m_src = matrix_src[matrix_src["sku"].isin(top200_set)]
rev_piv   = m_src.groupby(["sku", "ym"])["net_sales"].sum().unstack(fill_value=0)
units_piv = m_src.groupby(["sku", "ym"])["quantity"].sum().unstack(fill_value=0)

matrix_rows = []
for _, r in top200_rev.iterrows():
    s = r["sku"]
    rev_mo   = [round(float(rev_piv.loc[s, p]),   2) if (s in rev_piv.index   and p in rev_piv.columns)   else 0.0 for p in last_12]
    units_mo = [round(float(units_piv.loc[s, p]), 1) if (s in units_piv.index and p in units_piv.columns) else 0.0 for p in last_12]
    matrix_rows.append({
        "sku": s, "name": r["name"], "category": r["category"],
        "total_rev": round(float(r["total_rev"]), 2),
        "total_units": round(float(r["total_units"]), 1),
        "rev": rev_mo, "units": units_mo,
    })
matrix_json        = json.dumps(matrix_rows)
matrix_months_json = json.dumps(m_labels)

# ── Key Insights ──────────────────────────────────────────────────────────────
_cur_per   = pd.Period(datetime.now(et), "M")
last3_per  = [_cur_per - i for i in range(3)]
prior3_per = [_cur_per - 3 - i for i in range(3)]
older_per  = [_cur_per - 3 - i for i in range(9)]

def _wrev(periods):
    return df[df["ym"].isin(periods)].groupby("sku")["net_sales"].sum()

wl3 = _wrev(last3_per)
wp3 = _wrev(prior3_per)
wol = _wrev(older_per)

all_skus_df = df.groupby(["sku", "name", "category"])["net_sales"].sum().reset_index()
insights_rising, insights_fading, insights_stars = [], [], []

for _, row in all_skus_df.iterrows():
    s = row["sku"]
    name, cat = row["name"], row["category"]
    rl3 = float(wl3.get(s, 0))
    rp3 = float(wp3.get(s, 0))
    if rl3 >= 50 and rp3 > 0 and (rl3 - rp3) / rp3 >= 0.30:
        pct = (rl3 - rp3) / rp3 * 100
        insights_rising.append({"name": name, "category": cat,
                                  "metric": f"+{pct:.0f}% vs prior 3mo", "rev3": round(rl3, 2)})
    if rl3 >= 100 and (rp3 + float(wol.get(s, 0))) < 20:
        insights_stars.append({"name": name, "category": cat,
                                 "metric": f"${rl3:,.0f} in last 3mo (new!)", "rev3": round(rl3, 2)})

older_top50 = (df[df["ym"].isin(older_per)]
               .groupby(["sku", "name", "category"])["net_sales"]
               .sum().reset_index().sort_values("net_sales", ascending=False).head(50))
for _, row in older_top50.iterrows():
    s   = row["sku"]
    rl3 = float(wl3.get(s, 0))
    rp3 = float(wp3.get(s, 0))
    baseline = rp3 if rp3 > 10 else float(row["net_sales"]) / 3
    if baseline > 10 and rl3 < baseline * 0.5:
        drop = (baseline - rl3) / baseline * 100
        insights_fading.append({"name": row["name"], "category": row["category"],
                                  "metric": f"-{drop:.0f}% vs prior avg", "rev3": round(rl3, 2)})

insights_rising.sort(key=lambda x: x["rev3"], reverse=True)
insights_fading.sort(key=lambda x: x["rev3"])
insights_stars.sort(key=lambda x: x["rev3"], reverse=True)
insights_rising = insights_rising[:15]
insights_fading = insights_fading[:15]
insights_stars  = insights_stars[:15]

# ── Helpers ───────────────────────────────────────────────────────────────────
def fc(v):
    return f"${v:,.0f}"

def fp(v):
    return f"${v:,.2f}"

def cat_pill(cat):
    c = CAT_COLORS.get(cat, "#9E9E9E")
    return f'<span class="cat-pill" style="background:{c}22;color:{c}">{cat}</span>'

# ── Build table rows ──────────────────────────────────────────────────────────
def build_rev_rows():
    out = []
    for i, (_, r) in enumerate(top_rev.iterrows()):
        out.append(
            f'<tr>'
            f'<td class="rank">{i+1}</td>'
            f'<td class="name-cell">{r["name"]}</td>'
            f'<td class="brand-cell">{r["brand"]}</td>'
            f'<td>{cat_pill(r["category"])}</td>'
            f'<td class="num">{fc(r["revenue"])}</td>'
            f'<td class="num">{fc(r["ytd"])}</td>'
            f'<td class="num">{r["units"]:.0f}</td>'
            f'<td class="num">{fp(r["avg_price"])}</td>'
            f'</tr>'
        )
    return "\n".join(out)

def build_units_rows():
    out = []
    for i, (_, r) in enumerate(top_units_df.iterrows()):
        out.append(
            f'<tr>'
            f'<td class="rank">{i+1}</td>'
            f'<td class="name-cell">{r["name"]}</td>'
            f'<td class="brand-cell">{r["brand"]}</td>'
            f'<td>{cat_pill(r["category"])}</td>'
            f'<td class="num">{r["units"]:.0f}</td>'
            f'<td class="num">{fc(r["revenue"])}</td>'
            f'</tr>'
        )
    return "\n".join(out)

def build_monthly_html():
    parts = []
    for m in last_6:
        mdf = monthly_tops[m]
        mstr = m.strftime("%B %Y")
        r_html = ""
        for rank, (_, row) in enumerate(mdf.iterrows()):
            r_html += (f'<tr><td class="rank">{rank+1}</td>'
                       f'<td class="name-cell">{row["name"][:38]}</td>'
                       f'<td class="num">{fc(row["net_sales"])}</td></tr>')
        parts.append(
            f'<div class="month-card">'
            f'<div class="month-title">{mstr}</div>'
            f'<table><thead><tr><th>#</th><th>Product</th><th class="num">Revenue</th></tr></thead>'
            f'<tbody>{r_html}</tbody></table>'
            f'</div>'
        )
    return "\n".join(parts)

def build_brand_rows():
    if brand_p.empty:
        return '<tr><td colspan="6" style="text-align:center;color:#999">No brand data available</td></tr>'
    max_rev = float(brand_p["revenue"].max())
    out = []
    for i, (_, r) in enumerate(brand_p.iterrows()):
        bar_pct = round(float(r["revenue"]) / max_rev * 100, 1)
        out.append(
            f'<tr>'
            f'<td class="rank">{i+1}</td>'
            f'<td class="brand-cell">{r["brand"]}</td>'
            f'<td class="num">{fc(r["revenue"])}</td>'
            f'<td><div class="bar-bg"><div class="bar-fill" style="width:{bar_pct}%"></div></div></td>'
            f'<td class="num">{r["units"]:.0f}</td>'
            f'<td class="num">{r["skus"]}</td>'
            f'<td class="num">{fp(r["avg_price"])}</td>'
            f'</tr>'
        )
    return "\n".join(out)

def build_insight_cards():
    def irows(items, empty_msg):
        if not items:
            return f'<div class="insight-empty">{empty_msg}</div>'
        rows = []
        for it in items:
            rows.append(
                f'<div class="insight-row">'
                f'<div class="insight-name" title="{it["name"]}">{it["name"][:50]}</div>'
                f'<div class="insight-meta">{cat_pill(it["category"])}'
                f'<span class="insight-metric">{it["metric"]}</span></div>'
                f'<div class="insight-rev">{fc(it["rev3"])}</div>'
                f'</div>'
            )
        return "\n".join(rows)

    cards_data = [
        ("rising", "📈 Rising",    insights_rising,
         "Last 3mo revenue ≥30% above prior 3mo",
         "No rising products detected this period."),
        ("fading", "📉 Fading",    insights_fading,
         "Former top sellers down ≥50% in the last 3 months",
         "No fading products detected this period."),
        ("stars",  "⭐ New Stars", insights_stars,
         "Little prior history but ≥$100 in the last 3 months",
         "No new stars detected this period."),
    ]
    parts = []
    for cid, title, items, desc, empty_msg in cards_data:
        badge = f'<span class="insight-badge">{len(items)}</span>' if items else ''
        parts.append(
            f'<div class="insight-card">'
            f'<button class="insight-toggle" onclick="toggleInsight(\'{cid}\')">'
            f'<span class="insight-title">{title}{badge}</span>'
            f'<span class="insight-desc">{desc}</span>'
            f'<span class="insight-chevron" id="chev-{cid}">▼</span>'
            f'</button>'
            f'<div class="insight-body" id="ins-{cid}" style="display:none">'
            f'{irows(items, empty_msg)}'
            f'</div></div>'
        )
    return "\n".join(parts)

rev_rows_html    = build_rev_rows()
units_rows_html  = build_units_rows()
monthly_html     = build_monthly_html()
brand_rows_html  = build_brand_rows()
insight_cards_html = build_insight_cards()

# ── Portal back / switch links ────────────────────────────────────────────────
_switch_url  = f"{_odir}/WoofGang_{_ofn}_Retail_Dashboard.html" if _other_keys else ""
_switch_name = get_store_display(_other_keys[0]) if _other_keys else ""
_home_url    = "../index.html"

# ── CSS ───────────────────────────────────────────────────────────────────────
CSS = """
:root{--m:#C4276E;--ml:#FDF0F5;--t:#1B6B6B;--dk:#1a1a2e;--tx:#1f2937;--mu:#6b7280;--bd:#e5e7eb;--bg:#f8f9fb;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--tx);}
.header{background:linear-gradient(135deg,#C4276E 0%,#6B3520 100%);color:white;padding:36px 0 28px;text-align:center;position:relative;}
.header::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(255,255,255,0.08) 0%,transparent 50%)}
.header h1{font-size:2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px;}
.header .subtitle{font-size:0.95rem;font-weight:400;opacity:0.9;position:relative}
.header .brand-tag{display:inline-block;background:rgba(255,255,255,0.2);color:white;padding:4px 14px;border-radius:20px;font-size:0.72rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;margin-top:10px;position:relative}
.header-timestamp{position:absolute;top:12px;right:20px;font-size:0.75rem;opacity:0.8;font-weight:400;z-index:1}
.kpi-bar{display:grid;grid-template-columns:repeat(5,1fr);background:white;border-bottom:1px solid var(--bd);box-shadow:0 2px 8px rgba(0,0,0,0.05);}
.kpi{padding:20px 24px;border-right:1px solid var(--bd);text-align:center;}
.kpi:last-child{border-right:none;}
.kpi .v{font-size:28px;font-weight:800;color:var(--m);line-height:1.1;}
.kpi .l{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-top:5px;color:var(--mu);}
.kpi .sub{font-size:11px;color:var(--mu);margin-top:2px;}
.container{max-width:1200px;margin:0 auto;padding:32px 24px 60px;}
.section{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.05);margin-bottom:28px;overflow:hidden;}
.section-hdr{padding:20px 28px 0;border-bottom:1px solid var(--bd);padding-bottom:16px;}
.section-hdr h2{font-size:15px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--mu);}
.section-body{padding:24px 28px;}
.charts-row{display:grid;grid-template-columns:340px 1fr;gap:24px;align-items:start;}
.chart-box{padding:0;}
.chart-box h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--mu);margin-bottom:12px;}
.tab-bar{display:flex;gap:0;padding:0 28px;border-bottom:2px solid var(--bd);background:white;}
.tb{padding:12px 18px;border:none;background:none;font-family:inherit;font-size:13px;font-weight:600;color:var(--mu);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;transition:all .15s;}
.tb.active{color:var(--m);border-bottom-color:var(--m);}
.panel{display:none;padding:0 28px 24px;}.panel.active{display:block;}
table{width:100%;border-collapse:collapse;font-size:13px;}
thead th{background:var(--dk);color:rgba(255,255,255,.8);padding:10px 12px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;position:sticky;top:0;z-index:10;}
thead th.num{text-align:right;}
tbody tr{border-bottom:1px solid #f3f4f6;}
tbody tr:hover{background:#fdf0f5;}
td{padding:9px 12px;vertical-align:middle;}
td.num{text-align:right;font-family:'DM Mono',monospace;font-size:12px;}
td.rank{font-size:11px;color:var(--mu);font-weight:700;width:32px;}
td.name-cell{font-weight:500;max-width:280px;}
td.brand-cell{font-size:12px;color:var(--mu);max-width:140px;}
.cat-pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap;}
.monthly-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;}
.month-card{border:1px solid var(--bd);border-radius:10px;overflow:hidden;}
.month-card table{font-size:12px;}
.month-card thead th{background:#f8f9fb;color:var(--mu);font-size:10px;}
.month-title{padding:10px 14px;font-weight:700;font-size:13px;background:var(--m);color:white;}
.bar-bg{background:#f3f4f6;border-radius:4px;height:8px;min-width:80px;}
.bar-fill{background:var(--m);border-radius:4px;height:8px;}
.footer{text-align:center;padding:24px;font-size:0.78rem;color:#999;}
.footer strong{color:var(--m);}
@media(max-width:900px){.charts-row{grid-template-columns:1fr;}.monthly-grid{grid-template-columns:1fr 1fr;}.kpi-bar{grid-template-columns:repeat(3,1fr);}}
@media(max-width:600px){.kpi-bar{grid-template-columns:1fr 1fr;}.monthly-grid{grid-template-columns:1fr;}}
/* ── SKU Matrix ───────────────────────────────────────────────────────────── */
.matrix-controls{display:flex;align-items:center;gap:12px;padding:16px 28px 0;flex-wrap:wrap;}
.matrix-search{flex:1;min-width:180px;max-width:320px;padding:7px 12px;border:1px solid var(--bd);border-radius:6px;font-size:13px;font-family:inherit;}
.matrix-search:focus{outline:none;border-color:var(--m);box-shadow:0 0 0 3px rgba(196,39,110,0.08);}
.matrix-toggle{display:flex;border:1px solid var(--bd);border-radius:6px;overflow:hidden;}
.matrix-toggle button{padding:6px 16px;border:none;background:white;font-size:12px;font-weight:600;font-family:inherit;cursor:pointer;color:var(--mu);transition:all .15s;}
.matrix-toggle button.active{background:var(--m);color:white;}
.matrix-count{font-size:11px;color:var(--mu);padding:6px 28px 0;}
.matrix-wrap{overflow-x:auto;overflow-y:auto;max-height:600px;margin-top:8px;position:relative;}
.matrix-table{border-collapse:collapse;font-size:12px;width:max-content;min-width:100%;}
.matrix-table thead th{background:var(--dk);color:rgba(255,255,255,.85);padding:9px 10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;white-space:nowrap;position:sticky;top:0;z-index:20;cursor:pointer;user-select:none;}
.matrix-table thead th:hover{background:#2d3050;}
.matrix-table thead th.sort-asc::after{content:' ↑';}
.matrix-table thead th.sort-desc::after{content:' ↓';}
.matrix-table th.col-name{position:sticky;left:0;z-index:30;min-width:200px;max-width:240px;}
.matrix-table td{padding:7px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid #f3f4f6;font-family:'DM Mono',monospace;font-size:12px;}
.matrix-table td.col-name{position:sticky;left:0;z-index:10;background:white;font-weight:500;font-family:inherit;max-width:240px;padding:7px 12px;border-right:2px solid var(--bd);text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.matrix-table td.col-total{font-weight:700;border-left:2px solid var(--bd);}
.matrix-table tbody tr:hover td.col-name{background:#fdf0f5;}
/* ── Key Insights ─────────────────────────────────────────────────────────── */
.insight-card{border:1px solid var(--bd);border-radius:10px;overflow:hidden;margin-bottom:12px;}
.insight-card:last-child{margin-bottom:0;}
.insight-toggle{width:100%;text-align:left;background:white;border:none;padding:16px 20px;cursor:pointer;display:flex;align-items:center;gap:10px;transition:background .15s;}
.insight-toggle:hover{background:var(--ml);}
.insight-title{font-size:14px;font-weight:700;color:var(--tx);white-space:nowrap;}
.insight-badge{display:inline-block;background:var(--m);color:white;font-size:10px;font-weight:700;border-radius:20px;padding:1px 7px;margin-left:6px;vertical-align:middle;}
.insight-desc{font-size:11px;color:var(--mu);flex:1;}
.insight-chevron{font-size:11px;color:var(--mu);transition:transform .2s;flex-shrink:0;}
.insight-chevron.open{transform:rotate(180deg);}
.insight-body{border-top:1px solid var(--bd);}
.insight-row{display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid #f3f4f6;flex-wrap:wrap;}
.insight-row:last-child{border-bottom:none;}
.insight-name{font-size:13px;font-weight:500;flex:1;min-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.insight-meta{display:flex;align-items:center;gap:8px;flex-shrink:0;}
.insight-metric{font-size:11px;font-weight:700;color:var(--m);white-space:nowrap;}
.insight-rev{font-size:12px;font-family:'DM Mono',monospace;color:var(--mu);white-space:nowrap;text-align:right;flex-shrink:0;}
.insight-empty{padding:20px;text-align:center;color:var(--mu);font-size:13px;font-style:italic;}
"""

# ── HTML ──────────────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Woof Gang {_disp} — Retail Products</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{CSS}</style>
</head>
<body>

<div class="header">
  <div class="header-timestamp">Updated {now_str} &nbsp;|&nbsp; <a href="{_switch_url}" style="color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.78rem">&#x21C4; {_switch_name}</a></div>
  <a id="portal-back" href="{_home_url}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.83rem;font-weight:600;display:inline-block;margin-bottom:8px;">&larr; Home</a>
  {PORTAL_BACK_JS}
  <h1>Woof Gang {_disp}</h1>
  <div class="subtitle">Retail Products Dashboard</div>
  <div class="brand-tag">Woof Gang Bakery &amp; Grooming</div>
</div>

<div class="kpi-bar">
  <div class="kpi"><div class="v">{fc(total_rev)}</div><div class="l">Total Retail Revenue</div><div class="sub">All Time</div></div>
  <div class="kpi"><div class="v">{fc(ytd_rev)}</div><div class="l">Retail Revenue</div><div class="sub">{YEAR} YTD</div></div>
  <div class="kpi"><div class="v">{total_units:,.0f}</div><div class="l">Units Sold</div><div class="sub">All Time</div></div>
  <div class="kpi"><div class="v">{unique_skus:,}</div><div class="l">Unique Products</div><div class="sub">All Time</div></div>
  <div class="kpi"><div class="v">{fp(avg_basket)}</div><div class="l">Avg Retail / Order</div><div class="sub">With retail items</div></div>
</div>

<div class="container">

  <!-- KEY INSIGHTS -->
  <div class="section">
    <div class="section-hdr"><h2>Key Insights</h2></div>
    <div class="section-body">
      {insight_cards_html}
    </div>
  </div>

  <!-- CATEGORY OVERVIEW -->
  <div class="section">
    <div class="section-hdr"><h2>Category Overview</h2></div>
    <div class="section-body">
      <div class="charts-row">
        <div class="chart-box">
          <h3>Revenue Mix — All Time</h3>
          <div style="position:relative;height:280px"><canvas id="catDonut"></canvas></div>
        </div>
        <div class="chart-box">
          <h3>Monthly Revenue by Category — Last 12 Months</h3>
          <div style="position:relative;height:280px"><canvas id="monthlyBar"></canvas></div>
        </div>
      </div>
    </div>
  </div>

  <!-- TOP PRODUCTS -->
  <div class="section">
    <div class="section-hdr"><h2>Top Products</h2></div>
    <div class="tab-bar">
      <button class="tb active" onclick="switchTab('rev',this)">By Revenue</button>
      <button class="tb" onclick="switchTab('units',this)">By Units</button>
      <button class="tb" onclick="switchTab('monthly',this)">Monthly Bestsellers</button>
      <button class="tb" onclick="switchTab('matrix',this)">SKU Matrix</button>
    </div>

    <div id="p-rev" class="panel active">
      <div style="overflow-x:auto;margin-top:16px">
      <table>
        <thead><tr>
          <th>#</th><th>Product</th><th>Brand</th><th>Category</th>
          <th class="num">All-Time Rev</th><th class="num">{YEAR} YTD</th>
          <th class="num">Units</th><th class="num">Avg Price</th>
        </tr></thead>
        <tbody>{rev_rows_html}</tbody>
      </table>
      </div>
    </div>

    <div id="p-units" class="panel">
      <div style="overflow-x:auto;margin-top:16px">
      <table>
        <thead><tr>
          <th>#</th><th>Product</th><th>Brand</th><th>Category</th>
          <th class="num">Units Sold</th><th class="num">Revenue</th>
        </tr></thead>
        <tbody>{units_rows_html}</tbody>
      </table>
      </div>
    </div>

    <div id="p-monthly" class="panel">
      <div class="monthly-grid" style="margin-top:16px">
        {monthly_html}
      </div>
    </div>

    <div id="p-matrix" class="panel">
      <div class="matrix-controls">
        <input class="matrix-search" type="search" id="matrixSearch"
               placeholder="Search products..." oninput="filterMatrix()">
        <div class="matrix-toggle">
          <button id="btnRev" class="active" onclick="setMatrixMode('rev')">$ Revenue</button>
          <button id="btnUnits" onclick="setMatrixMode('units')">Units</button>
        </div>
      </div>
      <div class="matrix-count" id="matrixCount"></div>
      <div class="matrix-wrap">
        <table class="matrix-table">
          <thead id="matrixHead"></thead>
          <tbody id="matrixBody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- WEEKLY TREND -->
  <div class="section">
    <div class="section-hdr"><h2>Weekly Revenue Trend — Last 16 Weeks</h2></div>
    <div class="section-body">
      <div style="position:relative;height:160px"><canvas id="weeklyChart"></canvas></div>
    </div>
  </div>

  <!-- BRAND PERFORMANCE -->
  <div class="section">
    <div class="section-hdr"><h2>Brand Performance</h2></div>
    <div style="overflow-x:auto;padding:0 28px 24px">
    <table>
      <thead><tr>
        <th>#</th><th>Brand</th><th class="num">Revenue</th><th style="min-width:120px">Share</th>
        <th class="num">Units</th><th class="num">SKUs</th><th class="num">Avg Price</th>
      </tr></thead>
      <tbody>{brand_rows_html}</tbody>
    </table>
    </div>
  </div>

</div><!-- /container -->

<div class="footer">
  Generated {datetime.now().strftime("%B %d, %Y")} &mdash;
  <strong>Woof Gang Bakery &amp; Grooming</strong> &mdash; Operations Intelligence
</div>

<script>
// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(id, btn) {{
  document.querySelectorAll('.tb').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('p-' + id).classList.add('active');
  if (id === 'matrix' && !_mxBuilt) {{ _mxBuilt = true; _buildMxHead(); _renderMx(); }}
}}

// ── Category donut ────────────────────────────────────────────────────────────
new Chart(document.getElementById('catDonut'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(cat_labels)},
    datasets: [{{
      data: {cat_vals},
      backgroundColor: {json.dumps(cat_colors)},
      borderWidth: 2,
      borderColor: '#fff',
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'right', labels: {{ font: {{ size: 11 }}, padding: 10 }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' ' + ctx.label + ': $' + ctx.raw.toLocaleString('en-US', {{maximumFractionDigits:0}})
        }}
      }}
    }}
  }}
}});

// ── Monthly stacked bar ───────────────────────────────────────────────────────
new Chart(document.getElementById('monthlyBar'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(m_labels)},
    datasets: {monthly_datasets_js}
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ font: {{ size: 10 }}, padding: 8, boxWidth: 12 }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.raw.toLocaleString('en-US', {{maximumFractionDigits:0}})
        }}
      }}
    }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ stacked: true, ticks: {{ callback: v => '$' + (v/1000).toFixed(0) + 'k', font: {{ size: 10 }} }}, grid: {{ color: '#f3f4f6' }} }}
    }}
  }}
}});

// ── Weekly line chart ─────────────────────────────────────────────────────────
new Chart(document.getElementById('weeklyChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(w_short_labels)},
    datasets: [{{
      label: 'Weekly Retail Revenue',
      data: {w_vals},
      borderColor: '#C4276E',
      backgroundColor: 'rgba(196,39,110,0.08)',
      fill: true,
      tension: 0.4,
      pointBackgroundColor: '#C4276E',
      pointRadius: 4,
      pointHoverRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' $' + ctx.raw.toLocaleString('en-US', {{maximumFractionDigits:0}}),
          title: (items) => {json.dumps(w_labels)}[items[0].dataIndex]
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }},
      y: {{ ticks: {{ callback: v => '$' + (v/1000).toFixed(1) + 'k', font: {{ size: 10 }} }}, grid: {{ color: '#f3f4f6' }} }}
    }}
  }}
}});

// ── SKU Matrix ────────────────────────────────────────────────────────────────
const MATRIX_DATA   = {matrix_json};
const MATRIX_MONTHS = {matrix_months_json};
let _mxMode = 'rev', _mxSortCol = -1, _mxSortDir = 'desc', _mxQuery = '', _mxBuilt = false;

function _buildMxHead() {{
  const tr = document.createElement('tr');
  const thN = document.createElement('th'); thN.className = 'col-name'; thN.textContent = 'Product'; tr.appendChild(thN);
  MATRIX_MONTHS.forEach((lbl, i) => {{
    const th = document.createElement('th'); th.textContent = lbl; th.onclick = () => _mxSort(i); tr.appendChild(th);
  }});
  const thT = document.createElement('th'); thT.textContent = 'Total'; thT.className = 'col-total'; thT.onclick = () => _mxSort(-1); tr.appendChild(thT);
  document.getElementById('matrixHead').appendChild(tr);
}}

function _mxGet(row, i) {{ return _mxMode === 'rev' ? row.rev[i] : row.units[i]; }}
function _mxTotal(row) {{ return _mxMode === 'rev' ? row.total_rev : row.total_units; }}
function _mxFmt(v) {{
  if (!v) return '';
  return _mxMode === 'rev'
    ? '$' + v.toLocaleString('en-US', {{maximumFractionDigits:0}})
    : v.toLocaleString('en-US', {{maximumFractionDigits:0}});
}}
function _mxHeat(frac) {{
  if (frac <= 0) return '';
  const r = Math.round(255 + (196 - 255) * frac);
  const g = Math.round(255 + (39  - 255) * frac);
  const b = Math.round(255 + (110 - 255) * frac);
  return `background:rgb(${{r}},${{g}},${{b}});color:${{frac > 0.55 ? '#fff' : '#1f2937'}};`;
}}

function _renderMx() {{
  const q = _mxQuery.toLowerCase();
  let rows = q ? MATRIX_DATA.filter(r => r.name.toLowerCase().includes(q)) : MATRIX_DATA.slice();
  rows.sort((a, b) => {{
    const va = _mxSortCol === -1 ? _mxTotal(a) : _mxGet(a, _mxSortCol);
    const vb = _mxSortCol === -1 ? _mxTotal(b) : _mxGet(b, _mxSortCol);
    return _mxSortDir === 'desc' ? vb - va : va - vb;
  }});
  const ths = document.querySelectorAll('#matrixHead th');
  ths.forEach(th => th.classList.remove('sort-asc','sort-desc'));
  const si = _mxSortCol === -1 ? MATRIX_MONTHS.length + 1 : _mxSortCol + 1;
  if (ths[si]) ths[si].classList.add(_mxSortDir === 'desc' ? 'sort-desc' : 'sort-asc');
  const maxes = Array(MATRIX_MONTHS.length).fill(0);
  rows.forEach(r => MATRIX_MONTHS.forEach((_, i) => {{ const v = _mxGet(r, i); if (v > maxes[i]) maxes[i] = v; }}));
  const tbody = document.getElementById('matrixBody');
  tbody.innerHTML = '';
  rows.forEach(row => {{
    const tr = document.createElement('tr');
    const tdN = document.createElement('td'); tdN.className = 'col-name'; tdN.title = row.name; tdN.textContent = row.name; tr.appendChild(tdN);
    MATRIX_MONTHS.forEach((_, i) => {{
      const v = _mxGet(row, i), td = document.createElement('td');
      td.textContent = _mxFmt(v);
      const hs = _mxHeat(maxes[i] > 0 ? v / maxes[i] : 0); if (hs) td.setAttribute('style', hs);
      tr.appendChild(td);
    }});
    const tdT = document.createElement('td'); tdT.className = 'col-total'; tdT.textContent = _mxFmt(_mxTotal(row)); tr.appendChild(tdT);
    tbody.appendChild(tr);
  }});
  document.getElementById('matrixCount').textContent = rows.length < MATRIX_DATA.length
    ? `Showing ${{rows.length}} of ${{MATRIX_DATA.length}} products`
    : `${{MATRIX_DATA.length}} products`;
}}

function _mxSort(col) {{
  _mxSortDir = _mxSortCol === col ? (_mxSortDir === 'desc' ? 'asc' : 'desc') : 'desc';
  _mxSortCol = col; _renderMx();
}}
function setMatrixMode(mode) {{
  _mxMode = mode; _mxSortCol = -1; _mxSortDir = 'desc';
  document.getElementById('btnRev').classList.toggle('active', mode === 'rev');
  document.getElementById('btnUnits').classList.toggle('active', mode !== 'rev');
  _renderMx();
}}
function filterMatrix() {{ _mxQuery = document.getElementById('matrixSearch').value; _renderMx(); }}

// ── Insight accordion ─────────────────────────────────────────────────────────
function toggleInsight(id) {{
  const body = document.getElementById('ins-' + id);
  const chev = document.getElementById('chev-' + id);
  const open = body.style.display === 'none';
  body.style.display = open ? 'block' : 'none';
  chev.classList.toggle('open', open);
}}
</script>
</body>
</html>
"""

# ── Write output ──────────────────────────────────────────────────────────────
out_path = OUTPUT_DIR / f"WoofGang_{_fn}_Retail_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"  Saved: {out_path}")
