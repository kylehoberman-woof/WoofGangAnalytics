"""Temporary diagnostic: dump the raw get_plaintext() output for waitlist
emails so we can see why some Date: lines aren't matching parse_waitlist_signup's
regex in production even though they parse fine from Gmail API text. Will be
removed once resolved.
"""

import email
import imaplib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_booking_requests import get_plaintext, decode_str

IMAP_HOST = "imap.gmail.com"
GMAIL_USER = os.environ.get("BOOKING_REQUESTS_GMAIL_USER", "woofganglongislandops@gmail.com")
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

imap = imaplib.IMAP4_SSL(IMAP_HOST)
imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
imap.select("INBOX")

since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%d-%b-%Y")
status, data = imap.search(None, f'(SINCE "{since}")')
msg_ids = data[0].split()

count = 0
for msg_id in msg_ids:
    status, msg_data = imap.fetch(msg_id, "(RFC822)")
    if status != "OK":
        continue
    msg = email.message_from_bytes(msg_data[0][1])
    subject = decode_str(msg.get("Subject", ""))
    if "joined your waitlist" not in subject.lower():
        continue
    if "reitman" not in decode_str(msg.get("From", "")).lower() and count > 0:
        continue
    body = get_plaintext(msg)
    idx = body.find("Date:")
    print(f"=== msg {msg_id} ===")
    print("Content-Type:", msg.get_content_type())
    for part in msg.walk():
        print("  part:", part.get_content_type(), part.get("Content-Transfer-Encoding"))
    print("body around Date: line:")
    print(repr(body[max(0,idx-20):idx+80]))
    print()
    count += 1
    if count >= 3:
        break

imap.logout()
