"""Push a closed pay period's computed pay into a draft Gusto payroll.

Reuses the exact numbers already computed by build_commission_dashboard.py
(via data/pay_periods.json) plus any manual corrections stored in Supabase
(guarantee_overrides, commission_adjustments) — so what lands in Gusto always
matches what's shown on the commission dashboard. Never processes/submits the
payroll; it only fills in a draft for a human to review and approve inside
Gusto's own UI.

Usage:
    python3 push_to_gusto.py port-washington              # most recent closed period
    python3 push_to_gusto.py port-washington --period pp_3
    python3 push_to_gusto.py port-washington --dry-run     # print payload, don't call Gusto
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import config
import gusto_client

REST_URL = f"{config.SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "apikey": config.SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
}


def load_pay_periods(store_key):
    store = config.get_store(store_key)
    path = store.data_dir / "pay_periods.json"
    if not path.exists():
        raise RuntimeError(f"{path} not found — run build_commission_dashboard.py {store_key} first")
    with open(path) as f:
        return json.load(f)


def pick_period(pp_json, period_id=None):
    """Return (period_id, start_date, end_date). Defaults to the most recently
    closed period (end_date strictly before today) so we never push a period
    that's still accumulating shifts."""
    dates = pp_json["dates"]
    if period_id:
        if period_id not in dates:
            raise RuntimeError(f"Unknown period {period_id}")
        d = dates[period_id]
        return period_id, d["start"], d["end"]

    today = date.today().isoformat()
    closed = [(pid, d["start"], d["end"]) for pid, d in dates.items() if d["end"] < today]
    if not closed:
        raise RuntimeError("No closed pay periods found")
    # dates dict isn't guaranteed sorted — pick max by end date
    return max(closed, key=lambda t: t[2])


def fetch_overrides_and_adjustments(store_key):
    """Mirrors _loadOverrides() in build_commission_dashboard.py's JS."""
    r = requests.get(
        f"{REST_URL}/guarantee_overrides",
        params={"select": "groomer_name,override_date,waived"},
        headers=SB_HEADERS, timeout=15,
    )
    waived = set()
    if r.ok:
        for row in r.json():
            if row.get("waived"):
                waived.add((row["groomer_name"], row["override_date"]))

    r = requests.get(
        f"{REST_URL}/commission_adjustments",
        params={"select": "groomer_name,adj_date,adj_type,corrected_amount", "store_key": f"eq.{store_key}"},
        headers=SB_HEADERS, timeout=15,
    )
    adj = {}
    if r.ok:
        for row in r.json():
            adj.setdefault(row["groomer_name"], {}).setdefault(row["adj_date"], {})[row["adj_type"]] = float(row["corrected_amount"])

    return waived, adj


def fetch_employee_gusto_ids(store_key):
    """{franpos_name: {"employee_uuid": ..., "job_uuid": ...}} from schedule_employees."""
    r = requests.get(
        f"{REST_URL}/schedule_employees",
        params={
            "select": "name,full_name,store,gusto_employee_uuid,gusto_job_uuid",
            "store": f"in.({store_key},both)",
        },
        headers=SB_HEADERS, timeout=15,
    )
    if not r.ok:
        print(f"WARNING: could not fetch gusto_employee_uuid mapping ({r.status_code}: {r.text[:200]}). "
              f"Has the schedule_employees migration been run yet? Treating everyone as unmapped.",
              file=sys.stderr)
        return {}
    out = {}
    for e in r.json():
        if not e.get("gusto_employee_uuid"):
            continue
        key = e.get("full_name") or e["name"]
        out[key] = {"employee_uuid": e["gusto_employee_uuid"], "job_uuid": e.get("gusto_job_uuid")}
    return out


def compute_groomer_pay(period, waived, adj):
    """Recompute final paid/tips per groomer for the period, applying the same
    guarantee-override and commission-adjustment logic as renderPayPeriod()
    in the dashboard JS. Returns {groomer_name: {"paid": x, "tips": y}}."""
    result = {}
    for name, d in period.items():
        if name.startswith("_"):
            continue
        daily = d.get("daily") or []
        if not daily:
            # No day-level detail (shouldn't normally happen) — fall back to
            # the period totals as computed.
            result[name] = {"paid": d.get("paid", 0), "tips": d.get("tips", 0)}
            continue
        paid = tips = 0.0
        for day in daily:
            comm_val = adj.get(name, {}).get(day["date"], {}).get("commission", day["comm"])
            tips_val = adj.get(name, {}).get(day["date"], {}).get("tip", day["tips"])
            guar_active = day.get("guar_applied") and (name, day["date"]) not in waived
            # Guarantee rate itself isn't re-derived here — guar_applied already
            # encodes "commission < guarantee rate for this day", and the paid
            # amount pre-override is in the exported `paid` field per-day if
            # present. Fall back to comm_val when no per-day paid is exported.
            day_paid = day.get("paid", comm_val)
            paid += day_paid if guar_active else comm_val
            tips += tips_val
        result[name] = {"paid": round(paid, 2), "tips": round(tips, 2)}
    return result


def build_compensations(period, groomer_pay, gusto_ids, store_key):
    """Build Gusto's employee_compensations payload. Skips anyone without a
    gusto_employee_uuid mapping in schedule_employees (prints a warning so
    they're not silently dropped from payroll)."""
    comps = []
    skipped = []

    def _ids_for(name):
        ids = gusto_ids.get(name)
        if not ids:
            skipped.append(name)
        return ids

    for name, pay in groomer_pay.items():
        ids = _ids_for(name)
        if not ids:
            continue
        fixed = []
        if pay["paid"]:
            fixed.append({"name": "Commission", "amount": f"{pay['paid']:.2f}", "job_uuid": ids["job_uuid"]})
        if pay["tips"]:
            fixed.append({"name": "Cash Tips", "amount": f"{pay['tips']:.2f}", "job_uuid": ids["job_uuid"]})
        if fixed:
            comps.append({"employee_uuid": ids["employee_uuid"], "fixed_compensations": fixed})

    for name, hours in (period.get("_bather_hours") or {}).items():
        ids = _ids_for(name)
        if not ids or not hours:
            continue
        comps.append({
            "employee_uuid": ids["employee_uuid"],
            "hourly_compensations": [{"name": "Regular Hours", "hours": f"{hours:.3f}", "job_uuid": ids["job_uuid"]}],
        })
        tips = (period.get("_bather_tips") or {}).get(name, 0)
        if tips:
            comps.append({"employee_uuid": ids["employee_uuid"], "fixed_compensations": [
                {"name": "Cash Tips", "amount": f"{tips:.2f}", "job_uuid": ids["job_uuid"]}
            ]})

    for name, hours in (period.get("_retail_hours") or {}).items():
        ids = _ids_for(name)
        if not ids or not hours:
            continue
        comps.append({
            "employee_uuid": ids["employee_uuid"],
            "hourly_compensations": [{"name": "Regular Hours", "hours": f"{hours:.3f}", "job_uuid": ids["job_uuid"]}],
        })

    if skipped:
        print(f"WARNING: no gusto_employee_uuid on file for: {sorted(set(skipped))} — "
              f"their pay for this period was NOT included. Add them to schedule_employees first.",
              file=sys.stderr)

    return comps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--period", help="Specific period id, e.g. pp_3. Defaults to most recent closed period.")
    ap.add_argument("--dry-run", action="store_true", help="Print the payload instead of calling Gusto")
    args = ap.parse_args()

    reg = config.STORE_REGISTRY.get(args.store, {})
    uuid_env = reg.get("gusto_company_uuid_env")
    if not uuid_env:
        raise RuntimeError(f"No gusto_company_uuid_env configured for {args.store} in data/stores.json")
    import os
    company_uuid = os.environ.get(uuid_env)
    if not company_uuid and not args.dry_run:
        raise RuntimeError(f"{uuid_env} not set")

    pp_json = load_pay_periods(args.store)
    period_id, start, end = pick_period(pp_json, args.period)
    period = pp_json["periods"][period_id]
    print(f"Period: {period_id} ({start} to {end})")

    waived, adj = fetch_overrides_and_adjustments(args.store)
    groomer_pay = compute_groomer_pay(period, waived, adj)
    gusto_ids = fetch_employee_gusto_ids(args.store)
    comps = build_compensations(period, groomer_pay, gusto_ids, args.store)

    print(f"Built {len(comps)} employee_compensations entries")
    if args.dry_run:
        print(json.dumps(comps, indent=2))
        return

    payroll = gusto_client.find_payroll_for_period(company_uuid, start, end)
    if not payroll:
        raise RuntimeError(
            f"No unprocessed Gusto payroll found for {start}..{end} — "
            f"check the pay schedule in Gusto matches, or it's already been processed."
        )
    prepared = gusto_client.prepare_payroll(company_uuid, payroll["payroll_uuid"])
    version_by_employee = {ec["employee_uuid"]: ec["version"] for ec in prepared["employee_compensations"]}
    for c in comps:
        v = version_by_employee.get(c["employee_uuid"])
        if v is None:
            print(f"WARNING: employee_uuid {c['employee_uuid']} not in this payroll's prepared list, skipping", file=sys.stderr)
            continue
        c["version"] = v

    result = gusto_client.update_payroll(company_uuid, payroll["payroll_uuid"], comps)
    print(f"Updated draft payroll {payroll['payroll_uuid']} — review and approve in Gusto.")
    print(json.dumps(result, indent=2)[:500])


if __name__ == "__main__":
    main()
