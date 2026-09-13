"""Fetch online booking-request leads from the shared Gmail inbox that store
mailboxes auto-forward "New Appointment Information Request" emails into.

Each store's Outlook mailbox (hicksvilleny@, glencoveny@, portwashingtonny@
@woofgangbakery.com) has a forwarding rule pointed at
woofganglongislandops@gmail.com. The forwarded email arrives with that
store's own address as its sender — that's the only signal used to route a
lead to its store, no parsing of forwarded headers required.

Runs hourly via GitHub Actions during business hours. Writes straight to the
online_booking_requests Supabase table — no local JSON, no git commit.

Usage:
    python3 scripts/fetch_booking_requests.py
"""

import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import STORE_REGISTRY, PROJ_ROOT

IMAP_HOST = "imap.gmail.com"
GMAIL_USER = os.environ.get("BOOKING_REQUESTS_GMAIL_USER", "woofganglongislandops@gmail.com")
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

SUPABASE_URL = "https://bqzinttbjeeaybywhhet.supabase.co/rest/v1"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxemludHRiamVlYXlieXdoaGV0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM3MDU3NDUsImV4cCI6MjA4OTI4MTc0NX0.B2MqUy_WEWOo8NVpGxHibuh-8xLklsy3Ux4DnXp9zmQ"
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Which store a forwarded lead belongs to, keyed by the store mailbox that
# forwarded it (not the new-lead customer's own address).
STORE_BY_SENDER = {
    "hicksvilleny@woofgangbakery.com": "hicksville",
    "glencoveny@woofgangbakery.com": "glen-cove",
    "portwashingtonny@woofgangbakery.com": "port-washington",
}

LOOKBACK_DAYS = 3  # generous buffer for an hourly job; dedup is by message-id anyway


def build_phone_index():
    """phone (digits only) -> set of store keys already carrying that number.

    Lets a lead get flagged as "already a customer at <store>" — including a
    different store than the one they just inquired about, e.g. an existing
    Port Washington customer asking about the new Glen Cove location. That's
    useful context, not a problem: it just means the same household is
    covered by more than one store's data. Reads whatever customer_phones.json
    files already exist in the repo (a pre-launch store like Glen Cove won't
    have one yet — skipped, not an error, until FranPOS is connected there).
    """
    index = {}
    for store_key in STORE_REGISTRY:
        phones_file = PROJ_ROOT / store_key / "data" / "customer_phones.json"
        if not phones_file.exists():
            continue
        with open(phones_file) as f:
            phones = json.load(f)
        for rec in phones.values():
            phone = re.sub(r"\D", "", rec.get("phone") or "")
            if phone:
                index.setdefault(phone, set()).add(store_key)
    return index


def decode_str(s):
    if not s:
        return ""
    out = ""
    for text, enc in decode_header(s):
        out += text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text
    return out


def get_plaintext(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get("Content-Disposition"):
                charset = part.get_content_charset() or "utf-8"
                html = part.get_payload(decode=True).decode(charset, errors="replace")
                return re.sub("<[^>]+>", " ", html)
        return ""
    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    return payload.decode(charset, errors="replace") if payload else ""


def parse_field(text, label):
    m = re.search(rf"{re.escape(label)}:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def parse_requested_at(raw):
    # "2026-08-29 7:34 AM"
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            # Airtable's "Time of Request" is in the store's local time (ET);
            # treat naive and let Supabase store it as given rather than
            # guessing a UTC offset that shifts with DST.
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


def existing_message_ids():
    r = requests.get(
        f"{SUPABASE_URL}/online_booking_requests?select=gmail_message_id",
        headers=SB_HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    return {row["gmail_message_id"] for row in r.json()}


def main():
    phone_index = build_phone_index()
    print(f"Cross-store phone index: {len(phone_index)} known numbers")

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    imap.select("INBOX")

    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    status, data = imap.search(None, f'(SINCE "{since}")')
    if status != "OK":
        print("IMAP search failed")
        sys.exit(1)

    msg_ids = data[0].split()
    print(f"{len(msg_ids)} messages in the last {LOOKBACK_DAYS} days")

    known_ids = existing_message_ids()
    new_count = 0
    skipped_other_sender = 0
    skipped_other_subject = 0

    for msg_id in msg_ids:
        status, msg_data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        gmail_message_id = (msg.get("Message-ID") or "").strip()
        if not gmail_message_id or gmail_message_id in known_ids:
            continue

        sender_raw = decode_str(msg.get("From", ""))
        sender_match = re.search(r"[\w.+-]+@[\w.-]+", sender_raw)
        sender = (sender_match.group(0) if sender_match else "").lower()
        store = STORE_BY_SENDER.get(sender)
        if not store:
            skipped_other_sender += 1
            continue

        subject = decode_str(msg.get("Subject", ""))
        if "appointment information request" not in subject.lower():
            skipped_other_subject += 1
            continue

        body = get_plaintext(msg)
        customer_name = parse_field(body, "Customer Name")
        customer_email = parse_field(body, "Customer Email")
        customer_phone = re.sub(r"\D", "", parse_field(body, "Customer Phone Number"))
        requested_at = parse_requested_at(parse_field(body, "Time of Request"))

        if not customer_name and not customer_phone:
            print(f"  Skipping {gmail_message_id}: couldn't parse customer info from body")
            continue

        existing_at = ",".join(sorted(phone_index.get(customer_phone, ())))

        row = {
            "store": store,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "requested_at": requested_at,
            "gmail_message_id": gmail_message_id,
            "existing_at": existing_at or None,
        }
        r = requests.post(
            f"{SUPABASE_URL}/online_booking_requests?on_conflict=gmail_message_id",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json=row,
            timeout=30,
        )
        if r.ok:
            new_count += 1
            tag = f" [existing customer: {existing_at}]" if existing_at else ""
            print(f"  + [{store}] {customer_name} ({customer_phone}){tag}")
        else:
            print(f"  ERROR saving {gmail_message_id}: {r.status_code} {r.text}")

    imap.logout()
    print(
        f"\nDone! {new_count} new booking request(s) added "
        f"({skipped_other_sender} from unrecognized senders, {skipped_other_subject} non-booking subjects skipped)."
    )


if __name__ == "__main__":
    main()
