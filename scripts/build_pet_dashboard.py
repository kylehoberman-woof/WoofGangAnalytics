"""Build per-pet appointment dashboard with anomaly detection.

Reads pet_visits.json + all_data.json to produce:
  1. Daily appointments view — dogs groomed by day, groomer assignments
  2. Pet profiles — visit history per dog, frequency, last seen
  3. Anomaly detection — service/size mismatches, pricing anomalies, double-books

Output: {store}/WoofGang_{Store}_PetDashboard.html

Usage:
    python3 scripts/build_pet_dashboard.py
    python3 scripts/build_pet_dashboard.py hicksville
"""

import json, sys, re
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, get_store_display, get_store_fn

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
data_dir = store.data_dir
store_label = get_store_display(store_name)
store_fn = get_store_fn(store_name)

pet_visits_file = data_dir / "pet_visits.json"
all_data_file = data_dir / "all_data.json"

if not pet_visits_file.exists():
    print(f"ERROR: {pet_visits_file} not found — run fetch_pet_visits.py first")
    sys.exit(1)

with open(pet_visits_file) as f:
    pet_records = json.load(f)

print(f"Loaded {len(pet_records)} pet records")

# ── Build daily appointments index ───────────────────────────────────────────
# date → groomer → list of {pet_name, owner_name, service, breed_group, size, items_raw}
daily_by_groomer = defaultdict(lambda: defaultdict(list))
daily_totals = defaultdict(int)  # date → dog count

all_visits_flat = []
for rec in pet_records:
    for v in rec.get("visits", []):
        entry = {
            "date": v["date"],
            "datetime": v.get("datetime", ""),
            "pet_name": rec["pet_name"],
            "pet_cid": rec["pet_cid"],
            "owner_name": rec["owner_name"],
            "owner_phone": rec.get("owner_phone", ""),
            "stylist": v["stylist"],
            "service": v["service"],
            "breed_group": v["breed_group"],
            "size": v["size"],
            "items_raw": v["items_raw"],
            "salesperson": v.get("salesperson", ""),
        }
        all_visits_flat.append(entry)
        if v["date"]:
            daily_by_groomer[v["date"]][v["stylist"]].append(entry)
            daily_totals[v["date"]] += 1

# ── Load order items for price join ──────────────────────────────────────────
# Join: date + stylist + service keyword → price
order_price_map = {}  # (date, stylist_short, service_key) → price
groomer_service_prices = defaultdict(list)  # (stylist, service, size) → [prices]

if all_data_file.exists():
    with open(all_data_file) as f:
        all_data = json.load(f)
    order_items = all_data if isinstance(all_data, list) else all_data.get("order_items", [])

    GROOM_KEYWORDS = {"full groom", "bath", "lux bath", "groom", "trim", "nail"}

    for item in order_items:
        name = (item.get("Name") or "").lower()
        if not any(k in name for k in GROOM_KEYWORDS):
            continue
        emp = (item.get("EmployeeName") or item.get("SalesPerson") or "").strip()
        dt = item.get("Date") or item.get("CreatedOn") or ""
        day = dt[:10] if dt else ""
        price = float(item.get("Price") or item.get("Total") or 0)
        if not day or not emp or price <= 0:
            continue

        # Normalize service key from name
        svc_key = "full groom" if "full groom" in name or "full" in name else \
                  "lux bath" if "lux" in name else \
                  "bath" if "bath" in name else name[:20]

        emp_short = emp.split()[0] + " " + emp.split()[-1][0] if " " in emp else emp

        order_price_map[(day, emp_short.lower(), svc_key)] = price

        # Size from name if present
        size = "XL" if "xl" in name or "x-large" in name else \
               "LG" if "lg" in name or "large" in name else \
               "MD" if "md" in name or "medium" in name else \
               "SM" if "sm" in name or "small" in name else ""
        groomer_service_prices[(emp_short, svc_key, size)].append(price)

    print(f"Loaded {len(order_price_map)} grooming order items for price join")

# ── Anomaly detection ─────────────────────────────────────────────────────────
anomalies = []

# 1. Double-book: same pet, same day, multiple visits
pet_day_visits = defaultdict(list)
for v in all_visits_flat:
    pet_day_visits[(v["pet_cid"], v["date"])].append(v)

for (cid, day), visits in pet_day_visits.items():
    if len(visits) > 1:
        anomalies.append({
            "type": "Double Visit",
            "severity": "high",
            "date": day,
            "pet": visits[0]["pet_name"],
            "owner": visits[0]["owner_name"],
            "detail": f"{len(visits)} visits on same day: " +
                      " | ".join(v["items_raw"] for v in visits),
        })

# 2. Service change: pet has inconsistent service history (e.g., always bath, suddenly full groom)
for rec in pet_records:
    visits = rec.get("visits", [])
    if len(visits) < 3:
        continue
    services = [v["service"].lower() for v in visits if v["service"]]
    if not services:
        continue
    from collections import Counter
    most_common_svc, count = Counter(services).most_common(1)[0]
    for v in visits[:3]:  # check recent visits
        if v["service"].lower() != most_common_svc and count >= len(visits) * 0.7:
            anomalies.append({
                "type": "Service Change",
                "severity": "medium",
                "date": v["date"],
                "pet": rec["pet_name"],
                "owner": rec["owner_name"],
                "detail": f"Usually gets '{most_common_svc}' but got '{v['service']}' on {v['date']}",
            })
            break

# 3. Size inconsistency: pet's size changed between visits
for rec in pet_records:
    visits = [v for v in rec.get("visits", []) if v.get("size")]
    if len(visits) < 2:
        continue
    sizes = [v["size"] for v in visits]
    from collections import Counter
    most_common_size, count = Counter(sizes).most_common(1)[0]
    if count < len(sizes):  # not all the same size
        size_set = set(sizes)
        if len(size_set) > 1:
            recent = visits[0]
            if recent["size"] != most_common_size:
                anomalies.append({
                    "type": "Size Mismatch",
                    "severity": "medium",
                    "date": recent["date"],
                    "pet": rec["pet_name"],
                    "owner": rec["owner_name"],
                    "detail": f"Usually '{most_common_size}' but recently charged as '{recent['size']}' on {recent['date']}",
                })

anomalies.sort(key=lambda x: (x["severity"] == "high", x["date"]), reverse=True)
print(f"Found {len(anomalies)} anomalies ({sum(1 for a in anomalies if a['severity']=='high')} high)")

# ── Groomer summary (last 30 days) ───────────────────────────────────────────
from collections import Counter
cutoff_30 = (today - timedelta(days=30)).isoformat()
groomer_stats = defaultdict(lambda: {"dogs": 0, "sizes": Counter(), "services": Counter(), "revenue": 0.0, "revenue_exact": 0})

for v in all_visits_flat:
    if v["date"] < cutoff_30:
        continue
    g = v["stylist"] or "Unknown"
    groomer_stats[g]["dogs"] += 1
    if v.get("size"):
        groomer_stats[g]["sizes"][v["size"]] += 1
    if v.get("service"):
        groomer_stats[g]["services"][v["service"]] += 1
    if v.get("price") and v.get("price_match") in ("exact",):
        groomer_stats[g]["revenue"] += v["price"]
        groomer_stats[g]["revenue_exact"] += 1

SIZE_ORDER = ["XS", "SM", "MD", "LG", "XL"]

groomer_summary_rows = []
for g, stats in sorted(groomer_stats.items(), key=lambda x: -x[1]["dogs"]):
    size_pills = " ".join(
        f'<span style="background:#f3f4f6;padding:1px 6px;border-radius:8px;font-size:11px">{sz}:{cnt}</span>'
        for sz in SIZE_ORDER for cnt in [stats["sizes"].get(sz, 0)] if cnt > 0
    )
    top_svc = stats["services"].most_common(1)[0][0] if stats["services"] else "—"
    rev_str = f'${stats["revenue"]:.0f} <span style="color:#9ca3af;font-size:11px">({stats["revenue_exact"]} matched)</span>' if stats["revenue"] else "—"
    groomer_summary_rows.append(f"""
      <tr>
        <td><strong>{esc(g)}</strong></td>
        <td><strong>{stats['dogs']}</strong></td>
        <td>{size_pills}</td>
        <td><small>{esc(top_svc)}</small></td>
        <td>{rev_str}</td>
      </tr>""")

# ── Recent 30 days for daily view ─────────────────────────────────────────────
today = date.today()
recent_dates = sorted(
    [d for d in daily_totals if d >= (today - timedelta(days=30)).isoformat()],
    reverse=True
)

# ── Top pets by visit count ───────────────────────────────────────────────────
top_pets = sorted(
    [r for r in pet_records if r.get("total_visits", 0) > 0],
    key=lambda x: x.get("total_visits", 0),
    reverse=True
)[:50]

# ── Build HTML ────────────────────────────────────────────────────────────────
SEVERITY_COLOR = {"high": "#dc2626", "medium": "#d97706", "low": "#6b7280"}

def badge(text, color):
    return f'<span style="background:{color};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600">{text}</span>'

def esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')

daily_rows = []
for d in recent_dates[:30]:
    groomers = daily_by_groomer[d]
    dog_count = daily_totals[d]
    groomer_cells = []
    for groomer, visits in sorted(groomers.items()):
        dogs = ", ".join(f"{v['pet_name']} ({v['size'] or '?'})" for v in visits)
        groomer_cells.append(f"<div class='groomer-cell'><strong>{esc(groomer)}</strong><br><small>{esc(dogs)}</small></div>")
    daily_rows.append(f"""
      <tr>
        <td><strong>{d}</strong></td>
        <td><strong>{dog_count}</strong></td>
        <td>{''.join(groomer_cells)}</td>
      </tr>""")

anomaly_rows = []
for a in anomalies[:100]:
    sev_color = SEVERITY_COLOR.get(a["severity"], "#6b7280")
    anomaly_rows.append(f"""
      <tr>
        <td>{badge(a['type'], sev_color)}</td>
        <td>{esc(a['date'])}</td>
        <td><strong>{esc(a['pet'])}</strong><br><small>{esc(a['owner'])}</small></td>
        <td><small>{esc(a['detail'])}</small></td>
      </tr>""")

pet_rows = []
for r in top_pets:
    last = r.get("last_visit", "")
    days_ago = (today - date.fromisoformat(last)).days if last else 999
    recency = badge("Recent", "#16a34a") if days_ago <= 30 else \
              badge("Active", "#2563eb") if days_ago <= 60 else \
              badge("At Risk", "#d97706") if days_ago <= 90 else \
              badge("Lapsed", "#dc2626")
    recent_svc = r["visits"][0]["items_raw"] if r.get("visits") else "—"
    recent_groomer = r["visits"][0]["stylist"] if r.get("visits") else "—"
    pet_rows.append(f"""
      <tr>
        <td><strong>{esc(r['pet_name'])}</strong></td>
        <td><small>{esc(r['owner_name'])}</small></td>
        <td>{r.get('total_visits',0)}</td>
        <td>{last}<br>{recency}</td>
        <td><small>{esc(recent_svc)}</small></td>
        <td><small>{esc(recent_groomer)}</small></td>
      </tr>""")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pet Dashboard — {store_label}</title>
<style>
  :root {{
    --brown: #2C1A0E;
    --pink: #E8006A;
    --bg: #fdf8f5;
    --card: #fff;
    --border: #e5e7eb;
    --text: #1f2937;
    --muted: #6b7280;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); font-size: 14px; }}
  header {{ background: var(--brown); color: #fff; padding: 16px 24px;
            display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100; }}
  header h1 {{ font-size: 18px; font-weight: 700; }}
  nav {{ display: flex; gap: 8px; margin-left: auto; }}
  nav a {{ color: rgba(255,255,255,0.7); text-decoration: none; font-size: 13px;
           padding: 6px 12px; border-radius: 6px; }}
  nav a:hover {{ background: rgba(255,255,255,0.1); color: #fff; }}
  .tab-btn {{ background: rgba(255,255,255,0.15); color: #fff; border: none;
              padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; }}
  .tab-btn.active {{ background: var(--pink); }}
  main {{ max-width: 1400px; margin: 0 auto; padding: 24px 16px; }}
  .section {{ display: none; }}
  .section.active {{ display: block; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
           padding: 20px; margin-bottom: 20px; }}
  .card h2 {{ font-size: 16px; font-weight: 700; margin-bottom: 14px; color: var(--brown); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: var(--brown); color: #fff; padding: 10px 12px; text-align: left; font-size: 12px; font-weight: 600; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #fef9f5; }}
  .groomer-cell {{ background: #f9fafb; border-radius: 6px; padding: 6px 8px; margin-bottom: 4px; font-size: 12px; }}
  .stat-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }}
  .stat {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
           padding: 16px 20px; flex: 1; min-width: 140px; }}
  .stat .val {{ font-size: 28px; font-weight: 700; color: var(--brown); }}
  .stat .lbl {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .updated {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  @media(max-width:600px) {{ th,td {{ padding: 8px 6px; font-size: 12px; }} }}
</style>
</head>
<body>
<header>
  <h1>🐾 Pet Dashboard — {store_label}</h1>
  <nav>
    <button class="tab-btn active" onclick="showTab('daily')">Daily Appointments</button>
    <button class="tab-btn" onclick="showTab('anomalies')">Anomalies ({len(anomalies)})</button>
    <button class="tab-btn" onclick="showTab('pets')">Pet Profiles</button>
  </nav>
</header>
<main>

<!-- DAILY -->
<div class="section active" id="tab-daily">
  <div class="stat-row">
    <div class="stat"><div class="val">{len(pet_records)}</div><div class="lbl">Pet accounts</div></div>
    <div class="stat"><div class="val">{sum(1 for r in pet_records if r.get('last_visit','') >= (today - timedelta(days=30)).isoformat())}</div><div class="lbl">Active last 30d</div></div>
    <div class="stat"><div class="val">{len(all_visits_flat)}</div><div class="lbl">Total visits on record</div></div>
    <div class="stat"><div class="val">{len(anomalies)}</div><div class="lbl">Anomalies detected</div></div>
  </div>
  <div class="card">
    <h2>Groomer Summary — Last 30 Days</h2>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Groomer</th><th>Dogs</th><th>Size Breakdown</th><th>Top Service</th><th>Revenue (matched)</th></tr></thead>
      <tbody>{''.join(groomer_summary_rows) if groomer_summary_rows else '<tr><td colspan=5 style="color:#999;text-align:center;padding:24px">No data</td></tr>'}</tbody>
    </table>
    </div>
  </div>
  <div class="card">
    <h2>Appointments by Day (last 30 days)</h2>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Date</th><th>Dogs</th><th>Groomer Assignments</th></tr></thead>
      <tbody>{''.join(daily_rows) if daily_rows else '<tr><td colspan=3 style="color:#999;text-align:center;padding:24px">No visits in last 30 days</td></tr>'}</tbody>
    </table>
    </div>
  </div>
</div>

<!-- ANOMALIES -->
<div class="section" id="tab-anomalies">
  <div class="card">
    <h2>Anomaly Detection</h2>
    <p style="color:#6b7280;font-size:13px;margin-bottom:16px">
      Flags double visits, service changes, and size inconsistencies per dog.
    </p>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Type</th><th>Date</th><th>Pet / Owner</th><th>Detail</th></tr></thead>
      <tbody>{''.join(anomaly_rows) if anomaly_rows else '<tr><td colspan=4 style="color:#999;text-align:center;padding:24px">No anomalies detected</td></tr>'}</tbody>
    </table>
    </div>
  </div>
</div>

<!-- PET PROFILES -->
<div class="section" id="tab-pets">
  <div class="card">
    <h2>Pet Profiles (top {len(top_pets)} by visit count)</h2>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Pet</th><th>Owner</th><th>Visits</th><th>Last Visit</th><th>Last Service</th><th>Last Groomer</th></tr></thead>
      <tbody>{''.join(pet_rows)}</tbody>
    </table>
    </div>
  </div>
</div>

</main>
<script>
function showTab(name) {{
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body>
</html>"""

out_html = data_dir.parent / f"WoofGang_{store_fn}_PetDashboard.html"
with open(out_html, "w") as f:
    f.write(html)

print(f"Dashboard written → {out_html}")
