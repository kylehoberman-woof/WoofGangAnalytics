"""Fetch online booking-request leads from the shared Gmail inbox that store
mailboxes auto-forward lead-capture emails into.

Each store's Outlook mailbox (hicksvilleny@, glencoveny@, portwashingtonny@
@woofgangbakery.com) has a forwarding rule pointed at
woofganglongislandops@gmail.com. The forwarded email arrives with that
store's own address as its sender — that's the only signal used to route a
lead to its store, no parsing of forwarded headers required.

Two email formats are recognized so far (see LEAD_TYPES below), matched by
subject line, each with its own parser since their body layouts differ:
  - "New Appointment Information Request" — an ad-click landing-page lead
  - "A Pet Parent Has Joined Your Waitlist" — Glen Cove's pre-opening
    waitlist signup form (has pet/question free text, no explicit
    timestamp field of its own)
Add a new (subject_substring, parser_fn) entry to LEAD_TYPES for any
additional format that shows up.

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
    """Single-line field: 'Label: value' up to the next newline."""
    m = re.search(rf"{re.escape(label)}:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def parse_sectioned_fields(text, labels, stop_marker=None):
    """Multi-line-safe field extraction: 'Label: value...' where value can
    span several paragraphs, bounded by wherever the NEXT known label (or
    stop_marker) starts rather than the next newline. Needed for the
    waitlist form's free-text "Tell Us About Your Furbaby!" answer, which
    routinely spans multiple paragraphs before the next label appears.
    """
    marks = []
    for label in labels:
        m = re.search(re.escape(label) + r":?\s*", text)
        if m:
            marks.append((m.start(), m.end(), label))
    if stop_marker:
        m = re.search(re.escape(stop_marker), text)
        if m:
            marks.append((m.start(), m.start(), "__STOP__"))
    marks.sort(key=lambda x: x[0])

    result = {}
    for i, (_, end, label) in enumerate(marks):
        if label == "__STOP__":
            continue
        next_start = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        result[label] = text[end:next_start].strip()
    return result


def parse_datetime_loose(raw):
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%B %d, %Y at %I:%M %p"):
        try:
            # Airtable/Outlook timestamps here are the store's local time
            # (ET); treat naive and let Supabase store it as given rather
            # than guessing a UTC offset that shifts with DST.
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


def parse_appointment_request(body):
    """'New Appointment Information Request' — an ad-click landing-page lead,
    single-line fields, includes its own explicit request timestamp."""
    customer_phone = re.sub(r"\D", "", parse_field(body, "Customer Phone Number"))
    return {
        "customer_name": parse_field(body, "Customer Name"),
        "customer_email": parse_field(body, "Customer Email"),
        "customer_phone": customer_phone,
        "requested_at": parse_datetime_loose(parse_field(body, "Time of Request")),
        "notes": None,
    }


def parse_waitlist_signup(body):
    """'A Pet Parent Has Joined Your Waitlist' — Glen Cove's pre-opening
    waitlist form. No explicit timestamp field; falls back to the quoted
    'Date:' line from Kyle's forward (present whether it's a manual forward
    or, per testing, an Outlook auto-forward rule too)."""
    fields = parse_sectioned_fields(
        body,
        ["Name", "Email", "Phone", "Tell Us About Your Furbaby!", "Any Questions for Us?"],
        stop_marker="Note, if any sections are blank",
    )
    customer_phone = re.sub(r"\D", "", fields.get("Phone", ""))

    date_match = re.search(r"Date:\s*\w+,\s*(\w+ \d+, \d+ at \d+:\d+ [AP]M)", body)
    requested_at = parse_datetime_loose(date_match.group(1)) if date_match else None

    notes_parts = []
    furbaby = fields.get("Tell Us About Your Furbaby!", "").strip()
    if furbaby:
        notes_parts.append(f"Pet: {furbaby}")
    question = fields.get("Any Questions for Us?", "").strip()
    if question:
        notes_parts.append(f"Q: {question}")

    return {
        "customer_name": fields.get("Name", ""),
        "customer_email": fields.get("Email", ""),
        "customer_phone": customer_phone,
        "requested_at": requested_at,
        "notes": " | ".join(notes_parts) or None,
    }


# Subject substring (lowercased) -> parser. Checked in order; first match wins.
LEAD_TYPES = [
    ("appointment information request", parse_appointment_request),
    ("joined your waitlist", parse_waitlist_signup),
]


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
        subject_lower = subject.lower()
        parser = next((fn for key, fn in LEAD_TYPES if key in subject_lower), None)
        if parser is None:
            skipped_other_subject += 1
            continue

        body = get_plaintext(msg)
        lead = parser(body)
        customer_name = lead["customer_name"]
        customer_phone = lead["customer_phone"]

        if not customer_name and not customer_phone:
            print(f"  Skipping {gmail_message_id}: couldn't parse customer info from body")
            continue

        existing_at = ",".join(sorted(phone_index.get(customer_phone, ())))

        row = {
            "store": store,
            "customer_name": customer_name,
            "customer_email": lead["customer_email"],
            "customer_phone": customer_phone,
            "requested_at": lead["requested_at"],
            "gmail_message_id": gmail_message_id,
            "existing_at": existing_at or None,
            "notes": lead["notes"],
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
