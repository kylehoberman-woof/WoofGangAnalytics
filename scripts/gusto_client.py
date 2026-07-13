"""Thin wrapper around Gusto's payroll API.

Auth: OAuth 2.0. Gusto access tokens expire after 2 hours, so a static
GUSTO_ACCESS_TOKEN isn't viable beyond a single quick test. Preferred path:
set GUSTO_CLIENT_ID / GUSTO_CLIENT_SECRET / GUSTO_REFRESH_TOKEN and this
module exchanges the refresh token for a fresh access token at the start of
each run. Gusto rotates the refresh token on every use — the new one is
printed to stderr so it can be saved back into the secret store for next
time. (Auto-persisting it back to GitHub Actions secrets is a follow-up once
this moves from manual pilot to unattended nightly runs.)

GUSTO_ACCESS_TOKEN, if set, is used as-is with no refresh — handy for a
one-off test copy-pasted straight from an OAuth exchange, but will start
failing with 401s after ~2 hours.

Environment: GUSTO_ENV=sandbox (default) uses api.gusto-demo.com, the
self-serve demo environment. GUSTO_ENV=production uses api.gusto.com and
requires Gusto's Production Pre-Approval + Security Review to have been
granted for the connected app.
"""
import os
import sys
import requests

GUSTO_ENV = os.environ.get("GUSTO_ENV", "sandbox")
BASE_URL = "https://api.gusto-demo.com" if GUSTO_ENV == "sandbox" else "https://api.gusto.com"
AUTH_URL = "https://api.gusto-demo.com" if GUSTO_ENV == "sandbox" else "https://api.gusto.com"

_cached_access_token = None


def _refresh_access_token():
    global _cached_access_token
    if _cached_access_token:
        return _cached_access_token

    static_token = os.environ.get("GUSTO_ACCESS_TOKEN")
    client_id = os.environ.get("GUSTO_CLIENT_ID")
    client_secret = os.environ.get("GUSTO_CLIENT_SECRET")
    refresh_token = os.environ.get("GUSTO_REFRESH_TOKEN")

    if not (client_id and client_secret and refresh_token):
        if static_token:
            _cached_access_token = static_token
            return _cached_access_token
        raise RuntimeError(
            "No Gusto credentials set. Provide GUSTO_CLIENT_ID + GUSTO_CLIENT_SECRET + "
            "GUSTO_REFRESH_TOKEN (preferred, auto-refreshes), or GUSTO_ACCESS_TOKEN for a "
            "one-off test (expires in ~2 hours)."
        )

    r = requests.post(
        f"{AUTH_URL}/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _cached_access_token = data["access_token"]
    print(f"NEW GUSTO_REFRESH_TOKEN (save this, the old one is now invalid): {data['refresh_token']}",
          file=sys.stderr)
    return _cached_access_token


def _headers():
    token = _refresh_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_unprocessed_payrolls(company_uuid):
    """List unprocessed (draft) payrolls for a company."""
    r = requests.get(
        f"{BASE_URL}/v1/companies/{company_uuid}/payrolls",
        params={"processed": "false"},
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def find_payroll_for_period(company_uuid, start_date, end_date):
    """Find the unprocessed payroll whose pay period matches the given dates.

    Returns None if no matching draft payroll exists yet in Gusto (e.g. the
    pay schedule hasn't generated it, or it's already been processed).
    """
    for p in get_unprocessed_payrolls(company_uuid):
        if p.get("pay_period", {}).get("start_date") == start_date and \
           p.get("pay_period", {}).get("end_date") == end_date:
            return p
    return None


def prepare_payroll(company_uuid, payroll_uuid):
    """Lock in a version token + fetch current employee_compensations shape.

    Must be called immediately before update_payroll — the returned `version`
    is required for the update call and changes if anyone else touches the
    payroll (via Gusto's UI or API) in between.
    """
    r = requests.put(
        f"{BASE_URL}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}/prepare",
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def update_payroll(company_uuid, payroll_uuid, employee_compensations):
    """Push compensation data into a draft payroll. Never processes/submits it —
    that stays a manual step in Gusto's own UI so a human always signs off
    before money moves.

    employee_compensations: list of dicts matching Gusto's schema, each with
    employee_uuid, version (from prepare_payroll), and any of
    fixed_compensations / hourly_compensations / paid_time_off.
    """
    r = requests.put(
        f"{BASE_URL}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}",
        headers=_headers(),
        json={"employee_compensations": employee_compensations},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()
