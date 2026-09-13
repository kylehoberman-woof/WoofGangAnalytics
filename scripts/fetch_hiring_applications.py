"""Fetch job applications from the shared Gmail inbox and land them in the
existing hiring pipeline (hiring_candidates / hiring_contact_log — the same
Supabase tables hiring-portal.html already reads/writes).

Source is "Work at Woof Gang" specifically — the employer's own application
system (an Airtable form, plus a groomer-specific variant sent from
storerecruitment@woofgangbakery.com), forwarded from each store's Outlook
mailbox the same way online booking requests are. Indeed and walk-in/phone
candidates are separate sources handled elsewhere (manually, for now).

Both known subject variants ("New Application Notification" and "New
Groomer Application Notification") share the same body shape — an opening
"You have received a new candidate for <Position>" line followed by
"Label: value" paragraphs — but the exact field set differs between them
(general applicants get Address/Availability, groomer applicants get Years
of Experience/Specialties/etc). Rather than hardcode two field lists, this
scans generically for every "Label: value" line in the body: name/email/
phone go straight onto the candidate record, everything else (position,
availability, specialties, whatever else shows up) is preserved as the
first hiring_contact_log entry so nothing gets silently dropped if the
form's fields change.

role_id is deliberately left null — auto-matching free-text position/
location to a specific open hiring_roles row risks linking someone to the
wrong posting. The position and store are folded into `source` instead so
it's still visible in the portal; assign the exact role manually there.

Runs hourly via GitHub Actions (same job as fetch_booking_requests.py).
Writes straight to Supabase — no local JSON, no git commit.

Usage:
    python3 scripts/fetch_hiring_applications.py
"""

import email
import imaplib
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header

import requests

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

# Which store forwarded the application (not necessarily the store the
# position is at — usually the same, but source/notes carry the real
# position+location text regardless).
STORE_LABEL_BY_SENDER = {
    "hicksvilleny@woofgangbakery.com": "Hicksville",
    "glencoveny@woofgangbakery.com": "Glen Cove",
    "portwashingtonny@woofgangbakery.com": "Port Washington",
}

LOOKBACK_DAYS = 3

# Boilerplate labels that appear in the quoted-forward headers and footer —
# excluded so they don't get swept up by the generic Label: value scan.
IGNORED_LABELS = {"from", "date", "to", "cc", "subject", "caution"}


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


def parse_work_application(body):
    position_match = re.search(r"new candidate for\s+(.+)", body)
    position = position_match.group(1).strip() if position_match else ""

    start_idx = position_match.end() if position_match else 0
    stop_match = re.search(r"Sent via Automations", body)
    region = body[start_idx: stop_match.start() if stop_match else len(body)]

    fields = {}
    for line in region.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z \t'/]{1,40}?):\s*:?\s*(.*)$", line)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if label.lower() in IGNORED_LABELS or not value:
            continue
        fields[label] = value

    first = fields.pop("First Name", "")
    last = fields.pop("Last Name", "")
    name = f"{first} {last}".strip()
    email_addr = fields.pop("Email", "")
    phone = re.sub(r"\D", "", fields.pop("Phone", ""))

    extra_lines = [f"{k}: {v}" for k, v in fields.items()]
    return {
        "name": name,
        "email": email_addr,
        "phone": phone,
        "position": position,
        "extra_notes": "\n".join(extra_lines),
    }


LEAD_TYPES = [
    ("application notification", parse_work_application),
]


def existing_message_ids():
    r = requests.get(
        f"{SUPABASE_URL}/hiring_candidates?select=gmail_message_id&gmail_message_id=not.is.null",
        headers=SB_HEADERS, timeout=30,
    )
    r.raise_for_status()
    return {row["gmail_message_id"] for row in r.json()}


def main():
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
        store_label = STORE_LABEL_BY_SENDER.get(sender)
        if not store_label:
            skipped_other_sender += 1
            continue

        subject = decode_str(msg.get("Subject", ""))
        parser = next((fn for key, fn in LEAD_TYPES if key in subject.lower()), None)
        if parser is None:
            skipped_other_subject += 1
            continue

        body = get_plaintext(msg)
        app = parser(body)

        if not app["name"] and not app["phone"] and not app["email"]:
            print(f"  Skipping {gmail_message_id}: couldn't parse candidate info from body")
            continue

        source_bits = ["Work at Woof Gang"]
        if app["position"]:
            source_bits.append(app["position"])
        source_bits.append(store_label)
        source = " — ".join(source_bits)

        candidate_row = {
            "name": app["name"] or "(name not parsed)",
            "phone": app["phone"],
            "email": app["email"],
            "role_id": None,
            "source": source,
            "status": "new",
            "added_by": "Auto (Work at Woof Gang)",
            "gmail_message_id": gmail_message_id,
        }
        r = requests.post(
            f"{SUPABASE_URL}/hiring_candidates",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=candidate_row,
            timeout=30,
        )
        if not r.ok:
            print(f"  ERROR saving candidate {gmail_message_id}: {r.status_code} {r.text}")
            continue

        saved = r.json()
        candidate = saved[0] if isinstance(saved, list) else saved
        new_count += 1
        print(f"  + [{store_label}] {app['name']} — {app['position'] or '(no position)'}")

        if app["extra_notes"]:
            log_row = {
                "candidate_id": candidate["id"],
                "contact_date": date.today().isoformat(),
                "method": "other",
                "logged_by": "Automated (Work at Woof Gang)",
                "notes": f"Applied via Work at Woof Gang form.\n\n{app['extra_notes']}",
            }
            log_r = requests.post(
                f"{SUPABASE_URL}/hiring_contact_log",
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
                json=log_row,
                timeout=30,
            )
            if not log_r.ok:
                print(f"    WARNING: candidate saved but contact log entry failed: {log_r.status_code} {log_r.text}")

    imap.logout()
    print(
        f"\nDone! {new_count} new candidate(s) added "
        f"({skipped_other_sender} from unrecognized senders, {skipped_other_subject} non-application subjects skipped)."
    )


if __name__ == "__main__":
    main()
