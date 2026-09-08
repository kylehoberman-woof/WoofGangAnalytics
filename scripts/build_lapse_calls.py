"""Build the standalone Lapse Calls widget — a dedicated outreach tool for
sales associates, separate from the Pet Dashboard.

Reads pet_visits.json + all_data.json (same source as build_pet_dashboard.py,
via the shared lapse_calls_lib) to produce a list of dogs who had a real
visit, haven't been back since, and have nothing booked. Associates log a
call outcome per dog (Booked / Left Voicemail / Customer Will Book When
Ready / Not Interested) — each log is its own dated entry in Supabase
(lapse_call_log), not an overwritten status, so full call history is kept.
Only Left Voicemail routes a dog to the Needs Follow-up view; the other
three outcomes are resolved.

Output: {store}/WoofGang_{Store}_LapseCalls.html

Usage:
    python3 scripts/build_lapse_calls.py
    python3 scripts/build_lapse_calls.py hicksville
"""

import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store, get_store_display, get_store_fn, STORE_REGISTRY, PORTAL_BACK_JS
from lapse_calls_lib import (
    get_store_tag, filter_pet_records_to_store, load_customer_visit_staff,
    compute_lapse_candidates, group_records_by_dog,
)

store_name = sys.argv[1] if len(sys.argv) > 1 else "port-washington"
store = get_store(store_name)
data_dir = store.data_dir
store_label = get_store_display(store_name)
store_fn = get_store_fn(store_name)
store_tag = get_store_tag(store_name, STORE_REGISTRY)

pet_visits_file = data_dir / "pet_visits.json"
all_data_file = data_dir / "all_data.json"

out_html = data_dir.parent / f"WoofGang_{store_fn}_LapseCalls.html"

if not pet_visits_file.exists():
    print(f"WARNING: {pet_visits_file} not found — skipping lapse calls build")
    out_html.write_text(f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Lapse Calls — {store_label}</title></head>
<body style="font-family:sans-serif;padding:40px;color:#444">
<h2>📞 Lapse Calls — {store_label}</h2>
<p style="color:#888">Pet visit data is being fetched for the first time. This page will be available after the next nightly update.</p>
</body></html>""", encoding="utf-8")
    sys.exit(0)

with open(pet_visits_file) as f:
    pet_records = json.load(f)

filter_pet_records_to_store(pet_records, store_tag)
print(f"Loaded {len(pet_records)} pet records")

customer_visit_staff = load_customer_visit_staff(all_data_file)

lapsed_dogs, lapse_history = compute_lapse_candidates(pet_records, customer_visit_staff)
print(f"{len(lapsed_dogs)} lapse call candidates")

# Every dog, not just current candidates — a call log entry for a dog that
# has since been booked (the best outcome!) would otherwise have no owner
# name to show on the standalone Progress page.
cid_owner_map = {g["pet_cid"]: g["owner_name"] for g in group_records_by_dog(pet_records).values()}

n_lapsed = sum(1 for d in lapsed_dogs if d["status"] == "Lapsed")
n_at_risk = sum(1 for d in lapsed_dogs if d["status"] == "At Risk")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


rows = []
for d in lapsed_dogs:
    if d["status"] == "Lapsed":
        status_color, status_text = "#dc2626", "Lapsed"
    elif d["status"] == "At Risk":
        status_color, status_text = "#d97706", "At Risk"
    else:
        status_color, status_text = "#9ca3af", "—"

    if d["avg_interval"] is not None:
        freq_str = f"Every ~{d['avg_interval']} days ({d['avg_interval']//7}w)" if d['avg_interval'] >= 7 else f"Every ~{d['avg_interval']} days"
        overdue_html = f'<br><small style="color:{status_color}">{d["days_overdue"]}d overdue</small>' if d["days_overdue"] is not None else ""
    else:
        freq_str, overdue_html = "—", ""

    phone = d["owner_phone"]
    phone_link = f'<a href="tel:{phone}" style="color:var(--pink);text-decoration:none">{phone}</a>' if phone else "—"
    cid = esc(d["pet_cid"])

    if d["last_groomer"]:
        groomer_html = (
            f'<small>{esc(d["last_groomer"])}</small>' if d["last_groomer_confirmed"]
            else f'<small style="font-style:italic;color:var(--muted)" title="Stylist not recorded — showing front desk/checkout staff from that visit">{esc(d["last_groomer"])}*</small>'
        )
    else:
        groomer_html = '<small style="color:var(--muted)">—</small>'

    rows.append(f"""
      <tr class="lc-row" data-cid="{cid}" data-last-visit="{esc(d['last_visit'])}" data-pet-name="{esc(d['pet_name'])}" data-owner-name="{esc(d['owner_name'])}" data-owner-phone="{esc(d['owner_phone'])}" data-bucket="initial">
        <td><button class="lc-name-btn" onclick="openLapseDetail('{cid}')">{esc(d['pet_name'])}</button><br><small style="color:var(--muted)">{esc(d['size'])} · {esc(d['last_service'])}</small></td>
        <td>{esc(d['owner_name'])}<br><small>{phone_link}</small></td>
        <td style="color:{status_color};font-weight:700">{status_text}</td>
        <td>{d['last_visit']}<br><small style="color:var(--muted)">{d['days_since']}d ago</small></td>
        <td>{freq_str}{overdue_html}</td>
        <td>{groomer_html}</td>
        <td>{d['visit_count']}</td>
        <td class="lc-action-cell">
          <button class="lc-log-btn" onclick="openLapseDetail('{cid}')">Log call</button>
          <div class="lc-action-badge">Not yet contacted</div>
          <div class="lc-complaint-flag" style="display:none">⚠ Complaint on file</div>
        </td>
      </tr>""")

PET_HISTORY_JSON = json.dumps(lapse_history).replace("</", "<\\/")

CSS = """
:root { --brown:#2C1A0E; --pink:#E8006A; --bg:#fdf8f5; --card:#fff; --border:#e5e7eb; --text:#1f2937; --muted:#6b7280; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); font-size:14px; }
header { background:var(--brown); color:#fff; padding:16px 24px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; position:sticky; top:0; z-index:100; }
header h1 { font-size:18px; font-weight:700; }
header nav { display:flex; gap:8px; margin-left:auto; }
header nav a { color:rgba(255,255,255,0.75); text-decoration:none; font-size:13px; padding:6px 12px; border-radius:6px; }
header nav a:hover { background:rgba(255,255,255,0.12); color:#fff; }
main { max-width:1400px; margin:0 auto; padding:24px 16px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:20px; margin-bottom:20px; }
.card h2 { font-size:16px; font-weight:700; margin-bottom:14px; color:var(--brown); }
table { width:100%; border-collapse:collapse; }
th { background:var(--brown); color:#fff; padding:10px 12px; text-align:left; font-size:12px; font-weight:600; }
td { padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:#fef9f5; }
.bucket-row { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }
.bucket-btn { flex:1; min-width:160px; background:var(--card); border:2px solid var(--border); border-radius:10px; padding:16px 20px; text-align:left; cursor:pointer; font-family:inherit; }
.bucket-btn.active { border-color:var(--pink); background:#fff5fa; }
.bucket-btn .val { font-size:28px; font-weight:700; color:var(--brown); }
.bucket-btn .lbl { font-size:12px; color:var(--muted); margin-top:4px; }
.lc-filter-bar { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:16px; padding:12px; background:#f9fafb; border-radius:8px; }
.lc-filter-bar label { font-size:12px; color:var(--muted); display:flex; align-items:center; gap:6px; }
.lc-filter-bar input[type=date] { padding:4px 6px; border:1px solid var(--border); border-radius:6px; font-size:13px; }
.lc-filter-bar input[type=text] { padding:6px 10px; border:1px solid var(--border); border-radius:6px; font-size:13px; min-width:240px; }
.lc-preset-btn { background:var(--brown); color:#fff; border:none; padding:6px 12px; border-radius:6px; font-size:12px; cursor:pointer; }
.lc-preset-btn:hover { opacity:.85; }
.lc-range-count { font-size:12px; color:var(--muted); margin-left:auto; }
.lc-name-btn { background:none; border:none; padding:0; margin:0; font:inherit; font-weight:700; color:var(--pink); cursor:pointer; text-decoration:underline; text-align:left; }
.lc-name-btn:hover { opacity:.75; }
.lc-action-cell { min-width:160px; }
.lc-log-btn { background:var(--brown); color:#fff; border:none; padding:5px 12px; border-radius:6px; font-size:11px; cursor:pointer; }
.lc-log-btn:hover { opacity:.85; }
.lc-action-badge { font-size:11px; color:var(--muted); margin-top:5px; max-width:180px; font-weight:600; }
.lc-complaint-flag { font-size:11px; color:#dc2626; font-weight:700; margin-top:4px; }
.lc-row.lc-hidden { display:none; }
@media(max-width:600px) { th,td { padding:8px 6px; font-size:12px; } }

.lc-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:1000; align-items:flex-start; justify-content:center; padding:5vh 16px; overflow-y:auto; }
.lc-modal-overlay.active { display:flex; }
.lc-modal { background:#fff; border-radius:12px; max-width:640px; width:100%; padding:24px; position:relative; }
.lc-modal-close { position:absolute; top:14px; right:16px; background:none; border:none; font-size:22px; line-height:1; cursor:pointer; color:var(--muted); }
.lc-modal-close:hover { color:var(--text); }
.lc-modal h2 { font-size:20px; color:var(--brown); margin-bottom:2px; }
.lc-modal-owner { font-size:13px; color:var(--text); margin-bottom:4px; }
.lc-modal-phone { color:var(--pink); font-weight:600; text-decoration:none; }
.lc-modal-phone:hover { text-decoration:underline; }
.lc-modal-sub { font-size:12px; color:var(--muted); margin-bottom:18px; }
.lc-modal-section { margin-top:18px; }
.lc-modal-section h3 { font-size:13px; color:var(--brown); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px; }
.lc-action-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px; }
.lc-action-option { border:2px solid var(--border); background:#fff; border-radius:8px; padding:10px 12px; font-size:13px; font-weight:600; cursor:pointer; text-align:center; color:var(--text); }
.lc-action-option:hover { border-color:var(--pink); }
.lc-action-option.selected { border-color:var(--pink); background:#fff5fa; color:var(--pink); }
.lc-modal textarea, .lc-modal input[type=date] { width:100%; font-size:13px; font-family:inherit; border:1px solid var(--border); border-radius:8px; padding:8px 10px; }
.lc-modal textarea { resize:vertical; }
.lc-modal-field { margin-bottom:10px; }
.lc-modal-field label { font-size:11px; color:var(--muted); display:block; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }
.lc-modal-actions { display:flex; align-items:center; gap:10px; margin-top:10px; }
.lc-save-btn { background:var(--pink); color:#fff; border:none; padding:8px 18px; border-radius:6px; font-size:13px; font-weight:600; cursor:pointer; }
.lc-save-btn:hover { opacity:.85; }
.lc-save-btn:disabled { opacity:.4; cursor:not-allowed; }
.lc-saved-indicator { font-size:12px; color:#16a34a; }
.lc-history-table th, .lc-history-table td { font-size:12px; padding:6px 10px; }
.lc-history-empty { color:#999; text-align:center; padding:16px; font-size:13px; }
.lc-history-note { font-size:12px; color:var(--text); padding:6px 0; border-bottom:1px solid var(--border); }
.lc-history-note:last-child { border-bottom:none; }
.lc-history-note-date { color:var(--muted); font-weight:600; margin-right:6px; }
.lc-call-log-entry { background:#f9fafb; border-radius:8px; padding:10px 12px; margin-bottom:6px; font-size:12px; }
.lc-call-log-entry:last-child { margin-bottom:0; }
.lc-call-log-meta { display:flex; justify-content:space-between; margin-bottom:3px; }
.lc-call-log-date { color:var(--muted); font-weight:600; }
.lc-call-log-action { font-weight:700; }
.lc-call-log-notes { color:var(--text); }

.lc-modal-section.lc-complaints-section { background:#fef2f2; border:1px solid #fecaca; border-radius:10px; padding:14px 16px; margin-top:0; }
.lc-complaints-section h3 { color:#b91c1c; }
.lc-complaint-card { background:#fff; border:1px solid #fecaca; border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:12px; }
.lc-complaint-card:last-child { margin-bottom:0; }
.lc-complaint-meta { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }
.lc-complaint-desc { color:var(--text); margin-bottom:4px; }
.lc-complaint-res { color:var(--muted); font-style:italic; }

.lc-dnc-banner { background:#1f2937; color:#fff; border-radius:10px; padding:12px 16px; margin-top:14px; font-size:13px; display:flex; align-items:center; justify-content:space-between; gap:12px; }
.lc-dnc-banner strong { display:block; margin-bottom:2px; }
.lc-dnc-section { border:1px dashed var(--border); border-radius:10px; padding:14px 16px; margin-top:18px; }
.lc-dnc-toggle-btn { background:#1f2937; color:#fff; border:none; padding:8px 16px; border-radius:6px; font-size:12px; font-weight:600; cursor:pointer; }
.lc-dnc-toggle-btn:hover { opacity:.85; }
.lc-dnc-toggle-btn.lc-dnc-remove { background:#fff; color:#1f2937; border:1px solid var(--border); }
.lc-row.lc-dnc td { opacity:.55; }
"""

JS = """
var LC_STORE = "__STORE__";
var PET_HISTORY = __PET_HISTORY__;
var LC_SB  = 'https://bqzinttbjeeaybywhhet.supabase.co/rest/v1';
var LC_SK  = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxemludHRiamVlYXlieXdoaGV0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MDU3NDUsImV4cCI6MjA4OTI4MTc0NX0.B2MqUy_WEWOo8NVpGxHibuh-8xLklsy3Ux4DnXp9zmQ';
var LC_SHD = {'apikey':LC_SK,'Authorization':'Bearer '+LC_SK,'Content-Type':'application/json'};

var ACTION_LABELS = {
  booked: '✅ Booked',
  left_voicemail: '📞 Left Voicemail',
  will_book: '📅 Will Book When Ready',
  not_interested: '✗ Not Interested'
};
var ACTION_COLORS = {
  booked: '#16a34a',
  left_voicemail: '#d97706',
  will_book: '#2563eb',
  not_interested: '#dc2626'
};

function lcEsc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function lcGet(p){ return fetch(LC_SB+p, {headers:LC_SHD}).then(function(r){ return r.json(); }); }
function lcPost(t, body){
  return fetch(LC_SB+'/'+t, {
    method:'POST',
    headers:Object.assign({}, LC_SHD, {'Prefer':'return=representation'}),
    body: JSON.stringify(body)
  }).then(function(r){
    if(!r.ok) return r.json().catch(function(){ return null; }).then(function(err){
      throw new Error((err && err.message) || ('HTTP ' + r.status));
    });
    return r.json();
  });
}

// Every logged call is its own dated row (INSERT, never overwritten), so
// full call history is kept — not just a current status.
var callLogByCid = {};   // cid -> [entries], newest first
var dncByCid = {};       // cid -> {reason, created_at} — permanently excluded, not a call outcome
var lcCurrentCid = null;
var lcSelectedAction = null;
var currentBucket = 'initial';

function findLapseRow(cid){
  var rows = document.querySelectorAll('.lc-row');
  for(var i=0;i<rows.length;i++){
    if(rows[i].getAttribute('data-cid') === cid) return rows[i];
  }
  return null;
}

function loadCallLog(){
  lcGet('/lapse_call_log?store=eq.'+encodeURIComponent(LC_STORE)+'&order=call_date.desc,created_at.desc').then(function(rows){
    callLogByCid = {};
    (Array.isArray(rows)?rows:[]).forEach(function(r){
      if(!callLogByCid[r.pet_cid]) callLogByCid[r.pet_cid] = [];
      callLogByCid[r.pet_cid].push(r);
    });
    applyBuckets();
  }).catch(function(){});
}

function loadDoNotContact(){
  lcGet('/lapse_do_not_contact?store=eq.'+encodeURIComponent(LC_STORE)).then(function(rows){
    dncByCid = {};
    (Array.isArray(rows)?rows:[]).forEach(function(r){ dncByCid[r.pet_cid] = r; });
    applyBuckets();
  }).catch(function(){});
}

function currentAction(cid){
  var log = callLogByCid[cid];
  return log && log.length ? log[0].action : null;
}

function bucketFor(cid){
  if(dncByCid[cid]) return 'dnc';
  var action = currentAction(cid);
  if(!action) return 'initial';
  if(action === 'left_voicemail') return 'followup';
  return 'resolved';
}

function actionBadgeHtml(cid){
  if(dncByCid[cid]) return '<span style="color:#1f2937">🚫 Do Not Contact</span>';
  var action = currentAction(cid);
  if(!action) return 'Not yet contacted';
  var log = callLogByCid[cid];
  var lastDate = log[0].call_date;
  var label = ACTION_LABELS[action] || action;
  var color = ACTION_COLORS[action] || '#6b7280';
  return '<span style="color:' + color + '">' + lcEsc(label) + '</span><br><span style="font-weight:400;color:var(--muted)">' + lcEsc(lastDate) + '</span>';
}

function applyBuckets(){
  document.querySelectorAll('.lc-row').forEach(function(tr){
    var cid = tr.getAttribute('data-cid');
    var bucket = bucketFor(cid);
    tr.setAttribute('data-bucket', bucket);
    tr.classList.toggle('lc-dnc', bucket === 'dnc');
    var badge = tr.querySelector('.lc-action-badge');
    if(badge) badge.innerHTML = actionBadgeHtml(cid);
  });
  updateBucketCounts();
  filterLapseRows();
}

function updateBucketCounts(){
  var counts = {initial:0, followup:0, resolved:0, dnc:0};
  document.querySelectorAll('.lc-row').forEach(function(tr){
    var b = tr.getAttribute('data-bucket');
    if(counts[b] !== undefined) counts[b]++;
  });
  var totalEl = document.getElementById('bucket-count-all');
  if(totalEl) totalEl.textContent = counts.initial + counts.followup + counts.resolved;
  var iEl = document.getElementById('bucket-count-initial');
  if(iEl) iEl.textContent = counts.initial;
  var fEl = document.getElementById('bucket-count-followup');
  if(fEl) fEl.textContent = counts.followup;
  var rEl = document.getElementById('bucket-count-resolved');
  if(rEl) rEl.textContent = counts.resolved;
  var dEl = document.getElementById('bucket-count-dnc');
  if(dEl) dEl.textContent = counts.dnc;
}

function setBucket(b){
  currentBucket = b;
  document.querySelectorAll('.bucket-btn').forEach(function(btn){
    btn.classList.toggle('active', btn.getAttribute('data-bucket-btn') === b);
  });
  filterLapseRows();
}

// Complaint records only carry a free-text customer name (no reliable
// pet_cid link), so match against a dog's owner_name by last name rather
// than an exact key — matching on ANY shared name word (including first
// names) is too loose: "Nicole Goldberg" and an unrelated "Nicole
// Ortolano" complaint would collide on "Nicole" alone.
var lcComplaints = [];

function lcNameTokens(s){
  return (String(s||'').toLowerCase().match(/[a-z]+/g) || []).filter(function(t){ return t.length >= 3; });
}
function lcLastToken(s){
  var toks = lcNameTokens(s);
  return toks.length ? toks[toks.length - 1] : '';
}

function loadComplaints(){
  lcGet('/customer_complaints?store=eq.'+encodeURIComponent(LC_STORE)+'&select=customer_name,date,category,description,resolution,status&order=date.desc').then(function(rows){
    lcComplaints = (Array.isArray(rows) ? rows : []).map(function(c){
      return {rec: c, lastToken: lcLastToken(c.customer_name)};
    }).filter(function(c){ return c.lastToken; });
    flagComplaintRows();
  }).catch(function(){});
}

function complaintsForOwner(ownerName){
  var ownerLast = lcLastToken(ownerName);
  if(!ownerLast) return [];
  return lcComplaints.filter(function(c){ return c.lastToken === ownerLast; }).map(function(c){ return c.rec; });
}

function flagComplaintRows(){
  document.querySelectorAll('.lc-row').forEach(function(tr){
    var owner = tr.getAttribute('data-owner-name');
    var hasComplaint = complaintsForOwner(owner).length > 0;
    var flag = tr.querySelector('.lc-complaint-flag');
    if(flag) flag.style.display = hasComplaint ? 'block' : 'none';
  });
}

function selectAction(action){
  lcSelectedAction = action;
  document.querySelectorAll('.lc-action-option').forEach(function(btn){
    btn.classList.toggle('selected', btn.getAttribute('data-action') === action);
  });
  document.getElementById('lc-save-btn').disabled = false;
}

function renderCallHistory(cid){
  var log = callLogByCid[cid] || [];
  var el = document.getElementById('lc-modal-call-history');
  el.innerHTML = log.length
    ? log.map(function(entry){
        var label = ACTION_LABELS[entry.action] || entry.action;
        var color = ACTION_COLORS[entry.action] || '#6b7280';
        return '<div class="lc-call-log-entry">'
          + '<div class="lc-call-log-meta"><span class="lc-call-log-date">' + lcEsc(entry.call_date) + '</span>'
          + '<span class="lc-call-log-action" style="color:' + color + '">' + lcEsc(label) + '</span></div>'
          + (entry.notes ? '<div class="lc-call-log-notes">' + lcEsc(entry.notes) + '</div>' : '')
          + '</div>';
      }).join('')
    : '<div class="lc-history-empty">No calls logged yet</div>';
}

function openLapseDetail(cid){
  lcCurrentCid = cid;
  lcSelectedAction = null;
  var tr = findLapseRow(cid);
  var name = tr ? tr.getAttribute('data-pet-name') : '';
  var ownerName = tr ? tr.getAttribute('data-owner-name') : '';
  var ownerPhone = tr ? tr.getAttribute('data-owner-phone') : '';

  var phoneHtml = ownerPhone
    ? '<a href="tel:' + lcEsc(ownerPhone) + '" class="lc-modal-phone">' + lcEsc(ownerPhone) + '</a>'
    : '';
  document.getElementById('lc-modal-header').innerHTML =
    '<h2>' + lcEsc(name) + '</h2>'
    + '<div class="lc-modal-owner">' + lcEsc(ownerName || 'Unknown owner') + (phoneHtml ? ' · ' + phoneHtml : '') + '</div>'
    + '<div class="lc-modal-sub">' + actionBadgeHtml(cid).replace(/<br>/, ' — ').replace(/<[^>]+>/g, '') + '</div>';

  var complaints = complaintsForOwner(ownerName);
  var complaintsSection = document.getElementById('lc-modal-complaints-section');
  if(complaints.length){
    document.getElementById('lc-modal-complaints').innerHTML = complaints.map(function(c){
      return '<div class="lc-complaint-card">'
        + '<div class="lc-complaint-meta"><span>' + lcEsc(c.date || '') + ' · ' + lcEsc(c.category || '') + ' · on file for ' + lcEsc(c.customer_name || 'unknown') + '</span><span>' + lcEsc(c.status || '') + '</span></div>'
        + '<div class="lc-complaint-desc">' + lcEsc(c.description || '') + '</div>'
        + (c.resolution ? '<div class="lc-complaint-res">Resolution: ' + lcEsc(c.resolution) + '</div>' : '')
        + '</div>';
    }).join('');
    complaintsSection.style.display = 'block';
  } else {
    complaintsSection.style.display = 'none';
  }

  renderCallHistory(cid);
  renderDncSection(cid);

  document.querySelectorAll('.lc-action-option').forEach(function(btn){ btn.classList.remove('selected'); });
  document.getElementById('lc-m-notes').value = '';
  document.getElementById('lc-m-date').value = new Date().toISOString().slice(0,10);
  document.getElementById('lc-save-btn').disabled = true;
  document.getElementById('lc-m-indicator').textContent = '';

  var hist = PET_HISTORY[cid] || {appointments: [], notes: []};
  var appts = hist.appointments || [];
  var notes = hist.notes || [];

  var body = document.getElementById('lc-modal-history');
  body.innerHTML = appts.length
    ? appts.map(function(v){
        return '<tr><td>' + lcEsc(v.date) + '</td><td>' + lcEsc(v.service) + '</td><td>' + lcEsc(v.size) + '</td><td>' + lcEsc(v.groomer) + '</td></tr>';
      }).join('')
    : '<tr><td colspan=4 class="lc-history-empty">No visit history on file</td></tr>';

  var notesEl = document.getElementById('lc-modal-notes');
  notesEl.innerHTML = notes.length
    ? notes.map(function(n){
        return '<div class="lc-history-note"><span class="lc-history-note-date">' + lcEsc(n.date) + '</span> ' + lcEsc(n.text) + '</div>';
      }).join('')
    : '<div class="lc-history-empty">No notes on file</div>';

  document.getElementById('lc-modal-overlay').classList.add('active');
}

function closeLapseDetail(){
  document.getElementById('lc-modal-overlay').classList.remove('active');
  lcCurrentCid = null;
}

// Do Not Contact is a standing exclusion, not a call outcome — separate
// from the 4 action buttons above. Once set, a dog stays off every other
// view (including "All Candidates") until someone removes it here.
function renderDncSection(cid){
  var el = document.getElementById('lc-dnc-section');
  var flagged = dncByCid[cid];
  if(flagged){
    el.innerHTML =
      '<div class="lc-dnc-banner"><div><strong>🚫 Marked Do Not Contact</strong>'
      + lcEsc(flagged.reason || 'No reason given') + ' — ' + lcEsc((flagged.created_at||'').slice(0,10))
      + '</div><button class="lc-dnc-toggle-btn lc-dnc-remove" onclick="removeDoNotContact()">Remove flag</button></div>';
  } else {
    el.innerHTML =
      '<div class="lc-modal-field"><label>Reason (owner passed away, moved, banned, etc.)</label>'
      + '<textarea id="lc-dnc-reason" placeholder="Why should this dog never be called?" rows="2"></textarea></div>'
      + '<button class="lc-dnc-toggle-btn" onclick="saveDoNotContact()">🚫 Mark Do Not Contact</button>';
  }
}

function lcCheckOk(r){
  if(!r.ok) return r.json().catch(function(){ return null; }).then(function(err){
    throw new Error((err && err.message) || ('HTTP ' + r.status));
  });
  return r.status === 204 ? null : r.json();
}

function saveDoNotContact(){
  if(!lcCurrentCid) return;
  var cid = lcCurrentCid;
  var tr = findLapseRow(cid);
  var btn = document.querySelector('.lc-dnc-toggle-btn');
  var body = {
    store: LC_STORE,
    pet_cid: cid,
    pet_name: tr ? tr.getAttribute('data-pet-name') : '',
    owner_name: tr ? tr.getAttribute('data-owner-name') : '',
    reason: (document.getElementById('lc-dnc-reason') || {}).value || ''
  };
  if(btn){ btn.disabled = true; btn.textContent = 'Saving…'; }
  fetch(LC_SB + '/lapse_do_not_contact?on_conflict=store,pet_cid', {
    method: 'POST',
    headers: Object.assign({}, LC_SHD, {'Prefer': 'resolution=merge-duplicates,return=representation'}),
    body: JSON.stringify(body)
  }).then(lcCheckOk).then(function(res){
    var saved = Array.isArray(res) ? res[0] : res;
    if(!saved) throw new Error('save did not return a record');
    dncByCid[cid] = saved;
    renderDncSection(cid);
    applyBuckets();
    closeLapseDetail();
  }).catch(function(err){
    var el = document.getElementById('lc-dnc-section');
    if(el) el.insertAdjacentHTML('afterbegin', '<div style="color:#dc2626;font-size:12px;margin-bottom:8px">Error: ' + lcEsc(err.message) + ' — flag NOT saved, try again</div>');
    if(btn){ btn.disabled = false; btn.textContent = '🚫 Mark Do Not Contact'; }
  });
}

function removeDoNotContact(){
  if(!lcCurrentCid) return;
  var cid = lcCurrentCid;
  var btn = document.querySelector('.lc-dnc-remove');
  if(btn){ btn.disabled = true; btn.textContent = 'Removing…'; }
  fetch(LC_SB + '/lapse_do_not_contact?store=eq.' + encodeURIComponent(LC_STORE) + '&pet_cid=eq.' + encodeURIComponent(cid), {
    method: 'DELETE',
    headers: LC_SHD
  }).then(lcCheckOk).then(function(){
    delete dncByCid[cid];
    renderDncSection(cid);
    applyBuckets();
  }).catch(function(err){
    var el = document.getElementById('lc-dnc-section');
    if(el) el.insertAdjacentHTML('afterbegin', '<div style="color:#dc2626;font-size:12px;margin-bottom:8px">Error: ' + lcEsc(err.message) + ' — flag NOT removed, try again</div>');
    if(btn){ btn.disabled = false; btn.textContent = 'Remove flag'; }
  });
}

function saveLapseDetail(){
  if(!lcCurrentCid || !lcSelectedAction) return;
  var cid = lcCurrentCid;
  var tr = findLapseRow(cid);
  var body = {
    store: LC_STORE,
    pet_cid: cid,
    pet_name: tr ? tr.getAttribute('data-pet-name') : '',
    action: lcSelectedAction,
    notes: document.getElementById('lc-m-notes').value,
    call_date: document.getElementById('lc-m-date').value || new Date().toISOString().slice(0,10)
  };
  var ind = document.getElementById('lc-m-indicator');
  ind.textContent = 'Saving…';
  lcPost('lapse_call_log', body).then(function(res){
    var saved = Array.isArray(res) ? res[0] : res;
    if(saved){
      if(!callLogByCid[cid]) callLogByCid[cid] = [];
      callLogByCid[cid].unshift(saved);
    }
    ind.textContent = '✓ saved';
    renderCallHistory(cid);
    document.getElementById('lc-m-notes').value = '';
    lcSelectedAction = null;
    document.querySelectorAll('.lc-action-option').forEach(function(btn){ btn.classList.remove('selected'); });
    document.getElementById('lc-save-btn').disabled = true;
    applyBuckets();
  }).catch(function(err){
    ind.textContent = 'Error: ' + (err && err.message ? err.message : 'save failed');
  });
}

function filterLapseRows(){
  var from = document.getElementById('lc-from').value;
  var to = document.getElementById('lc-to').value;
  var q = (document.getElementById('lc-search').value || '').trim().toLowerCase();
  var rows = document.querySelectorAll('.lc-row');
  var shown = 0;
  rows.forEach(function(tr){
    var lv = tr.getAttribute('data-last-visit');
    var bucket = tr.getAttribute('data-bucket');
    var visible;
    if(q){
      // A search looks across every candidate regardless of bucket or date
      // range — finding a specific dog shouldn't depend on which filter
      // happens to be active. Do Not Contact stays excluded either way.
      var petName = (tr.getAttribute('data-pet-name') || '').toLowerCase();
      var ownerName = (tr.getAttribute('data-owner-name') || '').toLowerCase();
      var ownerPhone = (tr.getAttribute('data-owner-phone') || '').toLowerCase();
      visible = bucket !== 'dnc' && (petName.indexOf(q) !== -1 || ownerName.indexOf(q) !== -1 || ownerPhone.indexOf(q) !== -1);
    } else {
      // "All Candidates" means everyone still callable — Do Not Contact stays
      // hidden there too, only visible under its own dedicated bucket.
      visible = currentBucket === 'all' ? bucket !== 'dnc' : bucket === currentBucket;
      if(from && lv && lv < from) visible = false;
      if(to && lv && lv > to) visible = false;
    }
    tr.classList.toggle('lc-hidden', !visible);
    if(visible) shown++;
  });
  var rc = document.getElementById('lc-range-count');
  if(rc) rc.textContent = shown + ' shown';
}

function setLapseRange(fromDaysAgo, toDaysAgo){
  var from = new Date();
  from.setDate(from.getDate() - fromDaysAgo);
  var to = new Date();
  to.setDate(to.getDate() - toDaysAgo);
  document.getElementById('lc-from').value = from.toISOString().slice(0,10);
  document.getElementById('lc-to').value = to.toISOString().slice(0,10);
  filterLapseRows();
}

function clearLapseRange(){
  document.getElementById('lc-from').value = '';
  document.getElementById('lc-to').value = '';
  filterLapseRows();
}

setLapseRange(84, 42);  // default view: last visit 6-12 weeks ago
loadCallLog();
loadDoNotContact();
loadComplaints();
""".replace("__STORE__", store_name).replace("__PET_HISTORY__", PET_HISTORY_JSON)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lapse Calls — {store_label}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>📞 Lapse Calls — {store_label}</h1>
  <nav>
    <a id="portal-back" href="../index.html">&larr; Home</a>{PORTAL_BACK_JS}
    <a href="WoofGang_{store_fn}_PetDashboard.html">Pet Dashboard</a>
    <a href="WoofGang_{store_fn}_LapseProgress.html">📊 Progress</a>
  </nav>
</header>
<main>
  <div class="bucket-row">
    <button class="bucket-btn active" data-bucket-btn="initial" onclick="setBucket('initial')">
      <div class="val" id="bucket-count-initial">{len(lapsed_dogs)}</div>
      <div class="lbl">Needs Initial Contact</div>
    </button>
    <button class="bucket-btn" data-bucket-btn="followup" onclick="setBucket('followup')">
      <div class="val" id="bucket-count-followup">0</div>
      <div class="lbl">Needs Follow-up (left voicemail)</div>
    </button>
    <button class="bucket-btn" data-bucket-btn="resolved" onclick="setBucket('resolved')">
      <div class="val" id="bucket-count-resolved">0</div>
      <div class="lbl">Resolved (booked / will book / not interested)</div>
    </button>
    <button class="bucket-btn" data-bucket-btn="all" onclick="setBucket('all')">
      <div class="val" id="bucket-count-all">{len(lapsed_dogs)}</div>
      <div class="lbl">All Candidates</div>
    </button>
  </div>

  <div class="card">
    <h2>Outreach List</h2>
    <p style="color:#6b7280;font-size:13px;margin-bottom:16px">
      Every customer whose last completed visit falls in the date range below, with nothing since and no future appointment already on the books.
      Log an outcome for each call — <strong>Left Voicemail</strong> moves a dog to Needs Follow-up; Booked, Will Book When Ready, and Not Interested are resolved.
      Every call you log is kept as its own dated entry, so full call history is visible per dog.
      Lapsed/At Risk badges show how overdue a dog is against their own typical visit frequency where we have enough history (3+ real visits) — extra context only.
      A groomer marked with * means no stylist was recorded — showing front desk/checkout staff instead.
    </p>
    <div class="lc-filter-bar">
      <input type="text" id="lc-search" placeholder="🔍 Search dog or owner name / phone…" oninput="filterLapseRows()">
      <label>Last visit from <input type="date" id="lc-from" onchange="filterLapseRows()"></label>
      <label>to <input type="date" id="lc-to" onchange="filterLapseRows()"></label>
      <button class="lc-preset-btn" onclick="setLapseRange(84, 42)">6–12 Weeks Ago</button>
      <button class="lc-preset-btn" onclick="clearLapseRange()">Clear Dates</button>
      <span class="lc-range-count" id="lc-range-count"></span>
    </div>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Dog</th><th>Owner / Phone</th><th>Status</th><th>Last Visit</th><th>Frequency</th><th>Last Groomer</th><th>Visits</th><th>Call Outcome</th></tr></thead>
      <tbody id="lc-tbody">{''.join(rows) if rows else '<tr><td colspan=8 style="color:#999;text-align:center;padding:24px">No candidates</td></tr>'}</tbody>
    </table>
    </div>
    <p style="color:#9ca3af;font-size:12px;margin-top:10px">Click a dog's name to see its full appointment history and log a call.</p>
  </div>
</main>

<div class="lc-modal-overlay" id="lc-modal-overlay" onclick="if(event.target===this) closeLapseDetail()">
  <div class="lc-modal">
    <button class="lc-modal-close" onclick="closeLapseDetail()">&times;</button>
    <div id="lc-modal-header"></div>
    <div class="lc-modal-section lc-complaints-section" id="lc-modal-complaints-section" style="display:none">
      <h3>⚠ Prior Complaints</h3>
      <div id="lc-modal-complaints"></div>
    </div>
    <div class="lc-dnc-section" id="lc-dnc-section"></div>
    <div class="lc-modal-section">
      <h3>Call History</h3>
      <div id="lc-modal-call-history"></div>
    </div>
    <div class="lc-modal-section">
      <h3>Log a Call</h3>
      <div class="lc-action-grid">
        <button class="lc-action-option" data-action="booked" onclick="selectAction('booked')">✅ Booked</button>
        <button class="lc-action-option" data-action="left_voicemail" onclick="selectAction('left_voicemail')">📞 Left Voicemail</button>
        <button class="lc-action-option" data-action="will_book" onclick="selectAction('will_book')">📅 Customer Will Book When Ready</button>
        <button class="lc-action-option" data-action="not_interested" onclick="selectAction('not_interested')">✗ Not Interested</button>
      </div>
      <div class="lc-modal-field">
        <label>Call Date</label>
        <input type="date" id="lc-m-date">
      </div>
      <div class="lc-modal-field">
        <label>Notes</label>
        <textarea id="lc-m-notes" placeholder="Notes…" rows="3"></textarea>
      </div>
      <div class="lc-modal-actions">
        <button class="lc-save-btn" id="lc-save-btn" onclick="saveLapseDetail()" disabled>Save Call</button>
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
    <div class="lc-modal-section">
      <h3>Visit Notes</h3>
      <div id="lc-modal-notes"></div>
    </div>
  </div>
</div>

<script>{JS}</script>
</body>
</html>"""

with open(out_html, "w") as f:
    f.write(html)

print(f"Lapse Calls widget written → {out_html}")

# ── Day-by-Day Progress — standalone page ──────────────────────────────────
# Kept separate from the main Lapse Calls page so it doesn't distract
# associates working the outreach list; owners check it on its own.

out_progress_html = data_dir.parent / f"WoofGang_{store_fn}_LapseProgress.html"
CID_OWNER_JSON = json.dumps(cid_owner_map).replace("</", "<\\/")

PROGRESS_CSS = """
:root { --brown:#2C1A0E; --pink:#E8006A; --bg:#fdf8f5; --card:#fff; --border:#e5e7eb; --text:#1f2937; --muted:#6b7280; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); font-size:14px; }
header { background:var(--brown); color:#fff; padding:16px 24px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; position:sticky; top:0; z-index:100; }
header h1 { font-size:18px; font-weight:700; }
header nav { display:flex; gap:8px; margin-left:auto; }
header nav a { color:rgba(255,255,255,0.75); text-decoration:none; font-size:13px; padding:6px 12px; border-radius:6px; }
header nav a:hover { background:rgba(255,255,255,0.12); color:#fff; }
main { max-width:1100px; margin:0 auto; padding:24px 16px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:20px; margin-bottom:20px; }
.card h2 { font-size:16px; font-weight:700; margin-bottom:14px; color:var(--brown); }
table { width:100%; border-collapse:collapse; }
th { background:var(--brown); color:#fff; padding:10px 12px; text-align:left; font-size:12px; font-weight:600; }
td { padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
th.n, td.n { text-align:right; }
.lc-progress-row { cursor:pointer; }
.lc-progress-row:hover td { background:#fef9f5; }
.lc-progress-date { font-weight:700; color:var(--brown); }
.lc-progress-caret { display:inline-block; margin-right:6px; transition:transform .15s; color:var(--muted); }
.lc-progress-row.open .lc-progress-caret { transform:rotate(90deg); }
.lc-progress-detail-row.lc-pd-hidden { display:none; }
.lc-progress-detail-row td { background:#f9fafb; padding:12px 16px; }
.lc-progress-entry { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; padding:6px 0; border-bottom:1px solid var(--border); font-size:12px; }
.lc-progress-entry:last-child { border-bottom:none; }
.lc-progress-entry-dog { font-weight:700; min-width:160px; }
.lc-progress-entry-owner { font-weight:400; color:var(--muted); }
.lc-progress-entry-action { font-weight:600; min-width:170px; }
.lc-progress-entry-notes { color:var(--muted); flex:1; }
.lc-progress-empty { color:#9ca3af; text-align:center; padding:24px; }
@media(max-width:600px) { th,td { padding:8px 6px; font-size:12px; } }
"""

PROGRESS_JS = """
var LC_STORE = "__STORE__";
var CID_OWNER = __CID_OWNER__;
var LC_SB  = 'https://bqzinttbjeeaybywhhet.supabase.co/rest/v1';
var LC_SK  = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxemludHRiamVlYXlieXdoaGV0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MDU3NDUsImV4cCI6MjA4OTI4MTc0NX0.B2MqUy_WEWOo8NVpGxHibuh-8xLklsy3Ux4DnXp9zmQ';
var LC_SHD = {'apikey':LC_SK,'Authorization':'Bearer '+LC_SK,'Content-Type':'application/json'};

var ACTION_LABELS = {
  booked: '✅ Booked',
  left_voicemail: '📞 Left Voicemail',
  will_book: '📅 Will Book When Ready',
  not_interested: '✗ Not Interested'
};
var ACTION_COLORS = {
  booked: '#16a34a',
  left_voicemail: '#d97706',
  will_book: '#2563eb',
  not_interested: '#dc2626'
};

function lcEsc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function loadDayProgress(){
  fetch(LC_SB + '/lapse_call_log?store=eq.' + encodeURIComponent(LC_STORE) + '&order=call_date.desc,created_at.desc', {headers:LC_SHD})
    .then(function(r){ return r.json(); })
    .then(renderDayProgress)
    .catch(function(){
      document.getElementById('lc-progress-body').innerHTML = '<tr><td colspan=6 class="lc-progress-empty">Failed to load call log</td></tr>';
    });
}

function renderDayProgress(rows){
  var body = document.getElementById('lc-progress-body');
  var byDate = {};
  (Array.isArray(rows) ? rows : []).forEach(function(entry){
    var d = entry.call_date;
    if(!d) return;
    if(!byDate[d]) byDate[d] = [];
    byDate[d].push(entry);
  });
  var dates = Object.keys(byDate).sort().reverse();
  if(!dates.length){
    body.innerHTML = '<tr><td colspan=6 class="lc-progress-empty">No calls logged yet</td></tr>';
    return;
  }
  body.innerHTML = dates.map(function(d){
    var entries = byDate[d];
    var counts = {booked:0, left_voicemail:0, will_book:0, not_interested:0};
    entries.forEach(function(e){ if(counts[e.action] !== undefined) counts[e.action]++; });
    var rowId = 'lc-day-' + d.replace(/[^0-9]/g, '');
    var detailHtml = entries.map(function(e){
      var label = ACTION_LABELS[e.action] || e.action;
      var color = ACTION_COLORS[e.action] || '#6b7280';
      var owner = CID_OWNER[e.pet_cid] || '';
      return '<div class="lc-progress-entry">'
        + '<span class="lc-progress-entry-dog">' + lcEsc(e.pet_name || '') + (owner ? ' <span class="lc-progress-entry-owner">(' + lcEsc(owner) + ')</span>' : '') + '</span>'
        + '<span class="lc-progress-entry-action" style="color:' + color + '">' + lcEsc(label) + '</span>'
        + (e.notes ? '<span class="lc-progress-entry-notes">' + lcEsc(e.notes) + '</span>' : '')
        + '</div>';
    }).join('');
    return '<tr class="lc-progress-row" id="' + rowId + '-toggle" onclick="toggleDayDetail(\\'' + rowId + '\\')">'
      + '<td class="lc-progress-date"><span class="lc-progress-caret">&#9656;</span>' + lcEsc(d) + '</td>'
      + '<td class="n">' + entries.length + '</td>'
      + '<td class="n">' + counts.booked + '</td>'
      + '<td class="n">' + counts.left_voicemail + '</td>'
      + '<td class="n">' + counts.will_book + '</td>'
      + '<td class="n">' + counts.not_interested + '</td>'
      + '</tr>'
      + '<tr class="lc-progress-detail-row lc-pd-hidden" id="' + rowId + '"><td colspan=6>' + detailHtml + '</td></tr>';
  }).join('');
}

function toggleDayDetail(rowId){
  var detail = document.getElementById(rowId);
  var toggleRow = document.getElementById(rowId + '-toggle');
  if(detail) detail.classList.toggle('lc-pd-hidden');
  if(toggleRow) toggleRow.classList.toggle('open');
}

loadDayProgress();
""".replace("__STORE__", store_name).replace("__CID_OWNER__", CID_OWNER_JSON)

progress_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lapse Calls Progress — {store_label}</title>
<style>{PROGRESS_CSS}</style>
</head>
<body>
<header>
  <h1>📊 Lapse Calls Progress — {store_label}</h1>
  <nav>
    <a id="portal-back" href="../index.html">&larr; Home</a>{PORTAL_BACK_JS}
    <a href="WoofGang_{store_fn}_LapseCalls.html">Lapse Calls</a>
  </nav>
</header>
<main>
  <div class="card">
    <h2>Day-by-Day Progress</h2>
    <p style="color:#6b7280;font-size:13px;margin-bottom:16px">
      Every call logged, grouped by the day it was logged. Click a day to see exactly which dogs were called and the outcome of each.
    </p>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>Date</th><th class="n">Calls Logged</th><th class="n">✅ Booked</th><th class="n">📞 Voicemail</th><th class="n">📅 Will Book</th><th class="n">✗ Not Interested</th></tr></thead>
      <tbody id="lc-progress-body"><tr><td colspan=6 class="lc-progress-empty">Loading…</td></tr></tbody>
    </table>
    </div>
  </div>
</main>
<script>{PROGRESS_JS}</script>
</body>
</html>"""

with open(out_progress_html, "w") as f:
    f.write(progress_html)

print(f"Lapse Calls Progress page written → {out_progress_html}")
