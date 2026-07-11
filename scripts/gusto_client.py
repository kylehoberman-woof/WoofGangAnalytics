"""Thin wrapper around Gusto's payroll API.

Auth: OAuth 2.0 access token, read from GUSTO_ACCESS_TOKEN env var (a GitHub
Actions secret in production, same pattern as FRANPOS_TOKEN). Token refresh
is out of scope for v1 — Gusto access tokens are long-lived enough for manual
refresh during the pilot; revisit once this runs unattended.

Environment: GUSTO_ENV=sandbox (default) uses api.gusto-demo.com, the
self-serve demo environment. GUSTO_ENV=production uses api.gusto.com and
requires Gusto's Production Pre-Approval + Security Review to have been
granted for the connected app.
"""
import os
import requests

GUSTO_ENV = os.environ.get("GUSTO_ENV", "sandbox")
BASE_URL = "https://api.gusto-demo.com" if GUSTO_ENV == "sandbox" else "https://api.gusto.com"


def _headers():
    token = os.environ.get("GUSTO_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("GUSTO_ACCESS_TOKEN not set")
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
