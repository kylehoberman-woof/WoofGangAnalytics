"""Booking Anomaly Detector Dashboard — Woof Gang

Flags dogs whose recent grooming visits deviate from their historical patterns.
Uses pet_visits.json (per-dog visit history from FranPOS customer history API)
for exact per-dog accuracy — no clustering, no false positives from multi-dog households.

Usage:
    python3 scripts/build_booking_anomaly_dashboard.py                  # Port Washington
    python3 scripts/build_booking_anomaly_dashboard.py hicksville        # Hicksville
"""

import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from config import get_store, PORTAL_BACK_JS, get_store_display, get_store_fn, get_other_stores
from formatting import esc

# ── Store setup ───────────────────────────────────────────────────────────────

_store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
_store = get_store(_store_name)
_store_display = get_store_display(_store_name)
_fn_display = get_store_fn(_store_name)
_home_url = "../index.html"
_other_keys = get_other_stores(_store_name)
_other_store = get_store_display(_other_keys[0]) if _other_keys else ""
_other_dir = f"../{_other_keys[0]}" if _other_keys else ".."
_other_fn = get_store_fn(_other_keys[0]) if _other_keys else ""
_switch_url = f"{_other_dir}/WoofGang_{_other_fn}_BookingAnomalies.html" if _other_keys else ""

DATA_DIR = _store.data_dir
OUTPUT_DIR = _store.output_dir
NOW_STR = datetime.now(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p ET")

# ── Algorithm constants ───────────────────────────────────────────────────────

MIN_VISITS = 3            # min visits to build a profile
STALE_DAYS = 90           # ignore anomalies older than this many days
SERVICE_THRESHOLD = 0.75  # ≥75% of visits must be modal service to flag a change
SIZE_THRESHOLD = 0.75     # ≥75% of visits must be modal size to flag a change

SIZE_ORDER = {"XS": 0, "SM": 1, "MD": 2, "LG": 3, "XL": 4}

# ── Load pet visit history ────────────────────────────────────────────────────

_pet_visits_file = DATA_DIR / "pet_visits.json"

if not _pet_visits_file.exists():
    print(f"ERROR: {_pet_visits_file} not found — run fetch_pet_visits.py first")
    sys.exit(1)

print(f"Loading pet visit history for {_store_display}...")
with open(_pet_visits_file) as f:
    pet_records = json.load(f)

print(f"  {len(pet_records)} pet accounts loaded")

# ── Detect anomalies ──────────────────────────────────────────────────────────

today_str = date.today().isoformat()
cutoff_str = (date.today() - timedelta(days=STALE_DAYS)).isoformat()

anomalies = []
profiles = {}

for rec in pet_records:
    visits = rec.get("visits", [])
    if len(visits) < MIN_VISITS:
        continue

    # Skip if most recent visit is stale
    last_visit = visits[0]  # sorted desc
    if last_visit.get("date", "") < cutoff_str:
        continue

    pet_name = rec.get("pet_name", "").strip()
    owner_name = rec.get("owner_name", "").strip()
    dog_key = str(rec["pet_cid"])

    # Build history arrays
    sizes = [v["size"] for v in visits if v.get("size")]
    services = [v["service"] for v in visits if v.get("service")]
    breed_groups = [v["breed_group"] for v in visits if v.get("breed_group")]

    if not sizes and not services:
        continue

    # Modal values
    modal_size = Counter(sizes).most_common(1)[0][0] if sizes else ""
    modal_service = Counter(services).most_common(1)[0][0] if services else ""
    modal_breed = Counter(breed_groups).most_common(1)[0][0] if breed_groups else ""

    last_size = last_visit.get("size", "")
    last_service = last_visit.get("service", "")
    last_breed = last_visit.get("breed_group", "")
    last_groomer = last_visit.get("stylist", "")
    last_price = last_visit.get("price") or 0

    size_counts = dict(Counter(sizes))
    service_counts = dict(Counter(services))

    flags = []
    flag_details = {}

    # 1. Breed group change: General ↔ Poodle-Doodle
    if modal_breed and last_breed and last_breed != modal_breed:
        breed_modal_pct = Counter(breed_groups)[modal_breed] / len(breed_groups)
        if breed_modal_pct >= SERVICE_THRESHOLD:
            flags.append("breed_change")
            flag_details["breed_change"] = {"from": modal_breed, "to": last_breed}

    # 2. Size change: must differ by ≥1 step AND modal size must dominate
    if modal_size and last_size and last_size != modal_size:
        if modal_size in SIZE_ORDER and last_size in SIZE_ORDER:
            gap = abs(SIZE_ORDER[last_size] - SIZE_ORDER[modal_size])
            size_modal_pct = Counter(sizes)[modal_size] / len(sizes)
            if gap >= 1 and size_modal_pct >= SIZE_THRESHOLD:
                direction = "↑" if SIZE_ORDER[last_size] > SIZE_ORDER[modal_size] else "↓"
                flags.append("size_change")
                flag_details["size_change"] = {
                    "from": modal_size, "to": last_size,
                    "direction": direction, "gap": gap,
                }

    # 3. Service change: ≥75% of history is modal service, last visit differs
    if modal_service and last_service and last_service != modal_service:
        modal_pct = Counter(services)[modal_service] / len(services)
        if modal_pct >= SERVICE_THRESHOLD:
            flags.append("service_change")
            flag_details["service_change"] = {"from": modal_service, "to": last_service}

    # 4. Pricing anomaly: price deviates >30% from dog's own average for same service+size
    if last_price and last_price > 0:
        same_svc_prices = [
            v["price"] for v in visits[1:]  # exclude last visit itself
            if v.get("price") and v.get("price_match") in ("exact",)
            and v.get("service") == last_service
            and v.get("size") == last_size
        ]
        if len(same_svc_prices) >= 2:
            avg_price = sum(same_svc_prices) / len(same_svc_prices)
            pct_diff = abs(last_price - avg_price) / avg_price if avg_price else 0
            if pct_diff >= 0.30 and last_visit.get("price_match") == "exact":
                flags.append("price_anomaly")
                flag_details["price_anomaly"] = {
                    "expected": round(avg_price, 2),
                    "actual": round(last_price, 2),
                    "pct_diff": round(pct_diff * 100, 1),
                    "direction": "↑" if last_price > avg_price else "↓",
                }

    if not flags:
        continue

    # Build full history for modal (most recent first)
    history = []
    for v in visits:
        history.append({
            "date": v.get("date", ""),
            "service": v.get("service", ""),
            "size": v.get("size", ""),
            "breed_group": v.get("breed_group", ""),
            "items_raw": v.get("items_raw", ""),
            "groomer": v.get("stylist", ""),
            "price": round(v["price"], 2) if v.get("price") else 0,
            "price_match": v.get("price_match", ""),
        })

    anomalies.append({
        "cid": dog_key,
        "dog_key": dog_key,
        "customer_name": owner_name,
        "pet_name": pet_name,
        "visit_count": len(visits),
        "modal_size": modal_size,
        "modal_size_short": modal_size,
        "modal_doodle": modal_breed.lower() in ("poodle", "doodle", "poodle-doodle"),
        "modal_service": modal_service,
        "modal_breed": modal_breed,
        "size_counts": size_counts,
        "service_counts": service_counts,
        "last_date": last_visit.get("date", ""),
        "last_size": last_size,
        "last_size_short": last_size,
        "last_doodle": last_breed.lower() in ("poodle", "doodle", "poodle-doodle"),
        "last_service": last_service,
        "last_breed": last_breed,
        "last_groomer": last_groomer,
        "last_price": round(float(last_price), 2),
        "flags": flags,
        "flag_details": flag_details,
    })
    profiles[dog_key] = history


def _flag_severity(flags):
    if "breed_change" in flags:
        return 0
    if "size_change" in flags:
        return 1
    return 2


anomalies.sort(key=lambda x: (x["last_date"], _flag_severity(x["flags"])))
anomalies.reverse()  # most recent first

# Count by type
n_breed = sum(1 for a in anomalies if "breed_change" in a["flags"])
n_size = sum(1 for a in anomalies if "size_change" in a["flags"])
n_service = sum(1 for a in anomalies if "service_change" in a["flags"])
n_price = sum(1 for a in anomalies if "price_anomaly" in a["flags"])
n_total = len(anomalies)

# Collect all groomers for filter dropdown
all_groomers = sorted(set(a["last_groomer"] for a in anomalies if a["last_groomer"] and a["last_groomer"] != "Unknown"))

print(f"  {n_total} anomalies found: {n_breed} breed, {n_size} size, {n_service} service")

# Serialize for JS
import json as _json
ANOMALIES_JS = _json.dumps(anomalies, ensure_ascii=False)
PROFILES_JS = _json.dumps(profiles, ensure_ascii=False)
STORE_KEY_JS = _json.dumps(_store_name)


# ── HTML generation ───────────────────────────────────────────────────────────

groomer_options = "\n".join(
    f'<option value="{esc(g)}">{esc(g)}</option>' for g in all_groomers
)

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Woof Gang {esc(_store_display)} — Booking Anomalies</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f4f0;color:#1a1a2e}}

/* ── Header ── */
.header{{background:linear-gradient(135deg,#1B6B6B 0%,#6B3520 100%);color:white;padding:40px 0 30px;text-align:center;position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(196,39,110,0.15) 0%,transparent 50%)}}
.header h1{{font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px}}
.header .subtitle{{font-size:1rem;font-weight:400;opacity:0.9;position:relative}}
.header .brand-tag{{display:inline-block;background:#C4276E;color:white;padding:4px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-top:12px;position:relative}}
.header-timestamp{{position:absolute;top:12px;right:20px;font-size:0.78rem;opacity:0.85;font-weight:400;z-index:1;text-align:right}}

/* ── Topbar ── */
.topbar{{background:#C4276E;padding:14px 32px;color:white;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3);display:flex;justify-content:space-between;align-items:center}}
.home-link{{color:rgba(255,255,255,0.85);text-decoration:none;font-size:0.88rem;font-weight:600}}
.home-link:hover{{color:white}}
.topbar-center{{font-size:0.95rem;font-weight:700;color:white}}
.topbar-right{{font-size:0.78rem;color:rgba(255,255,255,0.75)}}

/* ── Tabs ── */
.tabs{{background:white;border-bottom:2px solid #eee;position:sticky;top:50px;z-index:99;display:flex;padding:0 24px;flex-wrap:wrap;overflow-x:auto}}
.tab{{padding:14px 18px;border:none;background:transparent;color:#999;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;font-family:inherit;white-space:nowrap;transition:color 0.15s}}
.tab:hover{{color:#C4276E}}
.tab.active{{color:#C4276E;border-bottom-color:#C4276E}}

/* ── Page & layout ── */
.page{{max-width:1400px;margin:0 auto;padding:24px 24px 60px}}

/* ── KPI cards ── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{background:white;border-radius:12px;padding:16px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,0.05)}}
.kpi-val{{font-size:2rem;font-weight:700;line-height:1}}
.kpi-label{{font-size:0.73rem;color:#999;margin-top:6px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em}}
.kpi.red{{border-top:3px solid #e53935}}
.kpi.yellow{{border-top:3px solid #F9A825}}
.kpi.orange{{border-top:3px solid #e65100}}
.kpi.gray{{border-top:3px solid #aaa}}
.kpi.red .kpi-val{{color:#e53935}}
.kpi.yellow .kpi-val{{color:#F9A825}}
.kpi.orange .kpi-val{{color:#e65100}}

/* ── Filters ── */
.filter-bar{{background:white;border-radius:12px;padding:14px 18px;margin-bottom:20px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;box-shadow:0 1px 4px rgba(0,0,0,0.05)}}
.filter-label{{font-size:0.73rem;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:0.05em;margin-right:4px}}
.day-btns{{display:flex;gap:6px}}
.day-btn{{padding:6px 12px;border:1.5px solid #ddd;border-radius:20px;background:white;color:#666;font-size:0.8rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all 0.15s}}
.day-btn:hover,.day-btn.active{{border-color:#C4276E;background:#C4276E;color:white}}
.filter-select{{padding:7px 12px;border:1.5px solid #ddd;border-radius:8px;font-family:inherit;font-size:0.84rem;background:white;cursor:pointer;color:#444}}
.filter-select:focus{{outline:none;border-color:#C4276E}}
.result-count{{font-size:0.8rem;color:#888;margin-left:auto}}

/* ── Card ── */
.card{{background:white;border-radius:14px;padding:22px;margin-bottom:22px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.stitle{{font-size:1.05rem;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.stitle::before{{content:'';display:inline-block;width:4px;height:18px;background:#C4276E;border-radius:2px}}

/* ── Table ── */
.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:0.83rem}}
th{{text-align:left;padding:9px 10px;color:#888;font-weight:600;font-size:0.73rem;text-transform:uppercase;letter-spacing:0.04em;border-bottom:2px solid #eee;white-space:nowrap;cursor:pointer;user-select:none}}
th:hover{{color:#C4276E}}
th.n{{text-align:right}}
th .sort-arrow{{opacity:0.4;font-size:0.7rem;margin-left:3px}}
th.sorted .sort-arrow{{opacity:1;color:#C4276E}}
td{{padding:9px 10px;border-bottom:1px solid #f5f0ec;vertical-align:middle}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
tr:hover td{{background:#fef9fb}}
thead th{{position:sticky;top:0;background:#f8f7f4;z-index:5}}
.empty-row td{{text-align:center;color:#aaa;padding:32px;font-style:italic}}

/* ── Badges ── */
.badge{{display:inline-block;padding:3px 9px;border-radius:12px;font-size:0.75rem;font-weight:600;white-space:nowrap;margin:1px}}
.badge.red{{background:#fce4ec;color:#c62828}}
.badge.yellow{{background:#fff9e6;color:#b45309}}
.badge.orange{{background:#fff3e0;color:#bf360c}}

/* ── CID link ── */
.cid-link{{color:#C4276E;text-decoration:none;font-weight:600;font-family:monospace;font-size:0.9rem}}
.cid-link:hover{{text-decoration:underline}}

/* ── Ack button ── */
.ack-btn{{padding:4px 12px;border:1.5px solid #1B6B6B;border-radius:8px;background:transparent;color:#1B6B6B;font-size:0.78rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all 0.15s}}
.ack-btn:hover{{background:#1B6B6B;color:white}}
.unack-btn{{padding:4px 12px;border:1.5px solid #aaa;border-radius:8px;background:transparent;color:#888;font-size:0.78rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all 0.15s}}
.unack-btn:hover{{border-color:#C4276E;color:#C4276E}}

/* ── Reviewed section toggle ── */
.reviewed-toggle{{padding:10px 16px;border:none;background:transparent;color:#999;font-size:0.85rem;font-weight:600;cursor:pointer;font-family:inherit;display:flex;align-items:center;gap:6px;width:100%;text-align:left}}
.reviewed-toggle:hover{{color:#555}}
.reviewed-inner{{display:none}}
.reviewed-inner.open{{display:block}}

/* ── Modal ── */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:1000;align-items:center;justify-content:center;padding:20px}}
.modal{{background:white;border-radius:16px;max-width:760px;width:100%;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.25)}}
.modal-header{{padding:20px 24px 14px;border-bottom:2px solid #eee;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}}
.modal-title{{font-size:1.05rem;font-weight:700}}
.modal-close{{background:none;border:none;font-size:1.5rem;cursor:pointer;color:#999;line-height:1;padding:0 4px}}
.modal-close:hover{{color:#C4276E}}
.modal-body{{overflow-y:auto;padding:20px 24px}}
.modal-profile{{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px;padding:14px;background:#fdf4f8;border-radius:10px}}
.modal-stat{{font-size:0.83rem}}
.modal-stat strong{{display:block;color:#1a1a2e;font-size:1rem;font-weight:700}}
.modal-stat span{{color:#888;font-size:0.73rem;text-transform:uppercase;font-weight:600}}
.modal table{{width:100%;font-size:0.83rem}}
.modal th{{text-align:left;padding:8px 10px;border-bottom:2px solid #eee;font-size:0.72rem;color:#888;text-transform:uppercase;letter-spacing:0.04em;font-weight:600}}
.modal td{{padding:8px 10px;border-bottom:1px solid #f5f0ec}}
.modal td.n{{text-align:right}}
tr.anomaly-row td{{background:#fff9c4 !important;font-weight:600}}
tr.anomaly-row td:first-child::before{{content:"⚠️ ";font-style:normal}}

/* ── Info banner ── */
.info-banner{{background:#e8f5e9;border:1px solid #a5d6a7;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:0.84rem;color:#1b5e20;line-height:1.5}}

@media(max-width:700px){{
  .header{{padding:28px 16px 24px}}
  .header h1{{font-size:1.6rem}}
  .page{{padding:16px 12px 40px}}
  .filter-bar{{gap:10px}}
  .modal-profile{{gap:14px}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-timestamp">Updated {esc(NOW_STR)}<br><a href="{esc(_switch_url)}" style="color:rgba(255,255,255,0.7);text-decoration:none;font-size:0.78rem">&#x21C4; {esc(_other_store)}</a></div>
  <h1>Woof Gang {esc(_store_display)}</h1>
  <div class="subtitle">Booking Anomaly Detector</div>
  <div class="brand-tag">Woof Gang Bakery &amp; Grooming</div>
</div>

<div class="topbar">
  <a id="portal-back" href="{esc(_home_url)}" class="home-link">&larr; Home</a>{PORTAL_BACK_JS}
  <span class="topbar-center">Booking Anomalies</span>
  <span class="topbar-right">{n_total} anomalies detected</span>
</div>

<div class="tabs">
  <button class="tab active" onclick="setTab('all',this)">All Anomalies ({n_total})</button>
  <button class="tab" onclick="setTab('breed_change',this)">&#x1F534; Breed Changes ({n_breed})</button>
  <button class="tab" onclick="setTab('size_change',this)">&#x1F7E1; Size Changes ({n_size})</button>
  <button class="tab" onclick="setTab('service_change',this)">&#x1F7E0; Service Changes ({n_service})</button>
  <button class="tab" onclick="setTab('price_anomaly',this)">&#x1F4B2; Price Anomalies ({n_price})</button>
</div>

<div class="page">

<div class="info-banner">
  <strong>How this works:</strong> Each dog with ≥{MIN_VISITS} grooming visits is analyzed for pattern deviations. Data comes from per-pet visit history (not owner accounts) — multi-dog households are tracked separately by dog.
  🔴 <strong>Breed</strong> = General ↔ Poodle-Doodle switch &nbsp;|&nbsp;
  🟡 <strong>Size</strong> = size category changed (XS/SM/MD/LG/XL) when ≥75% of history was one size &nbsp;|&nbsp;
  🟠 <strong>Service</strong> = service type switched when ≥75% of history was one type &nbsp;|&nbsp;
  💲 <strong>Price</strong> = last price deviated &gt;30% from this dog's own average for same service+size.
  Click any row to see full visit history. Acknowledge resolved items to hide them.
</div>

<div class="kpi-grid">
  <div class="kpi red"><div class="kpi-val" id="kpi-breed">{n_breed}</div><div class="kpi-label">&#x1F534; Breed Changes</div></div>
  <div class="kpi yellow"><div class="kpi-val" id="kpi-size">{n_size}</div><div class="kpi-label">&#x1F7E1; Size Changes</div></div>
  <div class="kpi orange"><div class="kpi-val" id="kpi-service">{n_service}</div><div class="kpi-label">&#x1F7E0; Service Changes</div></div>
  <div class="kpi gray"><div class="kpi-val" id="kpi-price">{n_price}</div><div class="kpi-label">&#x1F4B2; Price Anomalies</div></div>
  <div class="kpi gray"><div class="kpi-val" id="kpi-total">{n_total}</div><div class="kpi-label">Total Anomalies</div></div>
</div>

<div class="filter-bar">
  <span class="filter-label">Last:</span>
  <div class="day-btns">
    <button class="day-btn" onclick="setDays(30,this)">30d</button>
    <button class="day-btn active" onclick="setDays(60,this)">60d</button>
    <button class="day-btn" onclick="setDays(90,this)">90d</button>
    <button class="day-btn" onclick="setDays(0,this)">All Time</button>
  </div>
  <span class="filter-label">Groomer:</span>
  <select class="filter-select" id="groomer-select" onchange="setGroomer(this.value)">
    <option value="">All Groomers</option>
    {groomer_options}
  </select>
  <span class="result-count" id="result-count"></span>
</div>

<div class="card">
  <div class="stitle" id="table-title">All Anomalies</div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th onclick="sortBy('cid')">ID <span class="sort-arrow">↕</span></th>
          <th onclick="sortBy('customer_name')">Customer <span class="sort-arrow">↕</span></th>
          <th onclick="sortBy('pet_name')">Pet <span class="sort-arrow">↕</span></th>
          <th class="n" onclick="sortBy('visit_count')">Visits <span class="sort-arrow">↕</span></th>
          <th onclick="sortBy('modal_size_short')">History (Mode) <span class="sort-arrow">↕</span></th>
          <th onclick="sortBy('last_size_short')">Last Visit <span class="sort-arrow">↕</span></th>
          <th>Change</th>
          <th onclick="sortBy('last_groomer')">Groomer <span class="sort-arrow">↕</span></th>
          <th onclick="sortBy('last_date')" id="th-date" class="sorted">Date <span class="sort-arrow">↓</span></th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="anomaly-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Reviewed / Acknowledged Section -->
<div class="card" id="reviewed-card" style="display:none">
  <button class="reviewed-toggle" onclick="toggleReviewed(this)">
    <span id="reviewed-arrow">▶</span> ✅ Reviewed / Acknowledged (<span id="reviewed-count">0</span>)
  </button>
  <div class="reviewed-inner" id="reviewed-inner">
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Customer</th>
            <th>Pet</th>
            <th class="n">Visits</th>
            <th>History</th>
            <th>Last Visit</th>
            <th>Change</th>
            <th>Groomer</th>
            <th>Date</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="acked-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

</div><!-- .page -->

<!-- Customer History Modal -->
<div id="modal-overlay" class="modal-overlay" onclick="closeModal()">
  <div class="modal" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title">Customer #<span id="modal-cid"></span> &mdash; Visit History</div>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="modal-body">
      <div class="modal-profile" id="modal-profile"></div>
      <div class="tbl-wrap">
        <table class="modal-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Service</th>
              <th>Size</th>
              <th>Type</th>
              <th>Groomer</th>
              <th class="n">Price</th>
            </tr>
          </thead>
          <tbody id="modal-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
var ANOMALIES = {ANOMALIES_JS};
var PROFILES = {PROFILES_JS};
var STORE_KEY = {STORE_KEY_JS};

var _tab = 'all';
var _days = 60;
var _groomer = '';
var _sortField = 'last_date';
var _sortDir = -1;  // -1 = descending

// ── Size short display ──────────────────────────────────────────────────────
var SIZE_LABELS = {{
  '0-20 lbs': 'SM', '21-40 lbs': 'MD', '41-75 lbs': 'LG',
  '76-100 lbs': 'XLG', 'Over 100 lbs': 'XXLG'
}};
function shortSize(s) {{ return SIZE_LABELS[s] || s || '?'; }}

// ── Flag badge HTML ─────────────────────────────────────────────────────────
function flagBadge(f, details) {{
  if (f === 'breed_change') {{
    var d = details.breed_change;
    return '<span class="badge red">&#x1F534; ' + (d ? d.from + '→' + d.to : 'Breed') + '</span>';
  }}
  if (f === 'size_change') {{
    var d = details.size_change;
    return '<span class="badge yellow">&#x1F7E1; ' + (d ? d.from + d.direction + d.to : 'Size') + '</span>';
  }}
  if (f === 'service_change') {{
    var d = details.service_change;
    var from = d ? (d.from || '').split(' ')[0] : '';
    var to = d ? (d.to || '').split(' ')[0] : '';
    return '<span class="badge orange">&#x1F7E0; ' + from + '→' + to + '</span>';
  }}
  if (f === 'price_anomaly') {{
    var d = details.price_anomaly;
    return '<span class="badge yellow">&#x1F4B2; $' + (d ? d.actual.toFixed(0) + ' vs avg $' + d.expected.toFixed(0) : 'Price') + '</span>';
  }}
  return '';
}}

// ── localStorage helpers ────────────────────────────────────────────────────
function getAcked() {{
  try {{ return JSON.parse(localStorage.getItem('wg_acked_' + STORE_KEY) || '{{}}'); }}
  catch(e) {{ return {{}}; }}
}}
function isAcked(cid) {{ return !!getAcked()[cid]; }}
function ack(cid) {{
  var a = getAcked(); a[cid] = {{at: new Date().toISOString()}};
  localStorage.setItem('wg_acked_' + STORE_KEY, JSON.stringify(a));
  render();
}}
function unack(cid) {{
  var a = getAcked(); delete a[cid];
  localStorage.setItem('wg_acked_' + STORE_KEY, JSON.stringify(a));
  render();
}}

// ── Filter helpers ──────────────────────────────────────────────────────────
function setTab(tab, el) {{
  _tab = tab;
  document.querySelectorAll('.tab').forEach(function(t) {{ t.classList.remove('active'); }});
  if (el) el.classList.add('active');
  var labels = {{all:'All Anomalies',breed_change:'&#x1F534; Breed Changes',size_change:'&#x1F7E1; Size Changes',service_change:'&#x1F7E0; Service Changes'}};
  document.getElementById('table-title').innerHTML = labels[tab] || 'Anomalies';
  render();
}}
function setDays(days, el) {{
  _days = days;
  document.querySelectorAll('.day-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  if (el) el.classList.add('active');
  render();
}}
function setGroomer(val) {{ _groomer = val; render(); }}
function sortBy(field) {{
  if (_sortField === field) {{ _sortDir *= -1; }}
  else {{ _sortField = field; _sortDir = (field === 'last_date') ? -1 : 1; }}
  // Update header arrows
  document.querySelectorAll('th').forEach(function(th) {{
    th.classList.remove('sorted');
    var arr = th.querySelector('.sort-arrow');
    if (arr) arr.textContent = '↕';
  }});
  // Find the header with onclick matching field
  document.querySelectorAll('th[onclick]').forEach(function(th) {{
    if (th.getAttribute('onclick').includes("'" + field + "'")) {{
      th.classList.add('sorted');
      var arr = th.querySelector('.sort-arrow');
      if (arr) arr.textContent = _sortDir > 0 ? '↑' : '↓';
    }}
  }});
  render();
}}

function toggleReviewed(btn) {{
  var inner = document.getElementById('reviewed-inner');
  var arrow = document.getElementById('reviewed-arrow');
  inner.classList.toggle('open');
  arrow.textContent = inner.classList.contains('open') ? '▼' : '▶';
}}

// ── Filter anomalies ────────────────────────────────────────────────────────
function getFiltered(excludeAcked) {{
  var today = new Date();
  var cutoffStr = '';
  if (_days > 0) {{
    var cutoff = new Date(today.getTime() - _days * 86400 * 1000);
    cutoffStr = cutoff.toISOString().slice(0,10);
  }}
  return ANOMALIES.filter(function(a) {{
    if (_days > 0 && a.last_date < cutoffStr) return false;
    if (_tab !== 'all' && a.flags.indexOf(_tab) === -1) return false;
    if (_groomer && a.last_groomer !== _groomer) return false;
    if (excludeAcked && isAcked(a.dog_key || a.cid)) return false;
    return true;
  }});
}}

// ── Render table rows ───────────────────────────────────────────────────────
function makeRow(a, isAckedRow) {{
  var histDisplay = (a.modal_doodle ? 'Poodle-Doodle' : 'General') +
    ' / ' + a.modal_size_short +
    ' <span style="color:#aaa;font-size:0.75rem">(' + (a.size_counts[a.modal_size] || 0) + '/' + a.visit_count + ')</span>';
  var lastDisplay = (a.last_doodle ? 'Poodle-Doodle' : 'General') + ' / ' + a.last_size_short;
  var flagsHtml = a.flags.map(function(f) {{ return flagBadge(f, a.flag_details); }}).join(' ');
  var dk = a.dog_key || a.cid;
  var actionBtn = isAckedRow
    ? '<button class="unack-btn" onclick="unack(\\'' + dk + '\\')">↩ Undo</button>'
    : '<button class="ack-btn" onclick="ack(\\'' + dk + '\\')">&#x2713; Ack</button>';
  var cidShort = a.cid.length > 8 ? '#…' + a.cid.slice(-6) : '#' + a.cid;
  return '<tr>' +
    '<td><a class="cid-link" href="#" onclick="openModal(\\'' + dk + '\\');return false;">' + cidShort + '</a></td>' +
    '<td>' + (a.customer_name || '') + '</td>' +
    '<td>' + (a.pet_name || '') + '</td>' +
    '<td class="n">' + a.visit_count + '</td>' +
    '<td>' + histDisplay + '</td>' +
    '<td>' + lastDisplay + '</td>' +
    '<td>' + flagsHtml + '</td>' +
    '<td>' + (a.last_groomer || '') + '</td>' +
    '<td>' + a.last_date + '</td>' +
    '<td>' + actionBtn + '</td>' +
    '</tr>';
}}

// ── Main render ─────────────────────────────────────────────────────────────
function render() {{
  var active = getFiltered(true);  // exclude acked
  var acked = ANOMALIES.filter(function(a) {{
    if (_days > 0) {{
      var cutoff = new Date(Date.now() - _days * 86400 * 1000).toISOString().slice(0,10);
      if (a.last_date < cutoff) return false;
    }}
    if (_tab !== 'all' && a.flags.indexOf(_tab) === -1) return false;
    if (_groomer && a.last_groomer !== _groomer) return false;
    return isAcked(a.dog_key || a.cid);
  }});

  // Sort active
  active.sort(function(a, b) {{
    var av = a[_sortField], bv = b[_sortField];
    if (av == null) av = ''; if (bv == null) bv = '';
    if (typeof av === 'string') return _sortDir * av.localeCompare(bv);
    return _sortDir * (av - bv);
  }});

  var tbody = document.getElementById('anomaly-tbody');
  if (active.length === 0) {{
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No anomalies match current filters.</td></tr>';
  }} else {{
    tbody.innerHTML = active.map(function(a) {{ return makeRow(a, false); }}).join('');
  }}

  // Result count
  document.getElementById('result-count').textContent = active.length + ' anomali' + (active.length === 1 ? 'y' : 'es');

  // Reviewed section
  var reviewedCard = document.getElementById('reviewed-card');
  var ackedTbody = document.getElementById('acked-tbody');
  document.getElementById('reviewed-count').textContent = acked.length;
  if (acked.length > 0) {{
    reviewedCard.style.display = '';
    ackedTbody.innerHTML = acked.map(function(a) {{ return makeRow(a, true); }}).join('');
  }} else {{
    reviewedCard.style.display = 'none';
  }}

  // Update KPI counts
  var countAll = getFiltered(false);
  document.getElementById('kpi-total').textContent = countAll.length;
  document.getElementById('kpi-breed').textContent = countAll.filter(function(a) {{ return a.flags.indexOf('breed_change') !== -1; }}).length;
  document.getElementById('kpi-size').textContent = countAll.filter(function(a) {{ return a.flags.indexOf('size_change') !== -1; }}).length;
  document.getElementById('kpi-service').textContent = countAll.filter(function(a) {{ return a.flags.indexOf('service_change') !== -1; }}).length;
  document.getElementById('kpi-price').textContent = countAll.filter(function(a) {{ return a.flags.indexOf('price_anomaly') !== -1; }}).length;
}}

// ── Modal ────────────────────────────────────────────────────────────────────
function openModal(dogKey) {{
  var anomaly = ANOMALIES.find(function(a) {{ return (a.dog_key || a.cid) === dogKey; }});
  var history = PROFILES[dogKey] || [];
  var displayId = anomaly ? anomaly.cid : dogKey;

  document.getElementById('modal-cid').textContent = displayId;

  // Profile summary
  var profHtml = '';
  if (anomaly) {{
    if (anomaly.customer_name) profHtml += '<div class="modal-stat"><strong>' + anomaly.customer_name + '</strong><span>Customer</span></div>';
    if (anomaly.pet_name) profHtml += '<div class="modal-stat"><strong>' + anomaly.pet_name + '</strong><span>Pet Name</span></div>';
    profHtml += '<div class="modal-stat"><strong>' + anomaly.visit_count + '</strong><span>Visits</span></div>';
    profHtml += '<div class="modal-stat"><strong>' + (anomaly.modal_doodle ? 'Poodle-Doodle' : 'General') + ' / ' + anomaly.modal_size_short + '</strong><span>Usual</span></div>';
    profHtml += '<div class="modal-stat"><strong>' + (anomaly.last_doodle ? 'Poodle-Doodle' : 'General') + ' / ' + anomaly.last_size_short + '</strong><span>Last Visit</span></div>';
    var flagsHtml = anomaly.flags.map(function(f) {{ return flagBadge(f, anomaly.flag_details); }}).join(' ');
    profHtml += '<div class="modal-stat"><strong>' + flagsHtml + '</strong><span>Flags</span></div>';
  }}
  document.getElementById('modal-profile').innerHTML = profHtml;

  var lastDate = anomaly ? anomaly.last_date : '';
  var tbody = document.getElementById('modal-tbody');
  tbody.innerHTML = '';

  history.forEach(function(v) {{
    var isAnomaly = (v.date === lastDate);
    var tr = document.createElement('tr');
    if (isAnomaly) tr.classList.add('anomaly-row');
    tr.innerHTML =
      '<td>' + v.date + '</td>' +
      '<td>' + (v.service || '') + '</td>' +
      '<td>' + (v.size || '?') + '</td>' +
      '<td>' + (v.breed_group || '') + '</td>' +
      '<td>' + (v.groomer || '') + '</td>' +
      '<td class="n">' + (v.price && v.price > 0 ? '$' + v.price.toFixed(2) : '—') + '</td>';
    tbody.appendChild(tr);
  }});

  document.getElementById('modal-overlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  document.getElementById('modal-overlay').style.display = 'none';
  document.body.style.overflow = '';
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeModal();
}});

// ── Init ─────────────────────────────────────────────────────────────────────
render();
</script>

</body>
</html>'''

# ── Write output ──────────────────────────────────────────────────────────────

out_file = OUTPUT_DIR / f"WoofGang_{_fn_display}_BookingAnomalies.html"
out_file.write_text(html, encoding="utf-8")
print(f"  Written to: {out_file}")
print(f"Done! {n_total} anomalies: {n_breed} breed, {n_size} size, {n_service} service, {n_price} price")
