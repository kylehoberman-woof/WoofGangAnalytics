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
    print(f"WARNING: {pet_visits_file} not found — skipping pet dashboard build")
    out_html = data_dir.parent / f"WoofGang_{store_fn}_PetDashboard.html"
    out_html.write_text(f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Pet Dashboard — {store_label}</title></head>
<body style="font-family:sans-serif;padding:40px;color:#444">
<h2>🐾 Pet Dashboard — {store_label}</h2>
<p style="color:#888">Pet visit data is being fetched for the first time. This dashboard will be available after the next nightly update.</p>
</body></html>""", encoding="utf-8")
    sys.exit(0)

with open(pet_visits_file) as f:
    pet_records = json.load(f)

print(f"Loaded {len(pet_records)} pet records")

today = date.today()

def esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')

def badge(text, color):
    return f'<span style="background:{color};color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600">{text}</span>'

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

# ── Lapsed / At-Risk dogs ─────────────────────────────────────────────────────
# For each pet with ≥3 visits, compute average interval between visits.
# Flag as At Risk if days_since_last > 1.5x avg interval, Lapsed if > 2x.
today_date = date.today()
lapsed_dogs = []
lapse_history = {}  # pet_cid → list of past visits, for the click-through detail modal

for rec in pet_records:
    visits = [v for v in rec.get("visits", []) if v.get("date")]
    if len(visits) < 3:
        continue
    last_visit_str = visits[0]["date"]
    days_since = (today_date - date.fromisoformat(last_visit_str)).days

    # Compute average interval from sorted visit dates
    visit_dates = sorted([date.fromisoformat(v["date"]) for v in visits], reverse=True)
    intervals = [(visit_dates[i] - visit_dates[i+1]).days for i in range(len(visit_dates)-1)]
    avg_interval = sum(intervals) / len(intervals)

    if avg_interval < 7:  # skip if avg interval is unrealistically short
        continue

    ratio = days_since / avg_interval
    if ratio < 1.5:
        continue  # still on schedule

    status = "Lapsed" if ratio >= 2.0 else "At Risk"
    days_overdue = int(days_since - avg_interval)
    preferred_groomer = visits[0].get("stylist", "") or ""

    lapsed_dogs.append({
        "pet_cid": str(rec.get("pet_cid", "")),
        "pet_name": rec.get("pet_name", ""),
        "owner_name": rec.get("owner_name", ""),
        "owner_phone": rec.get("owner_phone", ""),
        "last_visit": last_visit_str,
        "days_since": days_since,
        "days_overdue": days_overdue,
        "avg_interval": round(avg_interval),
        "visit_count": len(visits),
        "status": status,
        "preferred_groomer": preferred_groomer,
        "last_service": visits[0].get("service", ""),
        "size": visits[0].get("size", ""),
        "ratio": ratio,
    })

    lapse_history[str(rec.get("pet_cid", ""))] = [
        {
            "date": v.get("date", ""),
            "service": v.get("service", "") or v.get("items_raw", ""),
            "size": v.get("size", ""),
            "groomer": v.get("stylist", ""),
        }
        for v in visits[:25]
    ]

# Sort: lapsed first, then by days overdue descending
lapsed_dogs.sort(key=lambda x: (-("Lapsed" in x["status"]), -x["days_overdue"]))

# ── Build HTML ────────────────────────────────────────────────────────────────
SEVERITY_COLOR = {"high": "#dc2626", "medium": "#d97706", "low": "#6b7280"}

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

n_lapsed = sum(1 for d in lapsed_dogs if d["status"] == "Lapsed")
n_at_risk = sum(1 for d in lapsed_dogs if d["status"] == "At Risk")

winback_rows = []
for d in lapsed_dogs:
    status_color = "#dc2626" if d["status"] == "Lapsed" else "#d97706"
    freq_str = f"Every ~{d['avg_interval']} days ({d['avg_interval']//7}w)" if d['avg_interval'] >= 7 else f"Every ~{d['avg_interval']} days"
    phone = d["owner_phone"]
    phone_link = f'<a href="tel:{phone}" style="color:var(--pink);text-decoration:none">{phone}</a>' if phone else "—"
    cid = esc(d["pet_cid"])
    winback_rows.append(f"""
      <tr class="lapse-row" data-cid="{cid}" data-last-visit="{esc(d['last_visit'])}" data-pet-name="{esc(d['pet_name'])}">
        <td><button class="lc-name-btn" onclick="openLapseDetail('{cid}')">{esc(d['pet_name'])}</button><br><small style="color:var(--muted)">{esc(d['size'])} · {esc(d['last_service'])}</small></td>
        <td>{esc(d['owner_name'])}<br><small>{phone_link}</small></td>
        <td style="color:{status_color};font-weight:700">{d['status']}</td>
        <td>{d['last_visit']}<br><small style="color:var(--muted)">{d['days_since']}d ago</small></td>
        <td>{freq_str}<br><small style="color:{status_color}">{d['days_overdue']}d overdue</small></td>
        <td><small>{esc(d['preferred_groomer'])}</small></td>
        <td>{d['visit_count']}</td>
        <td class="lapse-log-cell">
          <button class="lc-log-btn" onclick="openLapseDetail('{cid}')">Log call</button>
          <div class="lc-log-status">Not yet contacted</div>
        </td>
      </tr>""")

# Plain (non-f-string) CSS/JS chunks for the Lapse Calls tab — kept separate from
# the surrounding f-string so their literal { } don't need doubling.
LAPSE_CSS = """
  .lc-filter-bar { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:16px; padding:12px; background:#f9fafb; border-radius:8px; }
  .lc-filter-bar label { font-size:12px; color:var(--muted); display:flex; align-items:center; gap:6px; }
  .lc-filter-bar input[type=date] { padding:4px 6px; border:1px solid var(--border); border-radius:6px; font-size:13px; }
  .lc-preset-btn { background:var(--brown); color:#fff; border:none; padding:6px 12px; border-radius:6px; font-size:12px; cursor:pointer; }
  .lc-preset-btn:hover { opacity:.85; }
  .lc-range-count { font-size:12px; color:var(--muted); margin-left:auto; }
  .lc-check { display:flex; align-items:center; gap:5px; font-size:11px; color:var(--text); white-space:nowrap; margin-bottom:3px; }
  .lc-name-btn { background:none; border:none; padding:0; margin:0; font:inherit; font-weight:700; color:var(--pink); cursor:pointer; text-decoration:underline; text-align:left; }
  .lc-name-btn:hover { opacity:.75; }
  .lapse-log-cell { min-width:150px; }
  .lc-log-btn { background:var(--brown); color:#fff; border:none; padding:5px 12px; border-radius:6px; font-size:11px; cursor:pointer; }
  .lc-log-btn:hover { opacity:.85; }
  .lc-log-status { font-size:11px; color:var(--muted); margin-top:5px; max-width:180px; }
  .lapse-row.lc-hidden { display:none; }

  .lc-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:1000; align-items:flex-start; justify-content:center; padding:5vh 16px; overflow-y:auto; }
  .lc-modal-overlay.active { display:flex; }
  .lc-modal { background:#fff; border-radius:12px; max-width:640px; width:100%; padding:24px; position:relative; }
  .lc-modal-close { position:absolute; top:14px; right:16px; background:none; border:none; font-size:22px; line-height:1; cursor:pointer; color:var(--muted); }
  .lc-modal-close:hover { color:var(--text); }
  .lc-modal h2 { font-size:20px; color:var(--brown); margin-bottom:2px; }
  .lc-modal-sub { font-size:12px; color:var(--muted); margin-bottom:18px; }
  .lc-modal-section { margin-top:18px; }
  .lc-modal-section h3 { font-size:13px; color:var(--brown); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px; }
  .lc-check-grid { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:10px; }
  .lc-check-grid label { font-size:13px; display:flex; align-items:center; gap:6px; }
  .lc-modal textarea { width:100%; font-size:13px; font-family:inherit; border:1px solid var(--border); border-radius:8px; padding:8px 10px; resize:vertical; }
  .lc-modal-actions { display:flex; align-items:center; gap:10px; margin-top:10px; }
  .lc-save-btn { background:var(--pink); color:#fff; border:none; padding:7px 16px; border-radius:6px; font-size:12px; font-weight:600; cursor:pointer; }
  .lc-save-btn:hover { opacity:.85; }
  .lc-saved-indicator { font-size:12px; color:#16a34a; }
  .lc-history-table th, .lc-history-table td { font-size:12px; padding:6px 10px; }
  .lc-history-empty { color:#999; text-align:center; padding:16px; font-size:13px; }
"""

LAPSE_JS = """
var LC_STORE = "__STORE__";
var PET_HISTORY = __PET_HISTORY__;
var LC_SB  = 'https://bqzinttbjeeaybywhhet.supabase.co/rest/v1';
var LC_SK  = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxemludHRiamVlYXlieXdoaGV0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MDU3NDUsImV4cCI6MjA4OTI4MTc0NX0.B2MqUy_WEWOo8NVpGxHibuh-8xLklsy3Ux4DnXp9zmQ';
var LC_SHD = {'apikey':LC_SK,'Authorization':'Bearer '+LC_SK,'Content-Type':'application/json'};

function lcEsc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function lcGet(p){ return fetch(LC_SB+p, {headers:LC_SHD}).then(function(r){ return r.json(); }); }
function lcUpsert(body){
  return fetch(LC_SB+'/lapse_calls?on_conflict=store,pet_cid', {
    method:'POST',
    headers:Object.assign({}, LC_SHD, {'Prefer':'resolution=merge-duplicates,return=representation'}),
    body: JSON.stringify(body)
  }).then(function(r){
    if(!r.ok) return r.json().catch(function(){ return null; }).then(function(err){
      throw new Error((err && err.message) || ('HTTP ' + r.status));
    });
    return r.json();
  });
}

var lcStatusByCid = {};
var lcCurrentCid = null;

function findLapseRow(cid){
  var rows = document.querySelectorAll('.lapse-row');
  for(var i=0;i<rows.length;i++){
    if(rows[i].getAttribute('data-cid') === cid) return rows[i];
  }
  return null;
}

function loadLapseCalls(){
  lcGet('/lapse_calls?store=eq.'+encodeURIComponent(LC_STORE)).then(function(rows){
    lcStatusByCid = {};
    (Array.isArray(rows)?rows:[]).forEach(function(r){ lcStatusByCid[r.pet_cid] = r; });
    applyLapseStatuses();
  }).catch(function(){});
}

function lcStatusSummary(rec){
  if(!rec || !rec.contacted) return 'Not yet contacted';
  var bits = [];
  if(rec.talked_to_customer) bits.push('talked to customer');
  if(rec.left_voicemail) bits.push('left voicemail');
  if(rec.booked) bits.push('booked ✓');
  return bits.length ? ('✓ Contacted — ' + bits.join(', ')) : '✓ Contacted';
}

function updateLogStatus(cid){
  var tr = findLapseRow(cid);
  if(!tr) return;
  var el = tr.querySelector('.lc-log-status');
  if(el) el.textContent = lcStatusSummary(lcStatusByCid[cid]);
}

function applyLapseStatuses(){
  document.querySelectorAll('.lapse-row').forEach(function(tr){
    updateLogStatus(tr.getAttribute('data-cid'));
  });
  updateCalledCount();
}

function openLapseDetail(cid){
  lcCurrentCid = cid;
  var tr = findLapseRow(cid);
  var name = tr ? tr.getAttribute('data-pet-name') : '';
  var rec = lcStatusByCid[cid] || {};

  document.getElementById('lc-modal-header').innerHTML =
    '<h2>' + lcEsc(name) + '</h2><div class="lc-modal-sub">' + lcStatusSummary(rec) + '</div>';

  document.getElementById('lc-m-contacted').checked = !!rec.contacted;
  document.getElementById('lc-m-talked').checked = !!rec.talked_to_customer;
  document.getElementById('lc-m-voicemail').checked = !!rec.left_voicemail;
  document.getElementById('lc-m-booked').checked = !!rec.booked;
  document.getElementById('lc-m-notes').value = rec.notes || '';
  document.getElementById('lc-m-indicator').textContent = '';

  var hist = PET_HISTORY[cid] || [];
  var body = document.getElementById('lc-modal-history');
  body.innerHTML = hist.length
    ? hist.map(function(v){
        return '<tr><td>' + lcEsc(v.date) + '</td><td>' + lcEsc(v.service) + '</td><td>' + lcEsc(v.size) + '</td><td>' + lcEsc(v.groomer) + '</td></tr>';
      }).join('')
    : '<tr><td colspan=4 class="lc-history-empty">No visit history on file</td></tr>';

  document.getElementById('lc-modal-overlay').classList.add('active');
}

function closeLapseDetail(){
  document.getElementById('lc-modal-overlay').classList.remove('active');
  lcCurrentCid = null;
}

function saveLapseDetail(){
  if(!lcCurrentCid) return;
  var cid = lcCurrentCid;
  var tr = findLapseRow(cid);
  var body = {
    store: LC_STORE,
    pet_cid: cid,
    pet_name: tr ? tr.getAttribute('data-pet-name') : '',
    contacted: document.getElementById('lc-m-contacted').checked,
    talked_to_customer: document.getElementById('lc-m-talked').checked,
    left_voicemail: document.getElementById('lc-m-voicemail').checked,
    booked: document.getElementById('lc-m-booked').checked,
    notes: document.getElementById('lc-m-notes').value,
    call_date: new Date().toISOString().slice(0,10)
  };
  var ind = document.getElementById('lc-m-indicator');
  ind.textContent = 'Saving…';
  lcUpsert(body).then(function(res){
    var saved = Array.isArray(res) ? res[0] : res;
    if(saved) lcStatusByCid[cid] = saved;
    ind.textContent = '✓ saved';
    updateLogStatus(cid);
    updateCalledCount();
  }).catch(function(err){
    ind.textContent = 'Error: ' + (err && err.message ? err.message : 'save failed');
  });
}

function updateCalledCount(){
  var visible = Array.prototype.slice.call(document.querySelectorAll('.lapse-row:not(.lc-hidden)'));
  var logged = visible.filter(function(tr){
    var cid = tr.getAttribute('data-cid');
    return lcStatusByCid[cid] && lcStatusByCid[cid].contacted;
  }).length;
  var el = document.getElementById('lc-called-count');
  if(el) el.textContent = logged;
}

function filterLapseRows(){
  var from = document.getElementById('lc-from').value;
  var to = document.getElementById('lc-to').value;
  var rows = document.querySelectorAll('.lapse-row');
  var shown = 0;
  rows.forEach(function(tr){
    var lv = tr.getAttribute('data-last-visit');
    var visible = true;
    if(from && lv && lv < from) visible = false;
    if(to && lv && lv > to) visible = false;
    tr.classList.toggle('lc-hidden', !visible);
    if(visible) shown++;
  });
  var rc = document.getElementById('lc-range-count');
  if(rc) rc.textContent = shown + ' of ' + rows.length + ' shown';
  updateCalledCount();
}

function setLapseRange(days){
  var to = new Date();
  var from = new Date();
  from.setDate(from.getDate() - days);
  document.getElementById('lc-to').value = to.toISOString().slice(0,10);
  document.getElementById('lc-from').value = from.toISOString().slice(0,10);
  filterLapseRows();
}

function clearLapseRange(){
  document.getElementById('lc-from').value = '';
  document.getElementById('lc-to').value = '';
  filterLapseRows();
}

loadLapseCalls();
filterLapseRows();
""".replace("__STORE__", store_name).replace(
    "__PET_HISTORY__", json.dumps(lapse_history).replace("</", "<\\/")
)

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
{LAPSE_CSS}
</style>
</head>
<body>
<header>
  <h1>🐾 Pet Dashboard — {store_label}</h1>
  <nav>
    <button class="tab-btn active" onclick="showTab('daily')">Daily Appointments</button>
    <button class="tab-btn" onclick="showTab('lapse')">Lapse Calls ({len(lapsed_dogs)})</button>
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
    <div class="stat" style="border-color:#dc2626"><div class="val" style="color:#dc2626">{n_lapsed}</div><div class="lbl">Lapsed dogs</div></div>
    <div class="stat" style="border-color:#d97706"><div class="val" style="color:#d97706">{n_at_risk}</div><div class="lbl">At-risk dogs</div></div>
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

<!-- LAPSE CALLS -->
<div class="section" id="tab-lapse">
  <div class="stat-row">
    <div class="stat" style="border-color:#dc2626"><div class="val" style="color:#dc2626">{n_lapsed}</div><div class="lbl">Lapsed</div><div class="updated">≥2× their usual interval</div></div>
    <div class="stat" style="border-color:#d97706"><div class="val" style="color:#d97706">{n_at_risk}</div><div class="lbl">At Risk</div><div class="updated">1.5–2× their usual interval</div></div>
    <div class="stat"><div class="val">{len(lapsed_dogs)}</div><div class="lbl">Total needing outreach</div></div>
    <div class="stat" style="border-color:#16a34a"><div class="val" style="color:#16a34a" id="lc-called-count">—</div><div class="lbl">Contacted in range</div></div>
  </div>
  <div class="card">
    <h2>Lapse Calls — Outreach List</h2>
    <p style="color:#6b7280;font-size:13px;margin-bottom:16px">
      Dogs overdue based on their own historical visit frequency. Lapsed = gone 2× longer than usual. At Risk = 1.5×. Sorted by most overdue first.
      Filter to a batch by last-visit date (typically 6 weeks at a time), work the calls, and log the outcome for each dog.
    </p>
    <div class="lc-filter-bar">
      <label>Last visit from <input type="date" id="lc-from" onchange="filterLapseRows()"></label>
      <label>to <input type="date" id="lc-to" onchange="filterLapseRows()"></label>
      <button class="lc-preset-btn" onclick="setLapseRange(42)">Last 6 Weeks</button>
      <button class="lc-preset-btn" onclick="clearLapseRange()">Show All</button>
      <span class="lc-range-count" id="lc-range-count"></span>
    </div>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Dog</th><th>Owner / Phone</th><th>Status</th><th>Last Visit</th><th>Frequency</th><th>Usual Groomer</th><th>Visits</th><th>Call Log</th></tr></thead>
      <tbody id="lc-tbody">{''.join(winback_rows) if winback_rows else '<tr><td colspan=8 style="color:#999;text-align:center;padding:24px">No lapsed or at-risk dogs</td></tr>'}</tbody>
    </table>
    </div>
    <p style="color:#9ca3af;font-size:12px;margin-top:10px">Click a dog's name to see its full appointment history and log a call.</p>
  </div>
</div>

<!-- LAPSE CALL DETAIL MODAL -->
<div class="lc-modal-overlay" id="lc-modal-overlay" onclick="if(event.target===this) closeLapseDetail()">
  <div class="lc-modal">
    <button class="lc-modal-close" onclick="closeLapseDetail()">&times;</button>
    <div id="lc-modal-header"></div>
    <div class="lc-modal-section">
      <h3>Call Log</h3>
      <div class="lc-check-grid">
        <label><input type="checkbox" id="lc-m-contacted"> Contacted</label>
        <label><input type="checkbox" id="lc-m-talked"> Talked to customer</label>
        <label><input type="checkbox" id="lc-m-voicemail"> Left voicemail</label>
        <label><input type="checkbox" id="lc-m-booked"> Booked</label>
      </div>
      <textarea id="lc-m-notes" placeholder="Notes…" rows="4"></textarea>
      <div class="lc-modal-actions">
        <button class="lc-save-btn" onclick="saveLapseDetail()">Save</button>
        <span class="lc-saved-indicator" id="lc-m-indicator"></span>
      </div>
    </div>
    <div class="lc-modal-section">
      <h3>Appointment History</h3>
      <div style="overflow-x:auto">
      <table class="lc-history-table">
        <thead><tr><th>Date</th><th>Service</th><th>Size</th><th>Groomer</th></tr></thead>
        <tbody id="lc-modal-history"></tbody>
      </table>
      </div>
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
{LAPSE_JS}
</script>
</body>
</html>"""

out_html = data_dir.parent / f"WoofGang_{store_fn}_PetDashboard.html"
with open(out_html, "w") as f:
    f.write(html)

print(f"Dashboard written → {out_html}")
