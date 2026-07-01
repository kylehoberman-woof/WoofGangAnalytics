import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from config import get_store, STORE_REGISTRY, get_store_display, get_store_fn, get_other_stores
from classifier import is_service, detect_vendor
from config import PORTAL_BACK_JS

import sys as _sys
_store_name = _sys.argv[1] if len(_sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = get_store_display(_store_name)
_home_url = "../index.html"
_other_keys = get_other_stores(_store_name)
# Backward-compat single-other-store variables (first other store)
_other_store = get_store_display(_other_keys[0]) if _other_keys else ""
_other_dir = f"../{_other_keys[0]}" if _other_keys else ".."
_other_fn = get_store_fn(_other_keys[0]) if _other_keys else ""
_switch_url = f"{_other_dir}/WoofGang_{_other_fn}_Inventory_Dashboard.html" if _other_keys else ""
DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir

with open(DATA_DIR / "all_data.json") as f:
    all_data = json.load(f)
with open(DATA_DIR / "stock_levels.json") as f:
    stock_levels = json.load(f)

# Fetch pending receipts from Supabase for "Pending" column
import httpx
from config import SUPABASE_URL, SUPABASE_ANON_KEY
_pending_by_sku = {}
try:
    _sb_headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }
    _r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/invoice_scans?"
        f"select=po_number,line_items&status=eq.pending&store_key=eq.{_store_name}",
        headers=_sb_headers,
        timeout=10,
    )
    if _r.status_code == 200:
        for row in _r.json():
            po = row.get("po_number") or "?"
            for li in (row.get("line_items") or []):
                sku = str(li.get("sku", "")).strip()
                qty = int(li.get("quantity", 0) or 0)
                if sku and qty > 0:
                    if sku not in _pending_by_sku:
                        _pending_by_sku[sku] = []
                    _pending_by_sku[sku].append({"qty": qty, "po": po})
        print(f"  Pending receipts: {len(_pending_by_sku)} SKUs with incoming stock")
    else:
        print(f"  Warning: Supabase returned {_r.status_code} for pending receipts")
except Exception as e:
    print(f"  Warning: Could not fetch pending receipts from Supabase: {e}")

# Load real brand data from FranPOS (if available)
_brand_cache_path = DATA_DIR / "sku_brands.json"
_brand_cache = {}
if _brand_cache_path.exists():
    with open(_brand_cache_path) as f:
        _brand_cache = json.load(f)

def _normalize_vendor(v):
    """Merge vendor name variants into canonical names."""
    vl = v.lower().strip()
    # "Woof Gang Private Label", "Woof Gang Bakery", "Woof Gang" → "Woof Gang"
    # But "WOOF" stays as its own vendor
    if vl in ("woof gang private label", "woof gang bakery", "woof gang"):
        return "Woof Gang"
    return v

def get_vendor(sku, name):
    """Get vendor/brand from FranPOS cache, fall back to heuristic."""
    info = _brand_cache.get(sku)
    if info and isinstance(info, dict) and info.get("brand"):
        return _normalize_vendor(info["brand"])
    return _normalize_vendor(detect_vendor(sku, name))

def get_retail_price(sku):
    """Get listed retail price from FranPOS product cache."""
    info = _brand_cache.get(sku)
    if info and isinstance(info, dict):
        return info.get("retail_price") or 0
    return 0

order_items = all_data.get("order_items", [])

sku_data = defaultdict(lambda: {"name":"","sku":"","cost":0,"revenue":0,"units":0,"months_all":set(),"vendor":""})

for item in order_items:
    sku = str(item.get("Sku","")).strip()
    name = str(item.get("Name","")).strip()
    if not sku or is_service(sku, name):
        continue
    d = sku_data[sku]
    d["sku"] = sku
    if name: d["name"] = name
    cost = float(item.get("Cost") or 0)
    if cost > 0: d["cost"] = cost
    price = float(item.get("Price") or 0)
    qty = float(item.get("Quantity") or 1)
    discount = float(item.get("DiscountAmount") or item.get("Discount") or 0)
    d["revenue"] += max(0, (price * qty) - discount)
    d["units"] += qty
    month = str(item.get("CreatedOn",""))[:7]
    if month: d["months_all"].add(month)
    d["vendor"] = get_vendor(sku, name)

results = []
for sku, d in sku_data.items():
    if d["units"] < 1: continue
    stock = stock_levels.get(sku)
    vel_monthly = d["units"] / max(len(d["months_all"]), 1)
    vel_weekly = vel_monthly / 4.33
    wos = round(stock / vel_weekly, 1) if stock is not None and vel_weekly > 0 else None
    has_cost = d["cost"] > 0
    avg_price = d["revenue"] / d["units"] if d["units"] > 0 else 0
    margin = ((avg_price - d["cost"]) / avg_price * 100) if has_cost and avg_price > 0 else None
    retail_price = get_retail_price(sku) or round(avg_price, 2)
    results.append({"sku":sku,"name":d["name"] or sku,"stock":stock,"revenue":d["revenue"],
        "units":d["units"],"cost":d["cost"],"has_cost":has_cost,"margin":margin,"retail_price":retail_price,
        "velocity_monthly":round(vel_monthly,2),"weeks_of_supply":wos,"vendor":d["vendor"]})

from datetime import timedelta

# Include any SKU with non-zero stock, plus any SKU active in last 90 days
cutoff_90 = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
active_skus = set()
for item in order_items:
    sku = str(item.get("Sku","")).strip()
    date = str(item.get("CreatedOn",""))[:10]
    if sku and date >= cutoff_90:
        active_skus.add(sku)

results = [r for r in results if r["sku"] in active_skus or (r["stock"] is not None and r["stock"] != 0)]
results.sort(key=lambda x: x["revenue"], reverse=True)

def categorize(r):
    s = r["stock"]
    if s is None: return "untracked"
    if s <= 0: return "out"
    if s <= 2: return "critical"
    wos = r["weeks_of_supply"]
    if s <= 5 or (wos is not None and wos < 2): return "low"
    return "ok"

for r in results: r["status"] = categorize(r)

vendor_stats = defaultdict(lambda: {"revenue":0,"units":0,"skus":0,"low_stock":0})
for r in results:
    v = r["vendor"]
    vendor_stats[v]["revenue"] += r["revenue"]
    vendor_stats[v]["units"] += r["units"]
    vendor_stats[v]["skus"] += 1
    if r["status"] in ("out","critical","low"): vendor_stats[v]["low_stock"] += 1

out_count = sum(1 for r in results if r["status"]=="out")
critical_count = sum(1 for r in results if r["status"]=="critical")
low_count = sum(1 for r in results if r["status"]=="low")
ok_count = sum(1 for r in results if r["status"]=="ok")
no_cost_count = sum(1 for r in results if not r["has_cost"])
top50 = results[:50]

def badge(s):
    m = {"out":'<span class="badge badge-out">OUT</span>',
         "critical":'<span class="badge badge-critical">CRITICAL</span>',
         "low":'<span class="badge badge-low">LOW</span>',
         "ok":'<span class="badge badge-ok">OK</span>',
         "untracked":'<span class="badge badge-na">-</span>'}
    return m.get(s,"")

def esc_attr(s):
    """Escape a string for use in an HTML attribute value."""
    return s.replace("&","&amp;").replace('"',"&quot;").replace("'","&#39;")

def pending_cell(sku):
    """Build the Pending column cell for a SKU."""
    plist = _pending_by_sku.get(sku, [])
    if not plist:
        return '<td class="num">-</td>'
    total = sum(p["qty"] for p in plist)
    pos = ", ".join(set(p["po"] for p in plist))
    return f'<td class="num"><span class="badge-pending" title="PO: {pos}">+{total}</span></td>'

def make_rows(items, show_cost=True):
    rows = []
    for r in items:
        s = r["status"]
        stock = r["stock"]
        wos = r["weeks_of_supply"]
        neg = " neg" if stock is not None and stock < 0 else ""
        stock_str = f"{stock:.0f}" if stock is not None else "-"
        wos_str = f"{wos:.1f}" if wos is not None and wos >= 0 else "-"
        vel_str = f"{r['velocity_monthly']:.1f}" if r["velocity_monthly"] > 0 else "-"
        cost_str = f"${r['cost']:.2f}" if r["has_cost"] else '<span class="no-cost">No cost</span>'
        price_str = f"${r['retail_price']:.2f}" if r["retail_price"] > 0 else "-"
        margin_str = f"{r['margin']:.0f}%" if r["margin"] is not None else "-"
        cost_cols = f'<td class="num">{cost_str}</td><td class="num">{price_str}</td><td class="num">{margin_str}</td>' if show_cost else ""
        rows.append(
            f'<tr class="row-{s}" data-status="{s}" data-vendor="{r["vendor"].lower()}">'
            f'<td>{badge(s)}</td><td class="sku-cell">{r["sku"]}</td>'
            f'<td class="name-cell">{r["name"][:48]}</td><td class="vendor-cell vendor-edit" data-sku="{r["sku"]}">{r["vendor"]}</td>'
            f'<td class="num stock-edit{neg}" data-sku="{r["sku"]}">{stock_str}</td>{pending_cell(r["sku"])}<td class="num">{vel_str}</td>'
            f'<td class="num">{wos_str}</td>{cost_cols}'
            f'<td class="num">${r["revenue"]:,.0f}</td>'
            f'<td><button class="req-btn" onclick="openReqModal(this)" data-name="{esc_attr(r["name"][:50])}">Request</button></td></tr>'
        )
    return "\n".join(rows)

vendor_order = ["Woof Gang","PFX","Fauna","K9 Cuisine","Other"]
vendor_colors = {"Woof Gang":("#C4276E","#FDF0F5"),"PFX":("#1B6B6B","#EEF7F7"),
                 "Fauna":("#6B3520","#F5EDE8"),"K9 Cuisine":("#2C5F8A","#EEF4FA"),"Other":("#555","#F5F5F5")}
vendor_cards = ""
for v in vendor_order:
    if v not in vendor_stats: continue
    vs = vendor_stats[v]
    color, bg = vendor_colors[v]
    alert = f'<span class="va">&#9888;&#65039; {vs["low_stock"]} low/out</span>' if vs["low_stock"] > 0 else ""
    vendor_cards += (
        f'<div class="vc" style="border-top:4px solid {color};background:{bg};">'
        f'<div class="vn" style="color:{color};">{v}</div>'
        f'<div class="vstats">'
        f'<div><span class="sv">{vs["skus"]}</span><span class="sl">SKUs</span></div>'
        f'<div><span class="sv">${vs["revenue"]:,.0f}</span><span class="sl">Revenue</span></div>'
        f'<div><span class="sv">{vs["units"]:.0f}</span><span class="sl">Units Sold</span></div>'
        f'</div>{alert}</div>'
    )

nocost_rows = "\n".join(
    f'<tr class="row-{r["status"]}" data-status="{r["status"]}" data-vendor="{r["vendor"].lower()}">'
    f'<td>{badge(r["status"])}</td><td class="sku-cell">{r["sku"]}</td>'
    f'<td class="name-cell">{r["name"][:48]}</td><td class="vendor-cell">{r["vendor"]}</td>'
    f'<td class="num stock-edit" data-sku="{r["sku"]}">{"" if r["stock"] is None else str(int(r["stock"]))}</td>'
    f'{pending_cell(r["sku"])}'
    f'<td class="num">{"-" if r["velocity_monthly"] <= 0 else str(round(r["velocity_monthly"],1))}</td>'
    f'<td class="num cost-edit" data-sku="{r["sku"]}">-</td>'
    f'<td class="num">{"${:.2f}".format(r["retail_price"]) if r["retail_price"] > 0 else "-"}</td>'
    f'<td class="num">${r["revenue"]:,.0f}</td>'
    f'<td><button class="req-btn" onclick="openReqModal(this)" data-name="{esc_attr(r["name"][:50])}">Request</button></td></tr>'
    for r in results if not r["has_cost"]
)

from zoneinfo import ZoneInfo
et = ZoneInfo("America/New_York")
now = datetime.now(et).strftime("%B %d, %Y at %I:%M %p ET")
top50_rows = make_rows(top50)
all_rows = make_rows(results)

# Reorder recommendations (3-week supply — lowered Apr 25 2026 to improve cash flow)
REORDER_WEEKS = 3
reorder_by_vendor = defaultdict(list)
for r in results:
    if r["status"] not in ("out","critical","low"): continue
    if r["velocity_monthly"] <= 0: continue
    needed = max(0, (r["velocity_monthly"] / 4.33) * REORDER_WEEKS - max(0, r.get("stock",0)))
    if needed <= 0: continue
    est_cost = round(needed * r["cost"], 2) if r["cost"] else 0
    reorder_by_vendor[r["vendor"]].append({**r, "needed": round(needed,1), "est_cost": est_cost})

TH_REORDER = '<th>SKU</th><th>Product</th><th class="num">Stock</th><th class="num">Vel/Mo</th><th class="num">Need</th><th class="num">Cost</th><th class="num">Price</th><th class="num">Est.$</th><th>Status</th>'

reorder_html = ""
reorder_total = 0
VENDOR_COLORS = {"Woof Gang":"#C4276E","PFX":"#1B6B6B","Fauna":"#6B3520"}
# Get all vendors that have reorder items, sorted with known vendors first then alphabetical
known_vendors = ["Woof Gang","PFX","Fauna"]
other_vendors = sorted(set(reorder_by_vendor.keys()) - set(known_vendors))
all_reorder_vendors = [v for v in known_vendors if v in reorder_by_vendor] + other_vendors
for v in all_reorder_vendors:
    items = reorder_by_vendor.get(v, [])
    if not items: continue
    vtotal = sum(i["est_cost"] for i in items)
    reorder_total += vtotal
    # Cycle through accent colors for vendors without a fixed color
    _fallback_colors = ["#F57C00","#455A64","#7B1FA2","#00838F","#D32F2F","#558B2F","#1565C0","#AD1457"]
    vc = VENDOR_COLORS.get(v, _fallback_colors[hash(v) % len(_fallback_colors)])
    rows = ""
    for i in sorted(items, key=lambda x: -x["velocity_monthly"]):
        rows += (f'<tr data-status="{i["status"]}" data-vendor="{i["vendor"].lower()}">'
            f'<td>{i["sku"]}</td><td>{i["name"][:45]}</td>'
            f'<td class="num">{i.get("stock",0):.1f}</td><td class="num">{i["velocity_monthly"]}</td>'
            f'<td class="num">{i["needed"]}</td><td class="num">${i["cost"]:.2f}</td>'
            f'<td class="num">${i["retail_price"]:.2f}</td>'
            f'<td class="num">${i["est_cost"]:,.2f}</td><td>{badge(i["status"])}</td></tr>')
    reorder_html += (f'<div style="background:white;border-radius:10px;border-top:4px solid {vc};padding:16px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,0.06)">'
        f'<div class="vendor-hdr" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
        f'<div style="font-weight:700;font-size:1rem;color:{vc}"><span class="collapse-arrow">▼</span>{v}</div>'
        f'<div style="font-size:0.85rem;color:#666">{len(items)} items · Est. <strong>${vtotal:,.0f}</strong></div></div>'
        f'<div style="overflow-x:auto"><table><thead><tr>{TH_REORDER}</tr></thead><tbody>{rows}</tbody></table></div></div>')

neg_rows = ""
for r in sorted(results, key=lambda x: x.get("stock") or 0):
    if (r.get("stock") or 0) >= 0: continue
    neg_rows += (f'<tr data-status="{r["status"]}" data-vendor="{r["vendor"].lower()}">'
        f'<td>{r["sku"]}</td><td>{r["name"][:45]}</td>'
        f'<td class="num stock-edit" style="color:red" data-sku="{r["sku"]}">{r.get("stock",0):.1f}</td>'
        f'<td>{r["vendor"]}</td><td class="num">{r["velocity_monthly"]}</td>'
        f'<td><button class="req-btn" onclick="openReqModal(this)" data-name="{esc_attr(r["name"][:50])}">Request</button></td></tr>')

TH_NEG = '<th>SKU</th><th>Product</th><th class="num">Stock</th><th>Vendor</th><th class="num">Vel/Mo</th><th></th>'

CSS = """
:root{--m:#C4276E;--ml:#FDF0F5;--t:#1B6B6B;--br:#6B3520;--dk:#1a1a2e;--md:#2d2d44;--tx:#1f2937;--mu:#6b7280;--bd:#e5e7eb;--bg:#f8f9fb;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--tx);}
.header{background:linear-gradient(135deg,#1B6B6B 0%,#6B3520 100%);color:white;padding:40px 0 30px;text-align:center;position:relative;overflow:hidden}
.header::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(196,39,110,0.15) 0%,transparent 50%)}
.header h1{font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px;color:white}
.header .subtitle{font-size:1rem;font-weight:400;opacity:0.9;position:relative}
.header .brand-tag{display:inline-block;background:#C4276E;color:white;padding:4px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-top:12px;position:relative}
.header-timestamp{position:absolute;top:12px;right:20px;font-size:0.78rem;opacity:0.85;font-weight:400;z-index:1}
.topbar{background:#C4276E;padding:12px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3)}
.sum-row{display:grid;grid-template-columns:repeat(5,1fr);background:#fff;border-bottom:1px solid var(--bd);}
.sc{padding:18px 20px;border-right:1px solid var(--bd);text-align:center;}
.sc:last-child{border-right:none;}
.sc .v{font-size:32px;font-weight:800;line-height:1;}
.sc .l{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-top:4px;color:var(--mu);}
.s-out .v{color:#dc2626;}.s-crit .v{color:#ea580c;}.s-low .v{color:#ca8a04;}.s-ok .v{color:#16a34a;}.s-nc .v{color:#7c3aed;}
.vs{padding:24px 40px 8px;}
.vs h2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--mu);margin-bottom:14px;}
.vg{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;}
.vc{border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);}
.vn{font-size:15px;font-weight:800;margin-bottom:10px;}
.vstats{display:flex;gap:12px;}
.vstats>div{flex:1;}
.sv{display:block;font-size:14px;font-weight:700;color:var(--tx);}
.sl{display:block;font-size:10px;color:var(--mu);text-transform:uppercase;letter-spacing:.4px;margin-top:1px;}
.va{display:block;margin-top:8px;font-size:11px;font-weight:600;color:#ea580c;}
.ctrl{padding:20px 40px 0;display:flex;gap:0;align-items:center;border-bottom:2px solid var(--bd);background:#fff;margin-top:20px;}
.tb{padding:10px 18px;border:none;background:none;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;color:var(--mu);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;transition:all .15s;}
.tb.active{color:var(--m);border-bottom-color:var(--m);}
.filters{padding:14px 40px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#fff;border-bottom:1px solid var(--bd);}
.fb{padding:5px 14px;border-radius:20px;border:1.5px solid var(--bd);background:#fff;cursor:pointer;font-family:'Inter',sans-serif;font-size:12px;font-weight:600;color:var(--mu);transition:all .15s;}
.fb.active,.fb:hover{border-color:var(--m);background:var(--m);color:#fff;}
.sb{margin-left:auto;padding:6px 14px;border:1.5px solid var(--bd);border-radius:20px;font-family:'Inter',sans-serif;font-size:12px;width:200px;outline:none;}
.sb:focus{border-color:var(--m);}
.tw{margin:0 40px 40px;background:#fff;border-radius:0 0 12px 12px;box-shadow:0 2px 12px rgba(0,0,0,.05);overflow:hidden;}
table{width:100%;border-collapse:collapse;font-size:13px;}
thead th{background:var(--dk);color:rgba(255,255,255,.8);padding:11px 12px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;}
thead th.num{text-align:right;}
tbody tr{border-bottom:1px solid #f3f4f6;transition:background .1s;}
tbody tr:hover{background:#fdf0f5 !important;}
td{padding:10px 12px;vertical-align:middle;}
td.num{text-align:right;font-family:'DM Mono',monospace;font-size:12px;}
td.neg{color:#dc2626 !important;font-weight:700;}
td.sku-cell{font-family:'DM Mono',monospace;font-size:11px;color:var(--mu);}
td.name-cell{font-weight:500;}
td.vendor-cell{font-size:12px;color:var(--mu);font-weight:500;}
.no-cost{color:#7c3aed;font-size:11px;font-weight:600;}
.row-out{background:#fff5f5;}.row-critical{background:#fff7ed;}.row-low{background:#fefce8;}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap;}
.badge-out{background:#fee2e2;color:#dc2626;}.badge-critical{background:#ffedd5;color:#ea580c;}
.badge-low{background:#fef9c3;color:#ca8a04;}.badge-ok{background:#dcfce7;color:#16a34a;}.badge-na{background:#f3f4f6;color:#9ca3af;}
.badge-pending{display:inline-block;background:#FFF3E0;color:#E65100;border:1px solid #FFB74D;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap;}
.hidden{display:none !important;}.panel{display:none;}.panel.active{display:block;}
/* Sortable headers */
thead th{cursor:pointer;user-select:none;position:relative;}
thead th:hover{background:var(--md);}
thead th .sort-arrow{font-size:9px;margin-left:4px;opacity:0.5;}
thead th.sorted-asc .sort-arrow,thead th.sorted-desc .sort-arrow{opacity:1;}
/* Sticky table headers */
.tw{position:relative;}
thead th{position:sticky;top:0;z-index:10;}
/* Collapsible vendor sections */
.vendor-hdr{cursor:pointer;user-select:none;}
.vendor-hdr:hover{opacity:0.85;}
.vendor-hdr .collapse-arrow{transition:transform 0.2s;display:inline-block;margin-right:6px;font-size:0.8rem;}
.vendor-hdr.collapsed .collapse-arrow{transform:rotate(-90deg);}
.stock-edit{cursor:pointer;position:relative;transition:background 0.2s;}
.stock-edit:hover{background:rgba(196,39,110,0.06);border-radius:4px;}
.stock-edit input{width:60px;padding:2px 4px;border:2px solid #C4276E;border-radius:4px;font-family:inherit;font-size:inherit;text-align:right;color:#1a1a2e;background:#fff;outline:none;}
@keyframes flashGreen{0%{background:#c8e6c9}100%{background:transparent}}
@keyframes flashRed{0%{background:#ffcdd2}100%{background:transparent}}
.stock-flash-ok{animation:flashGreen 1.2s ease-out;}
.stock-flash-err{animation:flashRed 1.2s ease-out;}
.cost-edit{cursor:pointer;position:relative;transition:background 0.2s;color:#7c3aed;font-weight:600;}
.cost-edit:hover{background:rgba(124,58,237,0.06);border-radius:4px;}
.cost-edit input{width:70px;padding:2px 4px;border:2px solid #7c3aed;border-radius:4px;font-family:inherit;font-size:inherit;text-align:right;color:#1a1a2e;background:#fff;outline:none;}
.vendor-edit{cursor:pointer;position:relative;transition:background 0.2s;color:#00838F;font-weight:600;}
.vendor-edit:hover{background:rgba(0,131,143,0.06);border-radius:4px;}
.vendor-edit input{width:120px;padding:2px 4px;border:2px solid #00838F;border-radius:4px;font-family:inherit;font-size:inherit;text-align:left;color:#1a1a2e;background:#fff;outline:none;}
/* ── Request button (inline in table rows) ── */
.req-btn{padding:3px 8px;background:none;border:1px solid #C4276E;color:#C4276E;border-radius:5px;font-family:inherit;font-size:0.72rem;font-weight:700;cursor:pointer;white-space:nowrap;}
.req-btn:hover{background:#FDF0F5;}
/* ── Requests tab ── */
.req-toolbar{display:flex;align-items:center;padding:14px 0 14px;gap:10px;}
.req-new-btn{padding:8px 16px;background:#C4276E;color:white;border:none;border-radius:8px;font-family:'Inter',sans-serif;font-size:0.85rem;font-weight:700;cursor:pointer;}
.req-new-btn:hover{background:#a31f5b;}
#req-filter{padding:7px 12px;border:2px solid #e8e8e8;border-radius:8px;font-family:'Inter',sans-serif;font-size:0.85rem;background:white;}
.req-card{background:white;border-radius:10px;padding:14px 18px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,0.07);display:flex;align-items:flex-start;gap:12px;border-left:4px solid #e8e8e8;}
.req-card.urgent{border-left-color:#C62828;}
.req-card.ordered{border-left-color:#2E7D32;opacity:0.75;}
.req-card.dismissed{opacity:0.45;}
.req-badge{font-size:0.65rem;font-weight:800;padding:3px 7px;border-radius:8px;text-transform:uppercase;letter-spacing:0.05em;}
.req-badge.urgent{background:#ffebee;color:#C62828;}
.req-badge.normal{background:#f5f5f5;color:#888;}
.req-badge.ordered{background:#e8f5e9;color:#2E7D32;}
.req-badge.dismissed{background:#f5f5f5;color:#bbb;}
.req-name{font-weight:700;font-size:0.95rem;}
.req-meta{font-size:0.75rem;color:#999;margin-top:3px;}
.req-actions{margin-left:auto;display:flex;gap:6px;flex-shrink:0;align-items:center;}
.req-order-btn{padding:5px 12px;background:#1B6B6B;color:white;border:none;border-radius:6px;font-family:inherit;font-size:0.78rem;font-weight:700;cursor:pointer;}
.req-order-btn:hover{background:#145858;}
.req-dismiss-btn{padding:5px 10px;background:white;border:1px solid #ddd;border-radius:6px;font-family:inherit;font-size:0.78rem;color:#999;cursor:pointer;}
.req-dismiss-btn:hover{color:#C62828;border-color:#C62828;}
.req-empty{text-align:center;color:#ccc;padding:40px;font-size:0.9rem;}
/* ── Shared modal (no existing modal in this file) ── */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:200;display:flex;align-items:center;justify-content:center;padding:20px;}
.overlay.hidden{display:none;}
.modal{background:white;border-radius:14px;max-width:520px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.15);}
.modal-hdr{padding:20px 24px 12px;border-bottom:1px solid #eee;}.modal-hdr h2{font-size:1.1rem;font-weight:800;}
.modal-body{padding:16px 24px;}
.rfg{margin-bottom:14px;}.rfg label{display:block;font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#999;margin-bottom:5px;}
.rfg input,.rfg select,.rfg textarea{width:100%;padding:9px 12px;border:2px solid #e8e8e8;border-radius:8px;font-family:inherit;font-size:0.9rem;color:#1a1a2e;background:white;}
.rfg input:focus,.rfg select:focus,.rfg textarea:focus{outline:none;border-color:#C4276E;}
.rfg textarea{resize:vertical;min-height:60px;}
.rrow2{display:flex;gap:12px;}.rrow2>.rfg{flex:1;}
.modal-actions{padding:12px 24px 20px;display:flex;justify-content:flex-end;gap:8px;}
.mbtn-save{padding:10px 24px;background:#C4276E;color:white;border:none;border-radius:8px;font-family:inherit;font-size:0.88rem;font-weight:700;cursor:pointer;}
.mbtn-save:hover{background:#a31f5b;}.mbtn-save:disabled{opacity:0.5;}
.mbtn-cancel{padding:10px 20px;background:white;border:2px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.88rem;font-weight:600;cursor:pointer;color:#888;}
.mbtn-cancel:hover{border-color:#999;}
"""

JS = """
let cf='all',cs='',ct='top50';
function switchTab(t,b){ct=t;document.querySelectorAll('.tb').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));document.getElementById('p-'+t).classList.add('active');if(t==='requests'){loadRequests();}else{af();}}
function setFilter(f,b){cf=f;document.querySelectorAll('.fb').forEach(x=>x.classList.remove('active'));b.classList.add('active');af();}
function doSearch(v){cs=v.toLowerCase();af();}
function af(){
  var tbodyMap={top50:'b-top50',all:'b-all',nocost:'b-nocost',negative:'b-negative'};
  var sel=ct==='reorder'?'#p-reorder tr':'#'+(tbodyMap[ct]||'b-top50')+' tr';
  document.querySelectorAll(sel).forEach(function(r){
    var s=r.dataset.status||'',v=r.dataset.vendor||'';
    if(!s&&!v){return;}
    var mf=cf==='all'||(['out','critical','low','ok','untracked'].includes(cf)?s===cf:cf==='other'?(v&&!['woof gang','pfx','fauna'].includes(v)):v.includes(cf));
    r.classList.toggle('hidden',!(mf&&(!cs||r.textContent.toLowerCase().includes(cs))));
  });
}

// ── Sortable columns ──
function sortTable(th){
  var table=th.closest('table');
  var tbody=table.querySelector('tbody');
  if(!tbody) return;
  var idx=Array.from(th.parentNode.children).indexOf(th);
  var rows=Array.from(tbody.querySelectorAll('tr')).filter(function(r){return r.children.length>1;});
  var isNum=th.classList.contains('num');
  // Determine sort direction
  var asc=!th.classList.contains('sorted-asc');
  // Clear all sort indicators in this table
  th.parentNode.querySelectorAll('th').forEach(function(h){h.classList.remove('sorted-asc','sorted-desc');});
  th.classList.add(asc?'sorted-asc':'sorted-desc');
  rows.sort(function(a,b){
    var aVal=a.children[idx]?a.children[idx].textContent.trim():'';
    var bVal=b.children[idx]?b.children[idx].textContent.trim():'';
    if(isNum){
      aVal=parseFloat(aVal.replace(/[^\\d.-]/g,''))||0;
      bVal=parseFloat(bVal.replace(/[^\\d.-]/g,''))||0;
      return asc?aVal-bVal:bVal-aVal;
    }
    return asc?aVal.localeCompare(bVal):bVal.localeCompare(aVal);
  });
  rows.forEach(function(r){tbody.appendChild(r);});
}
// Attach click handlers to all sortable headers
document.querySelectorAll('thead th').forEach(function(th){
  th.addEventListener('click',function(){sortTable(th);});
  th.innerHTML+='<span class="sort-arrow">⇅</span>';
});

// ── Collapsible vendor sections in Reorder ──
document.querySelectorAll('.vendor-hdr').forEach(function(hdr){
  hdr.addEventListener('click',function(){
    var body=hdr.nextElementSibling;
    if(body){
      var hidden=body.style.display==='none';
      body.style.display=hidden?'block':'none';
      hdr.classList.toggle('collapsed',!hidden);
    }
  });
});

// ── Inline Stock Editing ──
document.addEventListener('click',function(e){
  var cell=e.target.closest('.stock-edit');
  if(!cell||cell.querySelector('input')) return;
  var sku=cell.dataset.sku;
  if(!sku) return;
  var oldVal=cell.textContent.trim();
  var numVal=parseFloat(oldVal.replace(/[^\\d.-]/g,''));
  if(isNaN(numVal)) numVal=0;
  var inp=document.createElement('input');
  inp.type='number';inp.value=numVal;inp.step='1';
  cell.textContent='';cell.appendChild(inp);
  inp.focus();inp.select();
  function cancel(){cell.textContent=oldVal;}
  function save(){
    var nv=parseFloat(inp.value);
    if(isNaN(nv)){cancel();return;}
    if(nv===numVal){cancel();return;}
    cell.textContent=nv;
    cell.classList.add('stock-flash-ok');
    setTimeout(function(){cell.classList.remove('stock-flash-ok');},1200);
    // Push to Franpos via receiving tool proxy (avoids CORS)
    if(typeof FRANPOS_TOKEN!=='undefined'){
      fetch('https://scanner.hoberman.io/api/set-stock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sku:sku,quantity:nv,store:STORE_KEY})})
        .then(function(r){
          if(!r.ok) throw new Error('HTTP '+r.status);
          cell.classList.remove('stock-flash-ok');
          cell.classList.add('stock-flash-ok');
          cell.title='Updated to '+nv;
        })
        .catch(function(err){
          cell.classList.remove('stock-flash-ok');
          cell.classList.add('stock-flash-err');
          cell.title='Update failed: '+err.message;
          setTimeout(function(){cell.classList.remove('stock-flash-err');},1200);
        });
    }
  }
  inp.addEventListener('keydown',function(e){
    if(e.key==='Enter'){e.preventDefault();save();}
    if(e.key==='Escape'){e.preventDefault();cancel();}
  });
  inp.addEventListener('blur',function(){save();});
});

// ── Inline cost editing (Missing Cost tab) ──
document.addEventListener('click',function(e){
  var cell=e.target.closest('.cost-edit');
  if(!cell||cell.querySelector('input')) return;
  var sku=cell.dataset.sku;
  if(!sku) return;
  var oldVal=cell.textContent.trim();
  var numVal=parseFloat(oldVal.replace(/[^\\d.-]/g,''));
  if(isNaN(numVal)) numVal='';
  var inp=document.createElement('input');
  inp.type='number';inp.value=numVal;inp.step='0.01';inp.placeholder='0.00';
  cell.textContent='';cell.appendChild(inp);
  inp.focus();inp.select();
  function cancel(){cell.textContent=oldVal;}
  function save(){
    var nv=parseFloat(inp.value);
    if(isNaN(nv)||nv<=0){cancel();return;}
    cell.textContent='$'+nv.toFixed(2);
    cell.classList.add('stock-flash-ok');
    setTimeout(function(){cell.classList.remove('stock-flash-ok');},1200);
    fetch('https://scanner.hoberman.io/api/set-cost',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sku:sku,cost:nv,store:STORE_KEY})})
      .then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status);
        cell.classList.remove('stock-flash-ok');
        cell.classList.add('stock-flash-ok');
        cell.title='Cost updated to $'+nv.toFixed(2);
      })
      .catch(function(err){
        cell.classList.remove('stock-flash-ok');
        cell.classList.add('stock-flash-err');
        cell.title='Update failed: '+err.message;
        setTimeout(function(){cell.classList.remove('stock-flash-err');},1200);
      });
  }
  inp.addEventListener('keydown',function(e){
    if(e.key==='Enter'){e.preventDefault();save();}
    if(e.key==='Escape'){e.preventDefault();cancel();}
  });
  inp.addEventListener('blur',function(){save();});
});

// ── Inline vendor editing ──
document.addEventListener('click',function(e){
  var cell=e.target.closest('.vendor-edit');
  if(!cell||cell.querySelector('input')) return;
  var sku=cell.dataset.sku;
  if(!sku) return;
  var oldVal=cell.textContent.trim();
  var inp=document.createElement('input');
  inp.type='text';inp.value=oldVal;
  cell.textContent='';cell.appendChild(inp);
  inp.focus();inp.select();
  function cancel(){cell.textContent=oldVal;}
  function save(){
    var nv=inp.value.trim();
    if(!nv||nv===oldVal){cancel();return;}
    cell.textContent=nv;
    cell.classList.add('stock-flash-ok');
    setTimeout(function(){cell.classList.remove('stock-flash-ok');},1200);
    var row=cell.closest('tr');
    if(row) row.dataset.vendor=nv.toLowerCase();
    fetch('https://scanner.hoberman.io/api/set-vendor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sku:sku,vendor:nv,store:STORE_KEY})})
      .then(function(r){
        if(!r.ok) throw new Error('HTTP '+r.status);
        cell.classList.remove('stock-flash-ok');
        cell.classList.add('stock-flash-ok');
        cell.title='Vendor updated to '+nv;
      })
      .catch(function(err){
        cell.classList.remove('stock-flash-ok');
        cell.classList.add('stock-flash-err');
        cell.title='Update failed: '+err.message;
        setTimeout(function(){cell.classList.remove('stock-flash-err');},1200);
      });
  }
  inp.addEventListener('keydown',function(e){
    if(e.key==='Enter'){e.preventDefault();save();}
    if(e.key==='Escape'){e.preventDefault();cancel();}
  });
  inp.addEventListener('blur',function(){save();});
});

// ── Item Requests ──
var _SB_HDR={'apikey':SB_KEY,'Authorization':'Bearer '+SB_KEY,'Content-Type':'application/json','Prefer':'return=representation'};
var _portalLevel=sessionStorage.getItem('wg_portal_level')||'store';
var _canAct=(_portalLevel==='owner'||_portalLevel==='manager');

function openReqModal(nameOrBtn){
  var name=typeof nameOrBtn==='string'?nameOrBtn:(nameOrBtn&&nameOrBtn.dataset?nameOrBtn.dataset.name:'');
  document.getElementById('req-name').value=name||'';
  document.getElementById('req-qty').value='';
  document.getElementById('req-priority').value='normal';
  document.getElementById('req-notes').value='';
  document.getElementById('req-overlay').classList.remove('hidden');
  setTimeout(function(){document.getElementById('req-name').focus();},50);
}
function closeReqModal(){document.getElementById('req-overlay').classList.add('hidden');}

function saveRequest(){
  var name=document.getElementById('req-name').value.trim();
  if(!name){alert('Item name is required');return;}
  var btn=document.getElementById('req-save-btn');
  btn.disabled=true;btn.textContent='Submitting...';
  fetch(SB_URL+'/inventory_requests',{method:'POST',headers:_SB_HDR,body:JSON.stringify({
    store_key:STORE_KEY,
    item_name:name,
    quantity:document.getElementById('req-qty').value.trim()||null,
    priority:document.getElementById('req-priority').value,
    notes:document.getElementById('req-notes').value.trim()||null,
    status:'pending'
  })}).then(function(r){
    if(!r.ok) throw new Error('HTTP '+r.status);
    btn.disabled=false;btn.textContent='Submit Request';
    closeReqModal();
    // Switch to requests tab to show the new entry
    var reqTab=document.querySelector('[onclick*="requests"]');
    if(reqTab) switchTab('requests',reqTab);
    else loadRequests();
  }).catch(function(e){
    btn.disabled=false;btn.textContent='Submit Request';
    alert('Failed to submit request: '+e.message);
  });
}

function loadRequests(){
  var status=document.getElementById('req-filter')?document.getElementById('req-filter').value:'pending';
  var q='/inventory_requests?store_key=eq.'+STORE_KEY+'&order=created_at.desc';
  if(status!=='all') q+='&status=eq.'+status;
  fetch(SB_URL+q,{headers:{'apikey':SB_KEY,'Authorization':'Bearer '+SB_KEY}})
    .then(function(r){return r.json();})
    .then(function(rows){renderRequests(rows||[]);})
    .catch(function(e){
      var el=document.getElementById('req-list');
      if(el) el.innerHTML='<div class="req-empty">Failed to load requests</div>';
    });
}

function renderRequests(rows){
  var el=document.getElementById('req-list');
  if(!el) return;
  if(!rows.length){
    var status=document.getElementById('req-filter')?document.getElementById('req-filter').value:'pending';
    el.innerHTML='<div class="req-empty">'+(status==='pending'?'No pending requests — all clear!':'No requests found')+'</div>';
    return;
  }
  el.innerHTML=rows.map(function(r){
    var urgCls=r.priority==='urgent'?' urgent':'';
    var stCls=r.status==='ordered'?' ordered':(r.status==='dismissed'?' dismissed':'');
    var badgeKey=r.status==='ordered'?'ordered':(r.status==='dismissed'?'dismissed':r.priority);
    var badgeTxt=r.status==='ordered'?'Ordered':(r.status==='dismissed'?'Dismissed':(r.priority==='urgent'?'Urgent':'Normal'));
    var badge='<span class="req-badge '+badgeKey+'">'+badgeTxt+'</span>';
    var dt=new Date(r.created_at).toLocaleDateString('en-US',{month:'short',day:'numeric'});
    var meta=(r.quantity?'Qty: '+r.quantity+' &middot; ':'')+dt;
    var actions='';
    if(_canAct&&r.status==='pending'){
      actions='<div class="req-actions">'+
        '<button class="req-order-btn" onclick="updateReq('+r.id+',\\'ordered\\',this)">Mark Ordered</button>'+
        '<button class="req-dismiss-btn" onclick="updateReq('+r.id+',\\'dismissed\\',this)">Dismiss</button>'+
        '</div>';
    }
    return '<div class="req-card'+urgCls+stCls+'" id="rc-'+r.id+'">'+
      '<div style="flex:1">'+
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'+badge+
        '<span class="req-name">'+r.item_name+'</span></div>'+
        '<div class="req-meta">'+meta+(r.notes?' &middot; <em>'+r.notes+'</em>':'')+'</div>'+
      '</div>'+actions+'</div>';
  }).join('');
}

function updateReq(id,status,btn){
  if(btn){btn.disabled=true;btn.textContent='...';}
  fetch(SB_URL+'/inventory_requests?id=eq.'+id,{method:'PATCH',headers:_SB_HDR,body:JSON.stringify({status:status})})
    .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);loadRequests();})
    .catch(function(e){
      if(btn){btn.disabled=false;btn.textContent=status==='ordered'?'Mark Ordered':'Dismiss';}
      alert('Update failed: '+e.message);
    });
}
"""

TH = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\"num\">Stock</th><th class=\"num\">Pending</th><th class=\"num\">Vel/Mo</th><th class=\"num\">Wks</th><th class=\"num\">Cost</th><th class=\"num\">Price</th><th class=\"num\">Margin</th><th class=\"num\">Revenue</th><th></th>"
TH2 = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\"num\">Stock</th><th class=\"num\">Pending</th><th class=\"num\">Vel/Mo</th><th class=\"num\">Cost</th><th class=\"num\">Price</th><th class=\"num\">Revenue</th><th></th>"

# Build JS location_id → store_key map from registry (avoids hardcoded 203698/205993)
_loc_map_js = json.dumps({str(v["location_id"]): k for k, v in STORE_REGISTRY.items()})

html = f"""<!DOCTYPE html>
<html lang=\"en\"><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">
<title>Woof Gang - Inventory Dashboard</title>
<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap\" rel=\"stylesheet\">
<style>{CSS}</style></head><body>
<div class=\"header\"><div class=\"header-timestamp\">Updated {now}<br><a href=\"{_switch_url}\" style=\"color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.78rem\">&#x21C4; {_other_store}</a></div><h1>Woof Gang {_store_display}</h1><div class=\"subtitle\">Inventory Dashboard</div><div class=\"brand-tag\">Woof Gang Bakery &amp; Grooming</div></div>
<div class=\"topbar\"><a id=\"portal-back\" href=\"{_home_url}\" style=\"color:rgba(255,255,255,0.8);text-decoration:none;font-size:0.85rem;font-weight:600\">&larr; Home</a>{PORTAL_BACK_JS}</div>
<div class=\"sum-row\">
  <div class=\"sc s-out\"><div class=\"v\">{out_count}</div><div class=\"l\">Out of Stock</div></div>
  <div class=\"sc s-crit\"><div class=\"v\">{critical_count}</div><div class=\"l\">Critical (1-2)</div></div>
  <div class=\"sc s-low\"><div class=\"v\">{low_count}</div><div class=\"l\">Low Stock</div></div>
  <div class=\"sc s-ok\"><div class=\"v\">{ok_count}</div><div class=\"l\">Adequately Stocked</div></div>
  <div class=\"sc s-nc\"><div class=\"v\">{no_cost_count}</div><div class=\"l\">Missing Cost</div></div>
</div>
<div class=\"vs\"><h2>Orders by Vendor</h2><div class=\"vg\">{vendor_cards}</div></div>
<div class=\"ctrl\">
  <button class=\"tb active\" onclick=\"switchTab('top50',this)\">Top 50 Items</button>
  <button class=\"tb\" onclick=\"switchTab('all',this)\">All Retail Items</button>
  <button class=\"tb\" onclick=\"switchTab('nocost',this)\">Missing Cost</button>
  <button class=\"tb\" onclick=\"switchTab('reorder',this)\">Reorder</button>
  <button class=\"tb\" onclick=\"switchTab('negative',this)\">Negative Stock</button>
  <button class=\"tb\" onclick=\"switchTab('requests',this)\">Requests</button>
</div>
<div class=\"filters\">
  <button class=\"fb active\" onclick=\"setFilter('all',this)\">All</button>
  <button class=\"fb\" onclick=\"setFilter('out',this)\">Out of Stock</button>
  <button class=\"fb\" onclick=\"setFilter('critical',this)\">Critical</button>
  <button class=\"fb\" onclick=\"setFilter('low',this)\">Low</button>
  <button class=\"fb\" onclick=\"setFilter('ok',this)\">OK</button>
  <button class=\"fb\" onclick=\"setFilter('woof gang',this)\">Woof Gang</button>
  <button class=\"fb\" onclick=\"setFilter('pfx',this)\">PFX</button>
  <button class=\"fb\" onclick=\"setFilter('fauna',this)\">Fauna</button>
  <button class=\"fb\" onclick=\"setFilter('other',this)\">Other</button>
  <input class=\"sb\" type=\"text\" placeholder=\"Search name or SKU...\" oninput=\"doSearch(this.value)\">
</div>
<div class=\"tw\">
  <div id=\"p-top50\" class=\"panel active\"><table><thead><tr>{TH}</tr></thead><tbody id=\"b-top50\">{top50_rows}</tbody></table></div>
  <div id=\"p-all\" class=\"panel\"><table><thead><tr>{TH}</tr></thead><tbody id=\"b-all\">{all_rows}</tbody></table></div>
  <div id=\"p-nocost\" class=\"panel\"><table><thead><tr>{TH2}</tr></thead><tbody id=\"b-nocost\">{nocost_rows}</tbody></table></div>
  <div id=\"p-reorder\" class=\"panel\"><h3 style=\"padding:8px 8px 16px\">Reorder Recommendations (3-week supply) &middot; Est. Total ${reorder_total:,.0f}</h3>{reorder_html}</div>
  <div id=\"p-negative\" class=\"panel\"><table><thead><tr>{TH_NEG}</tr></thead><tbody id=\"b-negative\">{neg_rows}</tbody></table></div>
  <div id=\"p-requests\" class=\"panel\" style=\"padding:16px 40px 40px\">
    <div class=\"req-toolbar\">
      <button class=\"req-new-btn\" onclick=\"openReqModal()\">+ Request Item</button>
      <div style=\"margin-left:auto;display:flex;gap:8px;align-items:center\">
        <label style=\"font-size:0.78rem;color:#888;font-weight:600\">Show:</label>
        <select id=\"req-filter\" onchange=\"loadRequests()\">
          <option value=\"pending\">Pending</option>
          <option value=\"ordered\">Ordered</option>
          <option value=\"dismissed\">Dismissed</option>
          <option value=\"all\">All</option>
        </select>
      </div>
    </div>
    <div id=\"req-list\"><div class=\"req-empty\">Loading requests...</div></div>
  </div>
</div>
<!-- Request Modal -->
<div class=\"overlay hidden\" id=\"req-overlay\">
  <div class=\"modal\">
    <div class=\"modal-hdr\"><h2>Request Item</h2></div>
    <div class=\"modal-body\">
      <div class=\"rfg\"><label>Item Name *</label><input type=\"text\" id=\"req-name\" placeholder=\"e.g., Chicken Jerkey 6oz\"></div>
      <div class=\"rrow2\">
        <div class=\"rfg\"><label>Quantity / Amount</label><input type=\"text\" id=\"req-qty\" placeholder=\"e.g., 2 cases\"></div>
        <div class=\"rfg\"><label>Priority</label>
          <select id=\"req-priority\">
            <option value=\"normal\">Normal</option>
            <option value=\"urgent\">Urgent</option>
          </select>
        </div>
      </div>
      <div class=\"rfg\"><label>Notes</label><textarea id=\"req-notes\" placeholder=\"Optional details...\"></textarea></div>
    </div>
    <div class=\"modal-actions\">
      <button class=\"mbtn-cancel\" onclick=\"closeReqModal()\">Cancel</button>
      <button class=\"mbtn-save\" id=\"req-save-btn\" onclick=\"saveRequest()\">Submit Request</button>
    </div>
  </div>
</div>
<script>const FRANPOS_TOKEN="{_store.token}";const FRANPOS_LOC="{_store.location_id}";const FRANPOS_URL="https://publicapi.franpos.com";const _LOC_MAP={_loc_map_js};const STORE_KEY=_LOC_MAP[String(FRANPOS_LOC)]||"{_store_name}";const SB_URL="https://bqzinttbjeeaybywhhet.supabase.co/rest/v1";const SB_KEY="{SUPABASE_ANON_KEY}";{JS}</script></body></html>"""

_fn_store = _store_display.replace(" ", "")
out_path = OUTPUT_DIR / f"WoofGang_{_fn_store}_Inventory_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"SKUs: {len(results)} | Out: {out_count} | Critical: {critical_count} | Low: {low_count} | OK: {ok_count} | No cost: {no_cost_count}")