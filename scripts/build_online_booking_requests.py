"""Online Booking Requests — cross-store outreach page.

Store mailboxes (hicksvilleny@, glencoveny@, portwashingtonny@@woofgangbakery.com)
auto-forward "New Appointment Information Request" lead emails (an ad-click
landing-page signup, mostly from new stores before FranPOS online booking is
fully live) into a shared Gmail inbox. scripts/fetch_booking_requests.py polls
that inbox hourly and writes parsed leads straight to Supabase — this page has
no static per-store data of its own, it's a shell that fetches and renders the
current lead list live via JS on load, the same way Lapse Calls' call log and
Booking Anomalies' acks are fetched live rather than baked in at build time.

Usage:
    python3 scripts/build_online_booking_requests.py
"""

import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from config import PROJ_ROOT, PORTAL_BACK_JS, STORE_REGISTRY, get_store_display

NOW_STR = datetime.now(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p ET")
OUT_HTML = PROJ_ROOT / "port-washington" / "WoofGang_OnlineBookingRequests.html"

STORE_TABS_JS = ", ".join(
    f'{{key:"{k}",label:"{get_store_display(k)}"}}' for k in STORE_REGISTRY
)

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Online Booking Requests — Woof Gang</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f4f0;color:#1a1a2e}}

.header{{background:linear-gradient(135deg,#1B6B6B 0%,#6B3520 100%);color:white;padding:40px 0 30px;text-align:center;position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(196,39,110,0.15) 0%,transparent 50%)}}
.header h1{{font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;position:relative;margin-bottom:4px}}
.header .subtitle{{font-size:1rem;font-weight:400;opacity:0.9;position:relative}}
.header .brand-tag{{display:inline-block;background:#C4276E;color:white;padding:4px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;margin-top:12px;position:relative}}
.header-timestamp{{position:absolute;top:12px;right:20px;font-size:0.78rem;opacity:0.85;font-weight:400;z-index:1;text-align:right}}

.topbar{{background:#C4276E;padding:14px 32px;color:white;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(196,39,110,0.3);display:flex;justify-content:space-between;align-items:center}}
.home-link{{color:rgba(255,255,255,0.85);text-decoration:none;font-size:0.88rem;font-weight:600}}
.home-link:hover{{color:white}}
.topbar-center{{font-size:0.95rem;font-weight:700;color:white}}

.tabs{{background:white;border-bottom:2px solid #eee;position:sticky;top:50px;z-index:99;display:flex;padding:0 24px;flex-wrap:wrap;overflow-x:auto}}
.tab{{padding:14px 18px;border:none;background:transparent;color:#999;font-size:0.88rem;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;font-family:inherit;white-space:nowrap;transition:color 0.15s}}
.tab:hover{{color:#C4276E}}
.tab.active{{color:#C4276E;border-bottom-color:#C4276E}}

.page{{max-width:1100px;margin:0 auto;padding:24px 24px 60px}}

.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{background:white;border-radius:12px;padding:16px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,0.05);border-top:3px solid #C4276E}}
.kpi-val{{font-size:2rem;font-weight:700;line-height:1;color:#C4276E}}
.kpi-label{{font-size:0.73rem;color:#999;margin-top:6px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em}}

.card{{background:white;border-radius:14px;padding:22px;margin-bottom:22px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.stitle{{font-size:1.05rem;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.stitle::before{{content:'';display:inline-block;width:4px;height:18px;background:#C4276E;border-radius:2px}}

.tbl-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:0.86rem}}
th{{text-align:left;padding:9px 10px;color:#888;font-weight:600;font-size:0.73rem;text-transform:uppercase;letter-spacing:0.04em;border-bottom:2px solid #eee;white-space:nowrap}}
td{{padding:11px 10px;border-bottom:1px solid #f5f0ec;vertical-align:middle}}
tr:hover td{{background:#fef9fb}}
.empty-row td{{text-align:center;color:#aaa;padding:32px;font-style:italic}}

.store-badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:0.75rem;font-weight:600;white-space:nowrap;background:#f3e5f5;color:#6a1b9a}}
.phone-link{{color:#C4276E;text-decoration:none;font-weight:600}}
.phone-link:hover{{text-decoration:underline}}
.stale-flag{{color:#c62828;font-weight:700;font-size:0.75rem}}
.existing-flag{{color:#1565c0;font-size:0.73rem;font-weight:600}}

.action-btns{{display:flex;gap:6px;flex-wrap:wrap}}
.action-btn{{border:none;padding:6px 11px;border-radius:6px;font-size:0.75rem;font-weight:600;cursor:pointer;font-family:inherit;color:white;white-space:nowrap}}
.action-btn.voicemail{{background:#d97706}}
.action-btn.voicemail:hover{{background:#b8630a}}
.action-btn.booked{{background:#16a34a}}
.action-btn.booked:hover{{background:#128038}}
.action-btn.not-interested{{background:#dc2626}}
.action-btn.not-interested:hover{{background:#b91c1c}}
.unack-btn{{border:none;padding:6px 11px;border-radius:6px;font-size:0.75rem;font-weight:600;cursor:pointer;font-family:inherit;background:#eee;color:#555}}
.unack-btn:hover{{background:#ddd}}
.outcome-badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:0.75rem;font-weight:600;white-space:nowrap;color:white;margin-right:6px}}
.outcome-badge.voicemail{{background:#d97706}}
.outcome-badge.booked{{background:#16a34a}}
.outcome-badge.not-interested{{background:#dc2626}}

.reviewed-toggle{{cursor:pointer;user-select:none}}
.reviewed-inner{{display:none;margin-top:14px}}
.reviewed-inner.open{{display:block}}

.err-banner{{background:#fce4ec;color:#c62828;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:0.85rem;display:none}}
</style>
</head>
<body>

<div class="topbar">
  <a class="home-link" id="portal-back" href="../index.html">&larr; Owner Portal</a>
  <div class="topbar-center">Online Booking Requests</div>
  <div></div>
</div>

<div class="header">
  <div class="header-timestamp">Page built {NOW_STR}<br>List refreshes live on open</div>
  <h1>&#x1F4DE; Online Booking Requests</h1>
  <div class="subtitle">New leads from online ads &mdash; call before they go cold</div>
  <div class="brand-tag">WOOF GANG BAKERY &amp; GROOMING</div>
</div>

<div class="tabs" id="store-tabs"></div>

<div class="page">
  <div id="err-banner" class="err-banner"></div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val" id="kpi-total">0</div><div class="kpi-label">Needs Reach Out</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-followup">0</div><div class="kpi-label">Needs Follow-up</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-today">0</div><div class="kpi-label">Requested Today</div></div>
    <div class="kpi"><div class="kpi-val" id="kpi-stale">0</div><div class="kpi-label">Waiting 24h+</div></div>
  </div>

  <div class="card">
    <div class="stitle">Needs Reach Out (<span id="active-count">0</span>)</div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Requested</th><th>Customer</th><th>Phone</th><th>Email</th><th>Store</th><th></th></tr></thead>
        <tbody id="active-tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="stitle">&#128222; Needs Follow-up (<span id="followup-count">0</span>)</div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Requested</th><th>Customer</th><th>Phone</th><th>Email</th><th>Store</th><th></th></tr></thead>
        <tbody id="followup-tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="stitle reviewed-toggle" onclick="toggleReviewed()">
      <span id="reviewed-arrow">&#9656;</span> &#x2705; Resolved (<span id="reviewed-count">0</span>)
    </div>
    <div class="reviewed-inner tbl-wrap" id="reviewed-inner">
      <table>
        <thead><tr><th>Requested</th><th>Customer</th><th>Phone</th><th>Email</th><th>Store</th><th>Outcome</th><th></th></tr></thead>
        <tbody id="reviewed-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

{PORTAL_BACK_JS}
<script>
var OBR_SB  = 'https://bqzinttbjeeaybywhhet.supabase.co/rest/v1';
var OBR_SK  = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxemludHRiamVlYXlieXdoaGV0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MDU3NDUsImV4cCI6MjA4OTI4MTc0NX0.B2MqUy_WEWOo8NVpGxHibuh-8xLklsy3Ux4DnXp9zmQ';
var OBR_SHD = {{'apikey':OBR_SK,'Authorization':'Bearer '+OBR_SK,'Content-Type':'application/json'}};

var STORE_TABS = [{{key:'all',label:'All Stores'}}].concat([{STORE_TABS_JS}]);
var _storeFilter = 'all';
var ROWS = [];

function esc(s){{
  return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function storeLabel(key){{
  var t = STORE_TABS.find(function(s){{ return s.key === key; }});
  return t ? t.label : key;
}}

function existingFlag(r){{
  if(!r.existing_at) return '';
  var stores = r.existing_at.split(',').filter(Boolean).map(storeLabel);
  if(!stores.length) return '';
  return '<br><span class="existing-flag">&#128205; Already a customer at ' + esc(stores.join(', ')) + '</span>';
}}

function fmtRequested(iso){{
  if(!iso) return '&mdash;';
  var d = new Date(iso);
  if(isNaN(d)) return esc(iso);
  return d.toLocaleString('en-US', {{month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}});
}}

function hoursSince(iso){{
  if(!iso) return null;
  var d = new Date(iso);
  if(isNaN(d)) return null;
  return (Date.now() - d.getTime()) / 3600000;
}}

function renderTabs(){{
  var el = document.getElementById('store-tabs');
  el.innerHTML = STORE_TABS.map(function(t){{
    return '<button class="tab' + (t.key===_storeFilter?' active':'') + '" onclick="setStore(\\''+t.key+'\\')">' + esc(t.label) + '</button>';
  }}).join('');
}}
function setStore(key){{ _storeFilter = key; renderTabs(); render(); }}

var OUTCOME_LABELS = {{
  left_voicemail: '&#128222; Left Voicemail',
  booked: '&#9989; Booked',
  not_interested: '&#10007; Not Interested'
}};
var OUTCOME_CLASSES = {{
  left_voicemail: 'voicemail',
  booked: 'booked',
  not_interested: 'not-interested'
}};

function actionBtnsHtml(id){{
  return '<div class="action-btns">' +
    '<button class="action-btn voicemail" onclick="setOutcome(\\''+id+'\\',\\'left_voicemail\\')">&#128222; Left Voicemail</button>' +
    '<button class="action-btn booked" onclick="setOutcome(\\''+id+'\\',\\'booked\\')">&#9989; Booked</button>' +
    '<button class="action-btn not-interested" onclick="setOutcome(\\''+id+'\\',\\'not_interested\\')">&#10007; Not Interested</button>' +
    '</div>';
}}

function makeRow(r, section){{
  var hrs = hoursSince(r.requested_at);
  var staleFlag = (section === 'active' && hrs !== null && hrs >= 24) ? ' <span class="stale-flag">&#9888; ' + Math.floor(hrs/24) + 'd</span>' : '';
  var lastCol;
  if(section === 'resolved'){{
    var cls = OUTCOME_CLASSES[r.outcome] || '';
    lastCol = '<td><span class="outcome-badge ' + cls + '">' + (OUTCOME_LABELS[r.outcome] || esc(r.outcome)) + '</span></td>' +
      '<td><button class="unack-btn" onclick="clearOutcome(\\''+r.id+'\\')">&#8617; Undo</button></td>';
  }} else {{
    lastCol = '<td>' + actionBtnsHtml(r.id) + '</td>';
  }}
  return '<tr>' +
    '<td>' + fmtRequested(r.requested_at) + staleFlag + '</td>' +
    '<td>' + esc(r.customer_name || '&mdash;') + existingFlag(r) + '</td>' +
    '<td>' + (r.customer_phone ? '<a class="phone-link" href="tel:'+esc(r.customer_phone)+'">'+esc(r.customer_phone)+'</a>' : '&mdash;') + '</td>' +
    '<td>' + esc(r.customer_email || '&mdash;') + '</td>' +
    '<td><span class="store-badge">' + esc(storeLabel(r.store)) + '</span></td>' +
    lastCol +
    '</tr>';
}}

function render(){{
  var filtered = _storeFilter === 'all' ? ROWS : ROWS.filter(function(r){{ return r.store === _storeFilter; }});
  var active = filtered.filter(function(r){{ return !r.outcome; }})
    .sort(function(a,b){{ return (a.requested_at||'').localeCompare(b.requested_at||''); }});
  var followup = filtered.filter(function(r){{ return r.outcome === 'left_voicemail'; }})
    .sort(function(a,b){{ return (a.requested_at||'').localeCompare(b.requested_at||''); }});
  var resolved = filtered.filter(function(r){{ return r.outcome === 'booked' || r.outcome === 'not_interested'; }})
    .sort(function(a,b){{ return (b.requested_at||'').localeCompare(a.requested_at||''); }});

  var activeTbody = document.getElementById('active-tbody');
  activeTbody.innerHTML = active.length
    ? active.map(function(r){{ return makeRow(r, 'active'); }}).join('')
    : '<tr class="empty-row"><td colspan="6">Nothing waiting &mdash; you\\'re all caught up.</td></tr>';
  document.getElementById('active-count').textContent = active.length;

  var followupTbody = document.getElementById('followup-tbody');
  followupTbody.innerHTML = followup.length
    ? followup.map(function(r){{ return makeRow(r, 'followup'); }}).join('')
    : '<tr class="empty-row"><td colspan="6">No one waiting on a callback.</td></tr>';
  document.getElementById('followup-count').textContent = followup.length;

  var reviewedTbody = document.getElementById('reviewed-tbody');
  reviewedTbody.innerHTML = resolved.map(function(r){{ return makeRow(r, 'resolved'); }}).join('');
  document.getElementById('reviewed-count').textContent = resolved.length;

  document.getElementById('kpi-total').textContent = active.length;
  document.getElementById('kpi-followup').textContent = followup.length;
  var todayStr = new Date().toISOString().slice(0,10);
  document.getElementById('kpi-today').textContent = filtered.filter(function(r){{ return (r.requested_at||'').slice(0,10) === todayStr; }}).length;
  document.getElementById('kpi-stale').textContent = active.filter(function(r){{ var h = hoursSince(r.requested_at); return h !== null && h >= 24; }}).length;
}}

function toggleReviewed(){{
  var inner = document.getElementById('reviewed-inner');
  var arrow = document.getElementById('reviewed-arrow');
  inner.classList.toggle('open');
  arrow.innerHTML = inner.classList.contains('open') ? '&#9662;' : '&#9656;';
}}

function showError(msg){{
  var el = document.getElementById('err-banner');
  el.textContent = msg;
  el.style.display = 'block';
}}

function setOutcome(id, outcome){{
  fetch(OBR_SB + '/online_booking_requests?id=eq.' + encodeURIComponent(id), {{
    method: 'PATCH',
    headers: Object.assign({{}}, OBR_SHD, {{'Prefer': 'return=representation'}}),
    body: JSON.stringify({{outcome: outcome, outcome_at: new Date().toISOString()}})
  }}).then(function(r){{
    if(!r.ok) throw new Error('save failed');
    return r.json();
  }}).then(function(res){{
    var saved = Array.isArray(res) ? res[0] : res;
    if(!saved) throw new Error('no row returned');
    var row = ROWS.find(function(r){{ return r.id === id; }});
    if(row){{ row.outcome = saved.outcome; row.outcome_at = saved.outcome_at; }}
    render();
  }}).catch(function(){{ alert('Could not save — try again.'); }});
}}

function clearOutcome(id){{
  fetch(OBR_SB + '/online_booking_requests?id=eq.' + encodeURIComponent(id), {{
    method: 'PATCH',
    headers: Object.assign({{}}, OBR_SHD, {{'Prefer': 'return=representation'}}),
    body: JSON.stringify({{outcome: null, outcome_at: null}})
  }}).then(function(r){{
    if(!r.ok) throw new Error('save failed');
    return r.json();
  }}).then(function(res){{
    var saved = Array.isArray(res) ? res[0] : res;
    if(!saved) throw new Error('no row returned');
    var row = ROWS.find(function(r){{ return r.id === id; }});
    if(row){{ row.outcome = null; row.outcome_at = null; }}
    render();
  }}).catch(function(){{ alert('Could not undo — try again.'); }});
}}

function loadRows(){{
  fetch(OBR_SB + '/online_booking_requests?select=*&order=requested_at.desc', {{headers: OBR_SHD}})
    .then(function(r){{
      if(!r.ok) throw new Error('load failed (' + r.status + ')');
      return r.json();
    }})
    .then(function(rows){{
      ROWS = Array.isArray(rows) ? rows : [];
      render();
    }})
    .catch(function(err){{
      showError('Could not load booking requests: ' + err.message + ' — refresh to retry.');
    }});
}}

renderTabs();
render();
loadRows();
</script>
</body>
</html>
"""

OUT_HTML.write_text(HTML, encoding="utf-8")
print(f"Written to: {OUT_HTML}")
