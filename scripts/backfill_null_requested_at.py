"""One-off backfill: recompute requested_at for online_booking_requests rows
that came in null because of the Unicode narrow-no-break-space bug in
parse_waitlist_signup's date regex (fixed in fetch_booking_requests.py).
Re-fetches just those specific messages by Message-ID and re-parses with the
fixed regex. Safe to delete once run — the bug it backfills around is fixed,
so no new null rows of this kind should appear.
"""

import email
import imaplib
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from fetch_booking_requests import (
    IMAP_HOST, GMAIL_USER, GMAIL_APP_PASSWORD, SUPABASE_URL, SB_HEADERS,
    decode_str, get_plaintext, LEAD_TYPES,
)

r = requests.get(
    f"{SUPABASE_URL}/online_booking_requests?select=id,gmail_message_id&requested_at=is.null",
    headers=SB_HEADERS, timeout=30,
)
r.raise_for_status()
rows = r.json()
print(f"{len(rows)} rows with null requested_at")
if not rows:
    sys.exit(0)

imap = imaplib.IMAP4_SSL(IMAP_HOST)
imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
imap.select("INBOX")

fixed = 0
for row in rows:
    msg_id_header = row["gmail_message_id"]
    status, data = imap.search(None, f'(HEADER Message-ID "{msg_id_header}")')
    if status != "OK" or not data[0]:
        print(f"  Could not find message for {msg_id_header}")
        continue
    imap_id = data[0].split()[0]
    status, msg_data = imap.fetch(imap_id, "(RFC822)")
    if status != "OK":
        continue
    msg = email.message_from_bytes(msg_data[0][1])
    subject = decode_str(msg.get("Subject", ""))
    parser = next((fn for key, fn in LEAD_TYPES if key in subject.lower()), None)
    if parser is None:
        continue
    body = get_plaintext(msg)
    lead = parser(body)
    if not lead["requested_at"]:
        print(f"  Still couldn't parse a date for {msg_id_header}")
        continue
    patch = requests.patch(
        f"{SUPABASE_URL}/online_booking_requests?id=eq.{row['id']}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json={"requested_at": lead["requested_at"]},
        timeout=30,
    )
    if patch.ok:
        fixed += 1
        print(f"  Fixed {msg_id_header} -> {lead['requested_at']}")
    else:
        print(f"  ERROR patching {msg_id_header}: {patch.status_code} {patch.text}")

imap.logout()
print(f"\nDone! {fixed}/{len(rows)} backfilled.")
