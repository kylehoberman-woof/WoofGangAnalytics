"""Generate morning briefing JSON — combined stats for both stores.

Produces data/morning_briefing.json at the repo root with:
- Yesterday's revenue, appointments, transactions per store
- Same day last week comparison
- Week-to-date (Mon → yesterday) + prior week-to-date
- Low stock alerts per store (top 15 most urgent)

Usage:
    python3 scripts/build_morning_briefing.py
"""

import json, sys, os
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from config import STORES, PROJ_ROOT, UNTRACKED_SKUS
from classifier import classify_item

# Untracked SKU set (bulk treats, cookies, etc — intentionally 0 stock)
_UNTRACKED_SET = {sku for sku, _ in UNTRACKED_SKUS}

OUT_FILE = PROJ_ROOT / "data" / "morning_briefing.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def monday_of_week(d):
    """Return Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def compute_day_stats(items, target_date_iso):
    """Aggregate revenue/appointments for a single day."""
    order_ids_groom = set()
    order_ids_bath = set()
    order_ids_all = set()
    total_rev = 0.0
    groom_rev = 0.0
    retail_rev = 0.0

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if created != target_date_iso:
            continue
        oid = item.get("OrderId")
        if oid:
            order_ids_all.add(oid)

        name = item.get("Name", "") or ""
        sku = item.get("Sku", "") or ""
        try:
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            price = 0.0
        total_rev += price

        cls = classify_item(name, sku)
        if cls.get("is_groom"):
            groom_rev += price
            if cls.get("groom_category") == "core":
                svc = (cls.get("service_type") or "").lower()
                if "bath" in svc:
                    if oid:
                        order_ids_bath.add(oid)
                else:
                    if oid:
                        order_ids_groom.add(oid)
        elif cls.get("is_retail"):
            retail_rev += price

    txn_count = len(order_ids_all)
    return {
        "total_rev": round(total_rev, 2),
        "groom_rev": round(groom_rev, 2),
        "retail_rev": round(retail_rev, 2),
        "grooms": len(order_ids_groom),
        "baths": len(order_ids_bath),
        "appointments": len(order_ids_groom) + len(order_ids_bath),
        "transactions": txn_count,
        "avg_ticket": round(total_rev / txn_count, 2) if txn_count else 0,
    }


def compute_range_stats(items, start_iso, end_iso):
    """Aggregate stats over a date range (inclusive)."""
    order_ids_groom = set()
    order_ids_bath = set()
    order_ids_all = set()
    total_rev = 0.0
    groom_rev = 0.0
    retail_rev = 0.0

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if not created or created < start_iso or created > end_iso:
            continue
        oid = item.get("OrderId")
        if oid:
            order_ids_all.add(oid)

        name = item.get("Name", "") or ""
        sku = item.get("Sku", "") or ""
        try:
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            price = 0.0
        total_rev += price

        cls = classify_item(name, sku)
        if cls.get("is_groom"):
            groom_rev += price
            if cls.get("groom_category") == "core":
                svc = (cls.get("service_type") or "").lower()
                if "bath" in svc:
                    if oid:
                        order_ids_bath.add(oid)
                else:
                    if oid:
                        order_ids_groom.add(oid)
        elif cls.get("is_retail"):
            retail_rev += price

    return {
        "total_rev": round(total_rev, 2),
        "groom_rev": round(groom_rev, 2),
        "retail_rev": round(retail_rev, 2),
        "grooms": len(order_ids_groom),
        "baths": len(order_ids_bath),
        "appointments": len(order_ids_groom) + len(order_ids_bath),
        "transactions": len(order_ids_all),
    }


def compute_low_stock(items, stock_levels, sku_brands, limit=20):
    """Find SKUs that are low/out of stock. Mirrors inventory_dashboard logic."""
    # Compute monthly velocity from last 90 days
    today = date.today()
    cutoff = (today - timedelta(days=90)).isoformat()

    sku_data = defaultdict(lambda: {"name": "", "units": 0, "months": set(), "vendor": ""})

    for item in items:
        created = (item.get("CreatedOn") or "")[:10]
        if not created or created < cutoff:
            continue
        sku = str(item.get("Sku", "") or "").strip()
        if len(sku) < 3:
            continue
        # Skip untracked SKUs (bulk treats, cookies, etc. — not tracked in inventory)
        if sku in _UNTRACKED_SET:
            continue
        # Skip service SKU prefixes
        if sku.startswith(("321", "987", "765", "543", "432", "986", "985", "984", "983", "982")):
            continue
        name = item.get("Name", "") or ""
        cls = classify_item(name, sku)
        if not cls.get("is_retail"):
            continue
        try:
            qty = float(item.get("Quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        sku_data[sku]["name"] = sku_data[sku]["name"] or name
        sku_data[sku]["units"] += qty
        sku_data[sku]["months"].add(created[:7])
        brand_info = sku_brands.get(sku, {})
        if isinstance(brand_info, dict):
            sku_data[sku]["vendor"] = brand_info.get("brand", "") or ""

    alerts = []
    for sku, d in sku_data.items():
        stock = stock_levels.get(sku)
        if stock is None:
            continue
        vel_monthly = d["units"] / max(len(d["months"]), 1) if d["units"] else 0
        vel_weekly = vel_monthly / 4.33 if vel_monthly else 0
        # Skip very-low-velocity items (< 1 unit/month avg) — they don't need urgent reorder
        if vel_monthly < 1:
            continue
        wos = stock / vel_weekly if vel_weekly > 0 else None

        # Determine status
        if stock <= 0:
            status = "out"
        elif wos is not None and wos < 1:
            status = "critical"
        elif wos is not None and wos < 2:
            status = "low"
        else:
            continue  # OK — don't include

        alerts.append({
            "sku": sku,
            "name": d["name"][:48],
            "stock": round(stock, 1),
            "weeks_of_supply": round(wos, 1) if wos is not None else None,
            "velocity_monthly": round(vel_monthly, 1),
            "status": status,
            "vendor": d["vendor"] or "Other",
        })

    # Sort by: out first, critical second, low third; then by velocity desc
    status_order = {"out": 0, "critical": 1, "low": 2}
    alerts.sort(key=lambda x: (status_order.get(x["status"], 9), -x["velocity_monthly"]))
    return alerts[:limit]


def build_store_briefing(store_key, store_cfg):
    """Build briefing data for a single store."""
    print(f"\nProcessing {store_key}...")
    data_dir = store_cfg.data_dir

    # Load order items
    with open(data_dir / "all_data.json") as f:
        raw = json.load(f)
    items = raw.get("order_items", [])

    # Load stock + brand caches (may not exist)
    stock_levels = {}
    sf = data_dir / "stock_levels.json"
    if sf.exists():
        with open(sf) as f:
            stock_levels = json.load(f)
    sku_brands = {}
    bf = data_dir / "sku_brands.json"
    if bf.exists():
        with open(bf) as f:
            sku_brands = json.load(f)

    # Date ranges
    today = date.today()
    yesterday = today - timedelta(days=1)
    last_week_same_day = yesterday - timedelta(days=7)

    # Week-to-date: Monday → yesterday
    this_monday = monday_of_week(today)
    wtd_end = yesterday if yesterday >= this_monday else this_monday
    # Prior week-to-date: same number of days ending last week
    prev_monday = this_monday - timedelta(days=7)
    prev_wtd_end = prev_monday + (wtd_end - this_monday)

    # Month-to-date: 1st of this month → yesterday
    mtd_start = today.replace(day=1)
    mtd_end = yesterday if yesterday >= mtd_start else mtd_start
    days_into_month = (mtd_end - mtd_start).days  # elapsed days (0-indexed from day 1)
    # Prior month-to-date: 1st of last month → same day offset
    if mtd_start.month == 1:
        prev_mtd_start = mtd_start.replace(year=mtd_start.year - 1, month=12)
    else:
        prev_mtd_start = mtd_start.replace(month=mtd_start.month - 1)
    prev_mtd_end = prev_mtd_start + timedelta(days=days_into_month)
    # Clamp if last month was shorter (e.g. today is Mar 31, last month = Feb)
    last_day_prev_month = (mtd_start - timedelta(days=1))
    if prev_mtd_end > last_day_prev_month:
        prev_mtd_end = last_day_prev_month

    # Year-to-date: Jan 1 → yesterday
    ytd_start = today.replace(month=1, day=1)
    ytd_end = yesterday if yesterday >= ytd_start else ytd_start
    days_into_year = (ytd_end - ytd_start).days
    # Prior year-to-date: Jan 1 last year → same day offset
    prev_ytd_start = ytd_start.replace(year=ytd_start.year - 1)
    prev_ytd_end = prev_ytd_start + timedelta(days=days_into_year)

    briefing = {
        "label": "Port Washington" if store_key == "port-washington" else "Hicksville",
        "yesterday": compute_day_stats(items, yesterday.isoformat()),
        "last_week_same_day": compute_day_stats(items, last_week_same_day.isoformat()),
        "wtd": compute_range_stats(items, this_monday.isoformat(), wtd_end.isoformat()),
        "prev_wtd": compute_range_stats(items, prev_monday.isoformat(), prev_wtd_end.isoformat()),
        "mtd": compute_range_stats(items, mtd_start.isoformat(), mtd_end.isoformat()),
        "prev_mtd": compute_range_stats(items, prev_mtd_start.isoformat(), prev_mtd_end.isoformat()),
        "mtd_label": f"{mtd_start.strftime('%b %-d')} → {mtd_end.strftime('%b %-d')}",
        "prev_mtd_label": f"{prev_mtd_start.strftime('%b %-d')} → {prev_mtd_end.strftime('%b %-d')}",
        "ytd": compute_range_stats(items, ytd_start.isoformat(), ytd_end.isoformat()),
        "prev_ytd": compute_range_stats(items, prev_ytd_start.isoformat(), prev_ytd_end.isoformat()),
        "ytd_label": f"Jan 1 → {ytd_end.strftime('%b %-d, %Y')}",
        "prev_ytd_label": f"Jan 1 → {prev_ytd_end.strftime('%b %-d, %Y')}",
        "low_stock": compute_low_stock(items, stock_levels, sku_brands),
    }
    print(f"  Yesterday: ${briefing['yesterday']['total_rev']:,.0f} rev, "
          f"{briefing['yesterday']['appointments']} appts, "
          f"{briefing['yesterday']['transactions']} txns")
    print(f"  WTD: ${briefing['wtd']['total_rev']:,.0f} rev (vs prev: ${briefing['prev_wtd']['total_rev']:,.0f})")
    print(f"  MTD: ${briefing['mtd']['total_rev']:,.0f} rev (vs prev month same period: ${briefing['prev_mtd']['total_rev']:,.0f})")
    print(f"  YTD: ${briefing['ytd']['total_rev']:,.0f} rev (vs prev year same period: ${briefing['prev_ytd']['total_rev']:,.0f})")
    print(f"  Low stock: {len(briefing['low_stock'])} items flagged")
    return briefing


def _trend_line(cur, prev, label, fmt="currency"):
    """Format a one-line trend comparison."""
    if fmt == "currency":
        c = f"${cur:,.0f}"
        p = f"${prev:,.0f}"
    else:
        c = f"{cur:,.0f}"
        p = f"{prev:,.0f}"
    if prev == 0:
        return f"{label}: {c} (no comparable prior)"
    pct = (cur - prev) / prev * 100
    direction = "up" if pct > 1 else "down" if pct < -1 else "flat"
    return f"{label}: {c} vs {p} ({direction} {abs(pct):.0f}%)"


def generate_executive_summary(output):
    """Generate a Bezos-style morning memo using Claude. Returns summary text or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[AI summary] Skipped — no ANTHROPIC_API_KEY env var")
        return None

    try:
        import httpx as _httpx
    except ImportError:
        print("[AI summary] Skipped — httpx not available")
        return None

    # Build a compact data brief for the LLM
    lines = []
    today_date = datetime.fromisoformat(output["today"]).strftime("%A, %B %d, %Y")
    yesterday_label = datetime.fromisoformat(output["yesterday"]).strftime("%A")
    lines.append(f"Today: {today_date}")
    lines.append(f"Data represents {yesterday_label}'s performance (yesterday).")
    lines.append("")

    for store_key, store in output.get("stores", {}).items():
        if "error" in store:
            continue
        lines.append(f"## {store['label']}")
        y = store["yesterday"]
        prev = store["last_week_same_day"]
        wtd = store["wtd"]
        pwtd = store["prev_wtd"]
        lines.append(f"Yesterday:")
        lines.append(f"  - {_trend_line(y['total_rev'], prev['total_rev'], 'Total revenue')}")
        lines.append(f"  - {_trend_line(y['groom_rev'], prev['groom_rev'], 'Grooming revenue')}")
        lines.append(f"  - {_trend_line(y['retail_rev'], prev['retail_rev'], 'Retail revenue')}")
        lines.append(f"  - {_trend_line(y['transactions'], prev['transactions'], 'Transactions', fmt='int')}")
        lines.append(f"  - Grooms: {y['grooms']}, Baths: {y['baths']}, Avg ticket: ${y['avg_ticket']:.0f}")
        lines.append(f"Week-to-date (Mon through yesterday):")
        lines.append(f"  - {_trend_line(wtd['total_rev'], pwtd['total_rev'], 'Revenue')}")
        lines.append(f"  - {_trend_line(wtd['appointments'], pwtd['appointments'], 'Appointments', fmt='int')}")
        mtd = store.get("mtd", {})
        pmtd = store.get("prev_mtd", {})
        if mtd:
            lines.append(f"Month-to-date ({store.get('mtd_label','this month')}) vs same period last month ({store.get('prev_mtd_label','')}):")
            lines.append(f"  - {_trend_line(mtd['total_rev'], pmtd['total_rev'], 'Revenue')}")
            lines.append(f"  - {_trend_line(mtd['appointments'], pmtd['appointments'], 'Appointments', fmt='int')}")
        ytd = store.get("ytd", {})
        pytd = store.get("prev_ytd", {})
        if ytd:
            lines.append(f"Year-to-date ({store.get('ytd_label','YTD')}) vs same period last year ({store.get('prev_ytd_label','')}):")
            lines.append(f"  - {_trend_line(ytd['total_rev'], pytd['total_rev'], 'Revenue')}")
            lines.append(f"  - {_trend_line(ytd['appointments'], pytd['appointments'], 'Appointments', fmt='int')}")
        low = store.get("low_stock", [])
        out = sum(1 for x in low if x["status"] == "out")
        crit = sum(1 for x in low if x["status"] == "critical")
        lines.append(f"Inventory: {out} items out of stock, {crit} critical (< 1 wk supply)")
        # Top 3 low-stock items with highest velocity
        if low:
            top = low[:3]
            lines.append(f"Top reorder priorities: " + ", ".join(f"{a['name']} ({a['velocity_monthly']:.0f}/mo, {a['stock']} left)" for a in top))
        lines.append("")

    data_brief = "\n".join(lines)

    prompt = f"""You are the Chief of Staff to Kyle, CEO and President of Woof Gang Bakery & Grooming — a 2-location pet grooming and retail business (Port Washington #264 and Hicksville #265 in Long Island, NY).

Write his morning briefing. Model your style on the way a trusted chief of staff would brief Jeff Bezos: direct, specific, strategic, efficient. No fluff. Lead with what matters most. Highlight one thing he should be proud of and one thing he should pay attention to. If you notice a pattern across both stores, name it. If something is concerning, say so plainly. If something is going well, say so.

Write 3-4 short paragraphs, prose only — no bullet points, no headers, no markdown. Keep it under 180 words total. Address him by name (Kyle) once at the start.

Here is the data:

{data_brief}

Write the briefing now."""

    try:
        print("\n[AI summary] Calling Claude...")
        r = _httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=45,
        )
        if r.status_code != 200:
            print(f"[AI summary] API error {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        text = text.strip()
        print(f"[AI summary] Generated {len(text)} chars")
        return text
    except Exception as e:
        print(f"[AI summary] Error: {e}")
        return None


def main():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    output = {
        "generated_at": datetime.now(et).isoformat(),
        "today": date.today().isoformat(),
        "yesterday": (date.today() - timedelta(days=1)).isoformat(),
        "week_start": monday_of_week(date.today()).isoformat(),
        "stores": {},
    }

    for store_key, store_cfg in STORES.items():
        try:
            output["stores"][store_key] = build_store_briefing(store_key, store_cfg)
        except Exception as e:
            print(f"  ERROR for {store_key}: {e}")
            output["stores"][store_key] = {"error": str(e)}

    # Generate executive summary via Claude
    output["summary_text"] = generate_executive_summary(output)

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWritten: {OUT_FILE}")


if __name__ == "__main__":
    main()
