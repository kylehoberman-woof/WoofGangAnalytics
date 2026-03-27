import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from config import get_store
from classifier import is_service, detect_vendor

import sys as _sys
_store_name = _sys.argv[1] if len(_sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = "Port Washington" if _store_name == "port-washington" else "Hicksville"
DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir

with open(DATA_DIR / "all_data.json") as f:
    all_data = json.load(f)
with open(DATA_DIR / "stock_levels.json") as f:
    stock_levels = json.load(f)

# Load real brand data from FranPOS (if available)
_brand_cache_path = DATA_DIR / "sku_brands.json"
_brand_cache = {}
if _brand_cache_path.exists():
    with open(_brand_cache_path) as f:
        _brand_cache = json.load(f)

def get_vendor(sku, name):
    """Get vendor/brand from FranPOS cache, fall back to heuristic."""
    info = _brand_cache.get(sku)
    if info and isinstance(info, dict) and info.get("brand"):
        return info["brand"]
    return detect_vendor(sku, name)

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
    results.append({"sku":sku,"name":d["name"] or sku,"stock":stock,"revenue":d["revenue"],
        "units":d["units"],"cost":d["cost"],"has_cost":has_cost,"margin":margin,
        "velocity_monthly":round(vel_monthly,2),"weeks_of_supply":wos,"vendor":d["vendor"]})

from datetime import timedelta
cutoff_90 = (datetime.today() - timedelta(days=90)).strftime("%Y-%m-%d")
active_skus = set()
for item in order_items:
    sku = str(item.get("Sku","")).strip()
    date = str(item.get("CreatedOn",""))[:10]
    if sku and date >= cutoff_90:
        active_skus.add(sku)

# Only keep SKUs active in last 90 days
results = [r for r in results if r["sku"] in active_skus]
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
    f'<td class="num">{"" if r["stock"] is None else str(int(r["stock"]))}</td>'
    f'<td class="num">{"-" if r["velocity_monthly"] <= 0 else str(round(r["velocity_monthly"],1))}</td>'
    f'<td class="num">${r["revenue"]:,.0f}</td></tr>'
    for r in results if not r["has_cost"]
)

from zoneinfo import ZoneInfo
et = ZoneInfo("America/New_York")
now = datetime.now(et).strftime("%B %d, %Y at %I:%M %p ET")
top50_rows = make_rows(top50)
all_rows = make_rows(results)

# Reorder recommendations (4-week supply)
REORDER_WEEKS = 4
reorder_by_vendor = defaultdict(list)
for r in results:
    if r["status"] not in ("out","critical","low"): continue
    if r["velocity_monthly"] <= 0: continue
    needed = max(0, (r["velocity_monthly"] / 4.33) * REORDER_WEEKS - max(0, r.get("stock",0)))
    if needed <= 0: continue
    est_cost = round(needed * r["cost"], 2) if r["cost"] else 0
    reorder_by_vendor[r["vendor"]].append({**r, "needed": round(needed,1), "est_cost": est_cost})

TH_REORDER = '<th>SKU</th><th>Product</th><th class="num">Stock</th><th class="num">Vel/Mo</th><th class="num">Need</th><th class="num">Cost</th><th class="num">Est.$</th><th>Status</th>'

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
        f'<td class="num" style="color:red">{r.get("stock",0):.1f}</td>'
        f'<td>{r["vendor"]}</td><td class="num">{r["velocity_monthly"]}</td></tr>')

TH_NEG = '<th>SKU</th><th>Product</th><th class="num">Stock</th><th>Vendor</th><th class="num">Vel/Mo</th>'

CSS = """
:root{--m:#C4276E;--ml:#FDF0F5;--t:#1B6B6B;--br:#6B3520;--dk:#1a1a2e;--md:#2d2d44;--tx:#1f2937;--mu:#6b7280;--bd:#e5e7eb;--bg:#f8f9fb;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--tx);}
.hdr{background:var(--m);padding:28px 40px 24px;display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid var(--m);}
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
/* Home link */
.home-link{color:rgba(255,255,255,.7);text-decoration:none;font-size:12px;font-weight:600;margin-right:12px;}
.home-link:hover{color:#fff;}
"""

JS = """
let cf='all',cs='',ct='top50';
function switchTab(t,b){ct=t;document.querySelectorAll('.tb').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));document.getElementById('p-'+t).classList.add('active');af();}
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
"""

TH = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\"num\">Stock</th><th class=\"num\">Vel/Mo</th><th class=\"num\">Wks</th><th class=\"num\">Cost</th><th class=\"num\">Margin</th><th class=\"num\">Revenue</th>"
TH2 = "<th>Status</th><th>SKU</th><th>Product</th><th>Vendor</th><th class=\"num\">Stock</th><th class=\"num\">Vel/Mo</th><th class=\"num\">Revenue</th>"

html = f"""<!DOCTYPE html>
<html lang=\"en\"><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">
<title>Woof Gang - Inventory Dashboard</title>
<link href=\"https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap\" rel=\"stylesheet\">
<style>{CSS}</style></head><body>
<div class=\"hdr\"><div><div style=\"display:flex;align-items:center;gap:8px\"><a href=\"index.html\" class=\"home-link\">&larr; Home</a><h1>&#128062; Woof Gang {_store_display} - Inventory Dashboard</h1></div><p>Retail stock levels, velocity &amp; reorder analysis</p></div><div class=\"hdr-r\">Updated {now}</div></div>
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
  <div id=\"p-reorder\" class=\"panel\"><h3 style=\"padding:8px 8px 16px\">Reorder Recommendations (4-week supply) &middot; Est. Total ${reorder_total:,.0f}</h3>{reorder_html}</div>
  <div id=\"p-negative\" class=\"panel\"><table><thead><tr>{TH_NEG}</tr></thead><tbody id=\"b-negative\">{neg_rows}</tbody></table></div>
</div>
<script>{JS}</script></body></html>"""

_fn_store = _store_display.replace(" ", "")
out_path = OUTPUT_DIR / f"WoofGang_{_fn_store}_Inventory_Dashboard.html"
with open(out_path, "w") as f:
    f.write(html)
print(f"Saved: {out_path}")
print(f"SKUs: {len(results)} | Out: {out_count} | Critical: {critical_count} | Low: {low_count} | OK: {ok_count} | No cost: {no_cost_count}")