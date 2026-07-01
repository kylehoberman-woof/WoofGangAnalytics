"""Fetch employee data from Supabase schedule_employees table.

Falls back to config.py constants if Supabase is empty or unavailable,
so dashboards continue to work before employees are entered in the portal.
"""
import sys
import requests
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

REST_URL = f"{config.SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": config.SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
}


def fetch_employees(store=None, role=None, active_only=True):
    """Fetch employees from Supabase. Returns list of dicts, empty list on error."""
    try:
        filters = []
        if active_only:
            filters.append("is_active=eq.true")
        if store:
            filters.append(f"store=in.({store},both)")
        if role:
            filters.append(f"role=eq.{role}")
        qs = "&".join(filters) + "&order=name"
        resp = requests.get(f"{REST_URL}/schedule_employees?{qs}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[fetch_employees] Supabase unavailable: {exc}", file=sys.stderr)
        return []


def build_name_map(employees):
    """Returns {full_name: short_name} for FranPOS transaction name matching."""
    return {e["full_name"]: e["name"] for e in employees if e.get("full_name")}


def build_rate_map(employees):
    """Returns {short_name: hourly_rate} for pay calculation."""
    return {e["name"]: float(e["hourly_rate"]) for e in employees if e.get("hourly_rate")}


def build_guarantee_map(employees):
    """Returns {franpos_name: (daily_rate, start_iso, end_iso)} in the same
    format as config.GUARANTEES. Guarantee end defaults to hire_date + 90 days
    when guarantee_end_date is not set (standard 90-day onboarding window)."""
    result = {}
    for e in employees:
        if not e.get("guarantee_daily_rate") or not e.get("hire_date"):
            continue
        hire = e["hire_date"]
        end = e.get("guarantee_end_date") or (
            date.fromisoformat(hire) + timedelta(days=90)
        ).isoformat()
        key = e.get("full_name") or e["name"]
        result[key] = (float(e["guarantee_daily_rate"]), hire, end)
    return result


# System/owner names derived from store registry + fixed owner names
_SYSTEM_NAMES = {"Unknown", "Kyle Hoberman", "Julie Schorr"} | {
    f"Wgb {v['display_name']}" for v in config.STORE_REGISTRY.values()
}


def get_exclude_set(store_key):
    """Return the set of FranPOS SalesPerson names to exclude from groomer commission.

    Dynamically built from all non-groomer employees in Supabase (retail, bather,
    manager roles — both active and terminated so historical records are clean),
    plus hardcoded system/owner names that never appear in Supabase.

    Falls back to config.EXCLUDE_EMPLOYEES if Supabase is unavailable.
    """
    non_groomers = fetch_employees(store=store_key, active_only=False)
    non_groomers = [e for e in non_groomers if e.get("role") != "groomer"]

    if non_groomers:
        exclude = set(_SYSTEM_NAMES)
        for e in non_groomers:
            if e.get("full_name"):
                exclude.add(e["full_name"])
            if e.get("name"):
                exclude.add(e["name"])
        return exclude

    # Supabase unavailable — fall back to config
    print("[fetch_employees] get_exclude_set: Supabase unavailable, using config fallback", file=sys.stderr)
    return set(config.EXCLUDE_EMPLOYEES)


def get_store_pay_data(store_key):
    """Return (retail_name_map, retail_rates, bather_name_map, bather_rate_map, guarantees)
    for the given store. Falls back to config.py if Supabase is empty.

    bather_rate_map: {short_name: rate} per-employee rates.
    Use BATHER_RATE_MAP.get(name, config.BATHER_RATE) as fallback in pay calculations.

    NOTE: includes inactive (terminated) employees so their HISTORICAL hours and
    commissions still match against FranPOS clock/order data. Active-only filtering
    is for forward-looking schedule UI, not historical financial dashboards.
    """
    employees = fetch_employees(store=store_key, active_only=False)

    retail_emps  = [e for e in employees if e.get("role") == "retail"]
    bather_emps  = [e for e in employees if e.get("role") == "bather"]
    groomer_emps = [e for e in employees if e.get("role") == "groomer"]

    retail_name_map = build_name_map(retail_emps)
    retail_rates    = build_rate_map(retail_emps)
    bather_name_map = build_name_map(bather_emps)
    bather_rate_map = build_rate_map(bather_emps)
    guarantees      = build_guarantee_map(groomer_emps)

    # Fall back to config.py if Supabase employees haven't been entered yet
    if store_key == "port-washington":
        if not retail_name_map:
            print("[fetch_employees] PW retail: using config fallback", file=sys.stderr)
            retail_name_map = dict(config.RETAIL_NAME_MAP)
        if not retail_rates:
            retail_rates = dict(config.RETAIL_RATES)
        if not bather_name_map:
            bather_name_map = dict(config.BATHER_NAME_MAP)
        if not bather_rate_map:
            bather_rate_map = {n: config.BATHER_RATE for n in config.BATHER_NAME_MAP.values()}
        if not guarantees:
            guarantees = dict(config.GUARANTEES)

    elif store_key == "hicksville":
        if not retail_name_map:
            print("[fetch_employees] HV retail: using config fallback", file=sys.stderr)
            retail_name_map = dict(config.HICKSVILLE_RETAIL_NAME_MAP)
        if not retail_rates:
            retail_rates = dict(config.HICKSVILLE_RETAIL_RATES)
        if not bather_name_map:
            bather_name_map = dict(config.HICKSVILLE_BATHER_NAME_MAP)
        if not bather_rate_map:
            bather_rate_map = {}
        if not guarantees:
            guarantees = dict(config.GUARANTEES)

    return retail_name_map, retail_rates, bather_name_map, bather_rate_map, guarantees
