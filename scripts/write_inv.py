new_script = open("/Users/julieschorr/Desktop/store-analysis/scripts/generate_inventory_dashboard.py").read()
print(f"Old script: {len(new_script)} chars")

import textwrap
content = textwrap.dedent("""
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DATA_DIR = Path("/Users/julieschorr/Desktop/store-analysis/port-washington/data")
OUTPUT_DIR = Path("/Users/julieschorr/Desktop/store-analysis/port-washington")

with open(DATA_DIR / "all_data.json") as f:
    all_data = json.load(f)
with open(DATA_DIR / "stock_levels.json") as f:
    stock_levels = json.load(f)

order_items = all_data.get("order_items", [])

SERVICE_PREFIXES = ("987", "765", "543", "432", "986", "985", "984", "983", "982")
SERVICE_KEYWORDS = ("groom", "bath", "add-on", "walk-in", "spa upgrade", "nail", "brush", "teeth",
                    "deshedd", "de-shed", "full groom", "mini groom", "classic bath", "lux bath",
                    "miscellaneous", "gift card", "gratuity", "tip", "hypo allergenic")

def is_service(sku, name):
    sku = str(sku).strip()
    name = str(name).lower().strip()
    if any(sku.startswith(p) for p in SERVICE_PREFIXES):
        return True
    if any(k in name for k in SERVICE_KEYWORDS):
        return True
    if sku in ("000", "001", "TREAT01", "TREAT02", "1001", "1002", "1003", "1004", "1005", "1006", "1007"):
        return True
    return False

def detect_vendor(sku, name):
    sku = str(sku).upper()
    name = str(name).lower()
    if sku.startswith("810153") or "wgb" in name:
        return "Woof Gang"
    if "pfx" in name or "performatrin" in name or sku.startswith("PFX"):
        return "PFX"
    if "fauna" in name or sku.startswith("FAU"):
        return "Fauna"
    if "k9d" in sku.lower():
        return "K9 Cuisine"
    return "Other"

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
    d["vendor"] = detect_vendor(sku, name)

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
    results.append({"sku":sku,"name":d["name"] or sku,"stock":stock,"revenue":d["revenue"],
        "units":d["units"],"cost":d["cost"],"has_cost":has_cost,"margin":margin,
        "velocity_monthly":round(vel_monthly,2),"weeks_of_supply":wos,"vendor":d["vendor"]})

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
        margin_str = f"{r['margin']:.0f}%" if r["margin"] is not None else "-"
        cost_cols = f'<td class="num">{cost_str}</td><td class="num">{margin_str}</td>' if show_cost else ""
        rows.append(
            f'<tr class="row-{s}" data-status="{s}" data-vendor="{r["vendor"].lower()}">'
            f'<td>{badge(s)}</td><td class="sku-cell">{r["sku"]}</td>'
            f'<td class="name-cell">{r["name"][:48]}</td><td class="vendor-cell">{r["vendor"]}</td>'
            f'<td class="num{neg}">{stock_str}</td><td class="num">{vel_str}</td>'
            f'<td class="num">{wos_str}</td>{cost_cols}'
            f'<td class="num">${r["revenue"]:,.0f}</td></tr>'
        )
    return "\\n".join(rows)

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

nocost_rows = "\\n".join(
    f'<tr class="row-{r["status"]}" data-status="{r["status"]}" data-vendor="{r["vendor"].lower()}">'
    f'<td>{badge(r["status"])}</td><td class="sku-cell">{r["sku"]}</td>'
    f'<td class="name-cell">{r["name"][:48]}</td><td class="vendor-cell">{r["vendor"]}</td>'
    f'<td class="num">{f\\'{r["stock"]:.0f}\\' if r["stock"] is not None else "-"}</td>'
    f'<td class="num">{f\\'{r["velocity_monthly"]:.1f}\\' if r["velocity_monthly"] > 0 else "-"}</td>'
    f'<td class="num">${r["revenue"]:,.0f}</td></tr>'
    for r in results if not r["has_cost"]
)

now = datetime.now().strftime("%B %d, %Y at %I:%M %p")
top50_rows = make_rows(top50)
all_rows = make_rows(results)

CSS = \"\"\"
:root{--m:#C4276E;--ml:#FDF0F5;--t:#1B6B6B;--br:#6B3520;--dk:#1a1a2e;--md:#2d2d44;--tx:#1f2937;--mu:#6b7280;--bd:#e5e7eb;--bg:#f8f9fb;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--tx);}
.hdr{background:linear-gradient(135deg,var(--dk),var(--md));padding:28px 40px 24px;display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid var(--m);}
.hdr h1{font-size:20px;font-weight:800;color:#fff;}
.hdr p{font-size:12px;color:rgba(255,255,255,.55);margin-top:4px;}
.hdr-r{font-size:11px;color:rgba(255,255,255,.4);text-align:right;}
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
.tb{padding:10px 18px;border:none;background:none;font-family:'DM Sans',sans-serif;font-size:13px;font-weight:600;color:var(--mu);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;transition:all .15s;}
.tb.active{color:var(--m);border-bottom-color:var(--m);}
.filters{padding:14px 40px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#fff;border-bottom:1px solid var(--bd);}
.fb{padding:5px 14px;border-radius:20px;border:1.5px solid var(--bd);background:#fff;cursor:pointer;font-family:'DM Sans',sans-serif;font-size:12px;font-weight:600;color:var(--mu);transition:all .15s;}
.fb.active,.fb:hover{border-color:var(--m);background:var(--m);color:#fff;}
.sb{margin-left:auto;padding:6px 14px;border:1.5px solid var(--bd);border-radius:20px;font-family:'DM Sans',sans-serif;font-size:12px;width:200px;outline:none;}
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
.hidden{display:none !important;}.panel{display:none;}.panel.active{display:block;}
\"\"\"

JS = \"\"\"
let cf='all',cs='',ct='top50';
function switchTab(t,b){ct=t;document.querySelectorAll('.tb').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));document.getElementById('p-'+t).classList.add('active');af();}
function setFilter(f,b){cf=f;document.querySelectorAll('.fb').forEach(x=>x.classList.remove('active'));b.classList.add('active');af();}
function doSearch(v){cs=v.toLowerCase();af();}
function af(){const id=ct==='nocost'?'b-nocost':ct==='all'?'b-all':'b-top50';document.querySelectorAll('#'+id+' tr').forEach(r=>{const s=r.dataset.status||'',v=r.dataset.vendor||'';let mf=cf==='all'||(['out','critical','low','ok','untracked'].includes(cf)?s===cf:v.includes(cf));r.classList.toggle('hidden',!(mf&&(!cs||r.textContent.toLowerCase().includes(cs))));});}
\"\"\"

TH = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\\"num\\">Stock</th><th class=\\"num\\">Vel/Mo</th><th class=\\"num\\">Wks</th><th class=\\"num\\">Cost</th><th class=\\"num\\">Margin</th><th class=\\"num\\">Revenue</th>"
TH2 = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\\"num\\">Stock</th><th class=\\"num\\">Vel/Mo</th><th class=\\"num\\">Revenue</th>"

html = f\"\"\"<!DOCTYPE html>
<html lang=\\"en\\"><head><meta charset=\\"UTF-8\\"><meta name=\\"viewport\\" content=\\"width=device-width,initial-scale=1.0\\">
<title>Woof Gang - Inventory Dashboard</title>
<link href=\\"https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap\\" rel=\\"stylesheet\\">
<style>{CSS}</style></head><body>
<div class=\\"hdr\\"><div><h1>&#128062; Woof Gang Port Washington - Inventory Dashboard</h1><p>Retail stock levels, velocity &amp; reorder analysis</p></div><div class=\\"hdr-r\\">Updated {now}</div></div>
<div class=\\"sum-row\\">
  <div class=\\"sc s-out\\"><div class=\\"v\\">{out_count}</div><div class=\\"l\\">Out of Stock</div></div>
  <div class=\\"sc s-crit\\"><div class=\\"v\\">{critical_count}</div><div class=\\"l\\">Critical (1-2)</div></div>
  <div class=\\"sc s-low\\"><div class=\\"v\\">{low_count}</div><div class=\\"l\\">Low Stock</div></div>
  <div class=\\"sc s-ok\\"><div class=\\"v\\">{ok_count}</div><div class=\\"l\\">Adequately Stocked</div></div>
  <div class=\\"sc s-nc\\"><div class=\\"v\\">{no_cost_count}</div><div class=\\"l\\">Missing Cost</div></div>
</div>
<div class=\\"vs\\"><h2>Orders by Vendor</h2><div class=\\"vg\\">{vendor_cards}</div></div>
<div class=\\"ctrl\\">
  <button class=\\"tb active\\" onclick=\\"switchTab('top50',this)\\">Top 50 Items</button>
  <button class=\\"tb\\" onclick=\\"switchTab('all',this)\\">All Retail Items</button>
  <button class=\\"tb\\" onclick=\\"switchTab('nocost',this)\\">Missing Cost</button>
</div>
<div class=\\"filters\\">
  <button class=\\"fb active\\" onclick=\\"setFilter('all',this)\\">All</button>
  <button class=\\"fb\\" onclick=\\"setFilter('out',this)\\">Out of Stock</button>
  <button class=\\"fb\\" onclick=\\"setFilter('critical',this)\\">Critical</button>
  <button class=\\"fb\\" onclick=\\"setFilter('low',this)\\">Low</button>
  <button class=\\"fb\\" onclick=\\"setFilter('ok',this)\\">OK</button>
  <button class=\\"fb\\" onclick=\\"setFilter('woof gang',this)\\">Woof Gang</button>
  <button class=\\"fb\\" onclick=\\"setFilter('pfx',this)\\">PFX</button>
  <button class=\\"fb\\" onclick=\\"setFilter('fauna',this)\\">Fauna</button>
  <input class=\\"sb\\" type=\\"text\\" placeholder=\\"Search name or SKU...\\" oninput=\\"doSearch(this.value)\\">
</div>
<div class=\\"tw\\">
  <div id=\\"p-top50\\" class=\\"panel active\\"><table><thead><tr>{TH}</tr></thead><tbody id=\\"b-top50\\">{top50_rows}</tbody></table></div>
  <div id=\\"p-all\\" class=\\"panel\\"><table><thead><tr>{TH}</tr></thead><tbody id=\\"b-all\\">{all_rows}</tbody></table></div>
  <div id=\\"p-nocost\\" class=\\"panel\\"><table><thead><tr>{TH2}</tr></thead><tbody id=\\"b-nocost\\">{nocost_rows}</tbody></table></div>
</div>
<script>{JS}</script></body></html>\"\"\"

out_path = OUTPUT_DIR / "WoofGang_PortWashington_Inventory_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"SKUs: {len(results)} | Out: {out_count} | Critical: {critical_count} | Low: {low_count} | OK: {ok_count} | No cost: {no_cost_count}")
""".strip())

with open("/Users/julieschorr/Desktop/store-analysis/scripts/generate_inventory_dashboard.py", "w") as f:
    f.write(content)
print(f"Written: {len(content)} chars")
