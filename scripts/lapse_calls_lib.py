"""Shared logic for turning a store's pet_visits.json + all_data.json into a
clean list of "lapse call" candidates — dogs who had a real visit, haven't
been back since, and have nothing booked. Used by build_pet_dashboard.py
(for its Daily-tab summary counts) and build_lapse_calls.py (the dedicated
Lapse Calls widget).
"""

import re
from datetime import date
from collections import defaultdict


# FranPOS's customer-history feed mixes real appointments in with internal
# groomer notes (clipper settings, scheduling asides, "Ok'd by Cindy", etc.) —
# there's no clean flag for it, so classify by shape: a real appointment
# almost always has a groomer assigned or a real size code, or its text
# names a known service. Free-text notes typically have none of those.
# Word-boundary matched (not raw substring) — otherwise "groom" matches
# inside "groomed", which shows up constantly in note prose like "Maria
# groomed the dog and noticed...".
SERVICE_KEYWORD_RE = re.compile(
    r"\b(fg|mg|full groom|mini groom|lux bath|bath|trim|nail|groom|spa|"
    r"teeth|gland|deshed|de-shed|online service|brush|blowout|add-on|mini)\b",
    re.IGNORECASE,
)
# A free-text note that happens to contain "/" gets split across service /
# breed_group / size by the same delimiter parsing that handles real
# "service / breed_group / size" records, so a merely non-empty size field
# isn't safe on its own — require it to actually look like a size code.
SIZE_CODE_RE = re.compile(r"^(xs|sm|md|lg|xlg|xl|general|teeth brushing)\b", re.IGNORECASE)

GROOM_KEYWORDS = {"full groom", "bath", "lux bath", "groom", "trim", "nail"}


def is_real_appointment(v):
    if v.get("stylist"):
        return True
    size = (v.get("size") or "").strip()
    if size and SIZE_CODE_RE.match(size):
        return True
    # Only the "service" field itself, not items_raw — for a note-corrupted
    # record items_raw is just the whole note restated across three fields,
    # and checking it here would match keywords buried in that prose too.
    return bool(SERVICE_KEYWORD_RE.search(v.get("service") or ""))


def get_store_tag(store_name, store_registry):
    """'(#264)'-style tag for this store, or None if not found."""
    number = store_registry.get(store_name, {}).get("store_number")
    return f"(#{number})" if number else None


def filter_pet_records_to_store(pet_records, store_tag):
    """Strip cross-location visits and recompute each record's summary
    fields (total_visits, last_visit) to match — in place, returns nothing.

    FranPOS's customer-history API returns a customer's visits network-wide
    across the whole Woof Gang franchise, not scoped to one location. A
    family groomed at a different city's store shows up in the same feed.
    """
    for rec in pet_records:
        rec["visits"] = [
            v for v in rec.get("visits", [])
            if store_tag is None or store_tag in (v.get("store") or "")
        ]
        rec["total_visits"] = len(rec["visits"])
        dated = [v["date"] for v in rec["visits"] if v.get("date")]
        rec["last_visit"] = max(dated) if dated else ""


def load_customer_visit_staff(all_data_file):
    """(CustomerId, date) -> [staff names] on a grooming order item.

    Used as the Last Groomer fallback when a visit has no stylist recorded
    — cross-checked against confirmed-stylist visits, this order-level
    field agrees 94% of the time. pet_visits.json's OWN embedded
    "salesperson" field, by contrast, disagrees with confirmed stylists 62%
    of the time — it reflects whoever logged the customer-history note,
    not who worked the appointment — so it's only used as a last resort.
    """
    import json

    customer_visit_staff = defaultdict(list)
    if not all_data_file.exists():
        return customer_visit_staff

    with open(all_data_file) as f:
        all_data = json.load(f)
    order_items = all_data if isinstance(all_data, list) else all_data.get("order_items", [])

    for item in order_items:
        staff = (item.get("EmployeeName") or item.get("SalesPerson") or "").strip()
        cust_id = item.get("CustomerId")
        dt = item.get("Date") or item.get("CreatedOn") or ""
        day = dt[:10] if dt else ""
        if staff and cust_id and day and any(k in (item.get("Name") or "").lower() for k in GROOM_KEYWORDS):
            customer_visit_staff[(cust_id, day)].append(staff)

    return customer_visit_staff


def pet_name_variants(pet_name):
    parts = {p.strip() for p in (pet_name or "").split(",") if p.strip()}
    parts.add((pet_name or "").strip())
    return parts


def compute_lapse_candidates(pet_records, customer_visit_staff, today_date=None):
    """Returns (lapsed_dogs: list[dict], lapse_history: dict[cid -> {...}]).

    pet_records must already be store-scoped (filter_pet_records_to_store).
    """
    today_date = today_date or date.today()
    today_iso = today_date.isoformat()

    lapsed_dogs = []
    lapse_history = {}

    owners_with_future_pet = set()
    for rec in pet_records:
        if any(v.get("date", "") > today_iso for v in rec.get("visits", []) if is_real_appointment(v)):
            owner = rec.get("owner_name", "")
            for name in pet_name_variants(rec.get("pet_name", "")):
                owners_with_future_pet.add((owner, name))

    # Plain duplicate accounts: many (owner, pet name) pairs have more than
    # one FranPOS pet_cid for what's clearly the same dog — same owner,
    # identical name, history just split across accounts however FranPOS
    # happened to create them. Merge every non-comma record sharing
    # (owner_name, pet_name) into one group before computing anything.
    individual_groups = defaultdict(list)  # (owner_name, pet_name) -> [records]
    combined_records = []
    for rec in pet_records:
        name = (rec.get("pet_name") or "").strip()
        if "," in name:
            combined_records.append(rec)
        else:
            individual_groups[(rec.get("owner_name", ""), name)].append(rec)

    individual_pet_names = set(individual_groups.keys())

    # A combined multi-pet account ("Lucy, Mason") almost always duplicates
    # individual accounts that already exist for each name under the same
    # owner. Skip it entirely once every one of its names already has its
    # own individual group; otherwise fold its visits into the group(s) for
    # the name(s) that don't, so a genuinely orphaned combined account
    # still contributes (and still shows as its own line item once split
    # per name below).
    for rec in combined_records:
        owner = rec.get("owner_name", "")
        names = [n.strip() for n in (rec.get("pet_name") or "").split(",") if n.strip()]
        for n in names:
            if (owner, n) not in individual_pet_names:
                individual_groups[(owner, n)].append(rec)

    for (owner_name_, pet_name_), group_records in individual_groups.items():
        all_dated_visits = []
        for rec in group_records:
            origin_cid = rec.get("pet_cid")
            all_dated_visits.extend(
                {**v, "_origin_cid": origin_cid} for v in rec.get("visits", []) if v.get("date")
            )
        all_dated_visits.sort(key=lambda v: v["date"], reverse=True)

        # Retail purchases (treats, food, shampoo) and internal notes ride
        # along in the same history feed as real grooming appointments —
        # last-visit and cadence are service-based only, not "last time
        # they bought something here."
        real_visits = [v for v in all_dated_visits if is_real_appointment(v)]
        has_future_sibling = (owner_name_, pet_name_) in owners_with_future_pet

        # Already has something on the books — no outreach needed
        # regardless of how overdue their past visit history looks.
        if any(v["date"] > today_iso for v in real_visits) or has_future_sibling:
            continue

        visits = [v for v in real_visits if v["date"] <= today_iso]
        if len(visits) < 1:
            continue

        last_visit_str = visits[0]["date"]
        days_since = (today_date - date.fromisoformat(last_visit_str)).days

        # Who counts as a candidate is purely "had a real visit, hasn't
        # been back since, nothing booked" — an associate picks the
        # last-visit date range themselves rather than the tool deciding
        # who's "due" for them personally. The Lapsed/At Risk ratio
        # against their OWN historical frequency is kept only as extra
        # context where there's enough history (3+ real visits) to
        # compute a meaningful personal baseline — it doesn't gate who
        # appears.
        avg_interval = None
        ratio = None
        status = None
        days_overdue = None
        if len(visits) >= 3:
            visit_dates = sorted([date.fromisoformat(v["date"]) for v in visits], reverse=True)
            intervals = [(visit_dates[i] - visit_dates[i + 1]).days for i in range(len(visit_dates) - 1)]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval >= 7:  # skip if avg interval is unrealistically short
                ratio = days_since / avg_interval
                if ratio >= 1.5:
                    status = "Lapsed" if ratio >= 2.0 else "At Risk"
                    days_overdue = int(days_since - avg_interval)
            else:
                avg_interval = None

        # Every dog should show a last groomer where the data allows it:
        # 1. Prefer the stylist on the most recent real visit, falling
        #    back through earlier real visits for one.
        # 2. If none of them ever recorded a stylist, cross-reference the
        #    order item for that same visit (matched by the visit's own
        #    pet_cid + date) — treated as confirmed.
        # 3. Only as a last resort — no stylist anywhere AND no matching
        #    order item — fall back to pet_visits.json's own embedded
        #    "salesperson" on the most recent visit, flagged as unconfirmed.
        last_groomer = ""
        last_groomer_confirmed = True
        for v in visits:
            if v.get("stylist"):
                last_groomer = v["stylist"]
                break
        if not last_groomer:
            for v in visits:
                staff = customer_visit_staff.get((v.get("_origin_cid"), v["date"]))
                if staff:
                    last_groomer = staff[0]
                    break
        if not last_groomer and visits[0].get("salesperson"):
            last_groomer = visits[0]["salesperson"]
            last_groomer_confirmed = False

        appointments_history = [
            {
                "date": v.get("date", ""),
                "service": v.get("service", "") or v.get("items_raw", ""),
                "size": v.get("size", ""),
                "groomer": v.get("stylist", ""),
            }
            for v in all_dated_visits[:25] if is_real_appointment(v)
        ]
        notes_history = [
            {
                "date": v.get("date", ""),
                # items_raw first here — for a note that got fragmented
                # across service/breed_group/size, items_raw is the joined
                # reconstruction and reads more completely than the
                # service field's fragment alone.
                "text": v.get("items_raw", "") or v.get("service", ""),
            }
            for v in all_dated_visits[:25] if not is_real_appointment(v)
        ]

        # Stable representative cid for the merged group — smallest
        # contributing pet_cid, deterministic across rebuilds.
        cid = str(min(rec.get("pet_cid", 0) for rec in group_records))
        owner_phone = next((rec.get("owner_phone", "") for rec in group_records if rec.get("owner_phone")), "")

        lapsed_dogs.append({
            "pet_cid": cid,
            "pet_name": pet_name_,
            "owner_name": owner_name_,
            "owner_phone": owner_phone,
            "last_visit": last_visit_str,
            "days_since": days_since,
            "days_overdue": days_overdue,
            "avg_interval": round(avg_interval) if avg_interval is not None else None,
            "visit_count": len(visits),
            "status": status,
            "last_groomer": last_groomer,
            "last_groomer_confirmed": last_groomer_confirmed,
            "last_service": visits[0].get("service", ""),
            "size": visits[0].get("size", ""),
            "ratio": ratio,
        })
        lapse_history[cid] = {"appointments": appointments_history, "notes": notes_history}

    lapsed_dogs.sort(key=lambda x: -x["days_since"])
    return lapsed_dogs, lapse_history
