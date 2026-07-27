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


PTO_AMOUNT_PER_USE = 100.00   # dollars per approved PTO use
PTO_MAX_USES = 5              # lifetime cap per employee
PTO_MIN_TENURE_DAYS = 365     # must have 1 year of employment to be eligible


def fetch_paid_pto(store_key, period_start, period_end):
    """Return {employee_name: dollar_amount} for approved paid-PTO uses whose
    start_date falls inside the pay period [period_start, period_end].

    Policy: full-time groomers with 1+ year tenure get $100 per use, up to 5
    lifetime uses ($500 total). Each time_off row with paid_pto=true is one use.

    Eligibility is checked against schedule_employees (is_full_time + hire_date).
    Lifetime-use counting looks at ALL historical paid_pto records for the employee
    and only includes uses that fall at or before period_end.
    """
    # --- fetch all paid PTO for this store (need full history for lifetime cap) ---
    r = requests.get(
        f"{REST_URL}/time_off",
        params={
            "select": "employee_name,start_date,type",
            "store_key": f"eq.{store_key}",
            "paid_pto": "eq.true",
            "status": "eq.approved",
            "order": "start_date.asc",
        },
        headers=SB_HEADERS,
        timeout=15,
    )
    if not r.ok:
        print(f"WARNING: could not fetch time_off PTO ({r.status_code}) — PTO will not be included", file=sys.stderr)
        return {}

    all_pto = r.json()

    # --- fetch employee eligibility ---
    re = requests.get(
        f"{REST_URL}/schedule_employees",
        params={
            "select": "name,full_name,is_full_time,hire_date,role",
            "store": f"in.({store_key},both)",
            "role": "eq.groomer",
        },
        headers=SB_HEADERS,
        timeout=15,
    )
    emp_map: dict[str, dict] = {}  # {name_variant: employee_row}
    if re.ok:
        for e in re.json():
            for key in [e.get("full_name"), e.get("name")]:
                if key:
                    emp_map[key] = e

    def _eligible(name, use_date_iso):
        """Is this employee eligible to receive PTO on use_date?"""
        e = emp_map.get(name)
        if not e:
            print(f"  PTO: {name} not found in schedule_employees — skipping", file=sys.stderr)
            return False
        if not e.get("is_full_time"):
            print(f"  PTO: {name} is not full-time — skipping", file=sys.stderr)
            return False
        if not e.get("hire_date"):
            print(f"  PTO: {name} has no hire_date — skipping", file=sys.stderr)
            return False
        tenure = (date.fromisoformat(use_date_iso) - date.fromisoformat(e["hire_date"])).days
        if tenure < PTO_MIN_TENURE_DAYS:
            print(f"  PTO: {name} has {tenure} days tenure (need {PTO_MIN_TENURE_DAYS}) — skipping", file=sys.stderr)
            return False
        return True

    # Track lifetime uses per employee (across all history up to period_end)
    lifetime_uses: dict[str, int] = {}
    for row in all_pto:
        if row["start_date"] > period_end:
            continue
        name = row["employee_name"]
        lifetime_uses[name] = lifetime_uses.get(name, 0) + 1

    # Now compute what's owed this period
    pto_dollars: dict[str, float] = {}
    uses_before_period: dict[str, int] = {}
    for row in all_pto:
        name = row["employee_name"]
        if row["start_date"] < period_start:
            uses_before_period[name] = uses_before_period.get(name, 0) + 1
            continue
        if row["start_date"] > period_end:
            continue
        # This use falls in the current period
        prior_uses = uses_before_period.get(name, 0)
        if prior_uses >= PTO_MAX_USES:
            print(f"  PTO: {name} already at lifetime cap ({PTO_MAX_USES} uses) — skipping", file=sys.stderr)
            continue
        if not _eligible(name, row["start_date"]):
            continue
        pto_dollars[name] = pto_dollars.get(name, 0.0) + PTO_AMOUNT_PER_USE
        uses_before_period[name] = prior_uses + 1  # count for subsequent rows in same period

    if pto_dollars:
        print(f"Paid PTO this period: {pto_dollars}")
    return pto_dollars


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


def build_compensations(period, groomer_pay, gusto_ids, store_key, pto_hours=None):
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

    # Paid PTO — $100/use flat, added as a fixed compensation line ("PTO")
    for name, dollars in (pto_hours or {}).items():
        if dollars <= 0:
            continue
        ids = _ids_for(name)
        if not ids:
            continue
        pto_comp = {"name": "PTO", "amount": f"{dollars:.2f}", "job_uuid": ids["job_uuid"]}
        existing = next((c for c in comps if c.get("employee_uuid") == ids["employee_uuid"]), None)
        if existing is not None:
            existing.setdefault("fixed_compensations", []).append(pto_comp)
        else:
            comps.append({
                "employee_uuid": ids["employee_uuid"],
                "fixed_compensations": [pto_comp],
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
    ap.add_argument("--dry-run", action="store_true", help="Print the JSON payload instead of calling Gusto")
    ap.add_argument("--csv", action="store_true", help="Write a human-readable CSV summary to stdout")
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
    pto_dollars = fetch_paid_pto(args.store, start, end)
    comps = build_compensations(period, groomer_pay, gusto_ids, args.store, pto_hours=pto_dollars)

    print(f"Built {len(comps)} employee_compensations entries")

    if args.csv:
        import csv as _csv
        import io
        buf = io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["Employee", "Commission", "Tips", "PTO", "Bather Hours", "Retail Hours", "Gusto UUID"])
        # Build lookup for bather/retail hours
        bather_h = period.get("_bather_hours") or {}
        retail_h = period.get("_retail_hours") or {}
        all_names = (
            set(groomer_pay) | set(bather_h) | set(retail_h) | set(pto_dollars)
        )
        ids_rev = {v["employee_uuid"]: k for k, v in gusto_ids.items()}
        for name in sorted(all_names):
            gp = groomer_pay.get(name, {})
            uuid = gusto_ids.get(name, {}).get("employee_uuid", "— missing —")
            w.writerow([
                name,
                f"${gp.get('paid', 0):.2f}" if gp else "",
                f"${gp.get('tips', 0):.2f}" if gp else "",
                f"${pto_dollars.get(name, 0):.2f}" if name in pto_dollars else "",
                f"{bather_h.get(name, 0):.2f} hrs" if name in bather_h else "",
                f"{retail_h.get(name, 0):.2f} hrs" if name in retail_h else "",
                uuid,
            ])
        print(buf.getvalue())
        return

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
