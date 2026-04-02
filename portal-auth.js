// ── Woof Gang Portal — Shared Auth Helpers ──

async function sha256(str) {
  var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(function(b){return b.toString(16).padStart(2,'0');}).join('');
}

function initPinInputs(onComplete) {
  var pins = [1,2,3,4].map(function(i){return document.getElementById('pin-'+i);});
  pins.forEach(function(p, i) {
    p.value = '';
    p.addEventListener('input', function() {
      if (p.value.length === 1 && i < 3) pins[i+1].focus();
      var full = pins.map(function(x){return x.value;}).join('');
      if (full.length === 4) onComplete(full, pins);
    });
    p.addEventListener('keydown', function(e) {
      if (e.key === 'Backspace' && !p.value && i > 0) { pins[i-1].focus(); pins[i-1].value = ''; }
    });
    p.addEventListener('paste', function(e) {
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g,'').slice(0,4);
      text.split('').forEach(function(c,j){if(pins[j]) pins[j].value = c;});
      if (text.length === 4) onComplete(text, pins);
      else if (text.length > 0 && pins[text.length]) pins[text.length].focus();
    });
  });
  pins[0].focus();
}

function showPinError(msg) {
  document.getElementById('pin-error').textContent = msg || 'Incorrect PIN';
  [1,2,3,4].forEach(function(i){document.getElementById('pin-'+i).value = '';});
  document.getElementById('pin-1').focus();
  setTimeout(function(){document.getElementById('pin-error').textContent = '';}, 2000);
}

function unlockPortal() {
  document.getElementById('login-gate').style.display = 'none';
  document.getElementById('app-content').style.display = 'block';
}

function showLoginGate() {
  document.getElementById('login-gate').style.display = 'flex';
}
