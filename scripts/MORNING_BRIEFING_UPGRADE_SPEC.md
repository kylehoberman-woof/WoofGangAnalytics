# Morning Briefing Upgrade — Implementation Spec

**Target file:** `scripts/build_morning_briefing.py`
**Goal:** Make the morning briefing surface cause attribution like a real chief of staff would — distinguishing calendar/holiday effects from operational performance, surfacing add-on revenue trends, and tracking strategic indicators (lapse pool, visit cadence).

**How to use this spec:** Paste this entire file as your first message to a fresh Claude Code session in the `~/Desktop/store-analysis/` directory. The session will have full context to implement all 6 changes.

---

## Change 1 — Holiday calendar awareness

**Where:** Add new module-level dict near the top of `build_morning_briefing.py` (after imports, before `_BATHERS_BY_STORE`).

```python
# ─── Holiday & event calendar ─────────────────────────────────────────────
# Days that materially affect grooming bookings (US, NY metro).
# Format: (month, day) → label, OR a callable for moving dates.
# Effects: most holidays DEPRESS bookings; few exceptions (Mother's Day boosts
# the Fri/Sat BEFORE the holiday as customers groom for family photos).

import calendar

def _nth_weekday(year, month, weekday, n):
    """Return date of nth weekday in month. weekday: 0=Mon, 6=Sun. n=1..5 or -1 for last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return date(year, month, 1 + offset + (n-1)*7)
    else:
        last_day = calendar.monthrange(year, month)[1]
        last = date(year, month, last_day)
        offset = (last.weekday() - weekday) % 7
        return date(year, month, last_day - offset)

def get_holidays_for_year(year):
    """Return dict: date → (label, effect_direction, effect_magnitude_pct).
    effect_direction: 'depress' | 'boost'
    effect_magnitude_pct: rough expected revenue impact on that day vs typical for that DOW
    """
    h = {}
    h[date(year, 1, 1)] = ("New Year's Day", "depress", -60)
    h[_nth_weekday(year, 1, 0, 3)] = ("MLK Day", "depress", -15)
    h[_nth_weekday(year, 2, 0, 3)] = ("Presidents Day", "depress", -15)
    h[_nth_weekday(year, 5, 6, 2)] = ("Mother's Day", "depress", -25)   # 2nd Sunday in May
    h[_nth_weekday(year, 5, 0, -1)] = ("Memorial Day", "depress", -30)  # last Monday in May
    h[date(year, 6, 19)] = ("Juneteenth", "depress", -10)
    h[_nth_weekday(year, 6, 6, 3)] = ("Father's Day", "depress", -15)
    h[date(year, 7, 4)] = ("Independence Day", "depress", -50)
    h[_nth_weekday(year, 9, 0, 1)] = ("Labor Day", "depress", -30)
    h[_nth_weekday(year, 10, 0, 2)] = ("Columbus Day", "depress", -10)
    h[date(year, 10, 31)] = ("Halloween", "depress", -10)
    h[_nth_weekday(year, 11, 3, 4)] = ("Thanksgiving", "depress", -85)
    # Black Friday (day after Thanksgiving)
    thx = _nth_weekday(year, 11, 3, 4)
    h[thx + timedelta(days=1)] = ("Black Friday", "boost", +20)
    h[date(year, 12, 24)] = ("Christmas Eve", "depress", -40)
    h[date(year, 12, 25)] = ("Christmas Day", "depress", -95)
    h[date(year, 12, 31)] = ("New Year's Eve", "depress", -25)
    # Pre-holiday boost windows (groom before family arrives)
    # Wed/Thu before Thanksgiving usually +20%
    h[thx - timedelta(days=1)] = ("Day before Thanksgiving", "boost", +25)
    h[thx - timedelta(days=2)] = ("Two days before Thanksgiving", "boost", +15)
    return h

def holiday_context(target_iso, window_days=3):
    """Return a human-readable context string about nearby holidays, or None.
    Looks ±window_days from target date."""
    try:
        target = date.fromisoformat(target_iso)
    except (ValueError, TypeError):
        return None
    hol = get_holidays_for_year(target.year)
    # Also pull adjacent year holidays if near year boundary
    if target.month == 1:
        hol.update({d: v for d, v in get_holidays_for_year(target.year - 1).items()
                    if d.month == 12})
    if target.month == 12:
        hol.update({d: v for d, v in get_holidays_for_year(target.year + 1).items()
                    if d.month == 1})

    for d, (label, direction, magnitude) in hol.items():
        diff = (d - target).days
        if abs(diff) <= window_days:
            if diff == 0:
                when = f"is today ({target.strftime('%A %b %d')})"
            elif diff == 1:
                when = "is tomorrow"
            elif diff == -1:
                when = "was yesterday"
            elif diff > 0:
                when = f"is in {diff} days ({d.strftime('%A %b %d')})"
            else:
                when = f"was {abs(diff)} days ago ({d.strftime('%A %b %d')})"
            return {
                "label": label,
                "when_text": when,
                "direction": direction,
                "magnitude_pct": magnitude,
                "diff_days": diff,
            }
    return None
```

---

## Change 2 — Split add-on revenue from groom revenue

**Where:** Replace the existing `compute_day_stats` (lines ~270-320) with this version. Same signature, additional return keys.

```python
def compute_day_stats(items, target_date_iso):
    """Aggregate revenue/appointments for a single day.

    Returns same fields as before plus:
        core_groom_rev, addon_rev, addon_attach_rate, addon_avg_value
    """
    order_ids_groom = set()
    order_ids_bath = set()
    order_ids_all = set()
    orders_with_addon = set()
    total_rev = 0.0
    groom_rev = 0.0
    core_groom_rev = 0.0
    addon_rev = 0.0
    addon_count = 0
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
            cat = cls.get("groom_category")
            if cat == "core":
                core_groom_rev += price
                svc = (cls.get("service_type") or "").lower()
                if "bath" in svc:
                    if oid:
                        order_ids_bath.add(oid)
                else:
                    if oid:
                        order_ids_groom.add(oid)
            elif cat in ("addon", "spa"):
                addon_rev += price
                addon_count += 1
                if oid:
                    orders_with_addon.add(oid)
        elif cls.get("is_retail"):
            retail_rev += price

    txn_count = len(order_ids_all)
    core_appt_count = len(order_ids_groom) + len(order_ids_bath)
    attach_rate = (len(orders_with_addon) / core_appt_count * 100) if core_appt_count else 0
    avg_addon = (addon_rev / addon_count) if addon_count else 0

    return {
        "total_rev": round(total_rev, 2),
        "groom_rev": round(groom_rev, 2),
        "core_groom_rev": round(core_groom_rev, 2),
        "addon_rev": round(addon_rev, 2),
        "addon_count": addon_count,
        "addon_attach_rate": round(attach_rate, 1),
        "addon_avg_value": round(avg_addon, 2),
        "retail_rev": round(retail_rev, 2),
        "grooms": len(order_ids_groom),
        "baths": len(order_ids_bath),
        "appointments": core_appt_count,
        "transactions": txn_count,
        "avg_ticket": round(total_rev / txn_count, 2) if txn_count else 0,
    }
```

Apply the **same change** to `compute_range_stats` — add `core_groom_rev`, `addon_rev`, `addon_count`, `addon_attach_rate`, `addon_avg_value` to its return dict using the same logic.

---

## Change 3 — DOW-mix normalization

**Where:** Add a new helper function before `build_store_briefing`.

```python
def compute_dow_adjusted_expectation(items, start_iso, end_iso, weeks_back=8):
    """Given a date range, compute what revenue SHOULD have been based on the
    trailing N-week average revenue for each DOW in the range.

    Returns: {
        'expected_total_rev': float,
        'expected_appointments': int,
        'dow_baseline': {0: rev/day, 1: rev/day, ..., 6: rev/day},
        'dow_counts_in_range': {0: 2, 1: 1, ...},
    }
    """
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)

    # Build trailing N-week DOW averages, ending the day before start
    baseline_end = start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=weeks_back * 7 - 1)

    by_dow = defaultdict(lambda: {"rev": 0.0, "appts": 0, "days": set()})
    for item in items:
        created_str = (item.get("CreatedOn") or "")[:10]
        if not created_str:
            continue
        try:
            created = date.fromisoformat(created_str)
        except ValueError:
            continue
        if created < baseline_start or created > baseline_end:
            continue
        try:
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            price = 0.0
        dow = created.weekday()
        by_dow[dow]["rev"] += price
        by_dow[dow]["days"].add(created)
        cls = classify_item(item.get("Name") or "", item.get("Sku") or "")
        if cls.get("is_groom") and cls.get("groom_category") == "core":
            by_dow[dow]["appts"] += 1

    dow_rev = {dow: (v["rev"] / len(v["days"])) if v["days"] else 0.0
               for dow, v in by_dow.items()}
    dow_appts = {dow: (v["appts"] / len(v["days"])) if v["days"] else 0.0
                 for dow, v in by_dow.items()}

    # Count DOW occurrences in the target range
    dow_counts = defaultdict(int)
    cur = start
    while cur <= end:
        dow_counts[cur.weekday()] += 1
        cur += timedelta(days=1)

    expected_rev = sum(dow_rev.get(dow, 0) * cnt for dow, cnt in dow_counts.items())
    expected_appts = sum(dow_appts.get(dow, 0) * cnt for dow, cnt in dow_counts.items())

    return {
        "expected_total_rev": round(expected_rev, 2),
        "expected_appointments": round(expected_appts, 0),
        "dow_baseline": dow_rev,
        "dow_counts_in_range": dict(dow_counts),
    }
```

**Then in `build_store_briefing`**, after the MTD computation, add:

```python
# DOW-adjusted MTD expectation — what should MTD revenue be given the
# specific DOW mix of days that have elapsed?
mtd_start_iso = mtd_start.isoformat()
yesterday_iso = yesterday.isoformat()
dow_adj = compute_dow_adjusted_expectation(items, mtd_start_iso, yesterday_iso)
briefing["mtd_dow_adjusted_expected"] = dow_adj["expected_total_rev"]

# Also compute for prev MTD comparison window so we can do apples-to-apples
prev_mtd_dow_adj = compute_dow_adjusted_expectation(
    items, prev_mtd_start.isoformat(), prev_mtd_end.isoformat()
)
briefing["prev_mtd_dow_adjusted_expected"] = prev_mtd_dow_adj["expected_total_rev"]
```

(Adjust variable names to match what's currently in the function — should be obvious from context.)

---

## Change 4 — Visit cadence drift (monthly, cached)

**Where:** New function in `build_morning_briefing.py`. Run only once per month (check if current month's distribution already cached).

```python
def compute_visit_cadence_distribution(items, lookback_days=90):
    """For recurring grooming customers, compute distribution of days between
    consecutive visits during the last lookback_days window.

    Returns: {
        'window_days': int,
        'total_gaps': int,
        'mean_gap': float,
        'median_gap': float,
        'distribution': {
            '2-3wk': pct, '3-4wk': pct, '4-6wk': pct,
            '6-8wk': pct, '8-12wk': pct, '12wk+': pct,
        }
    }
    """
    from statistics import mean, median

    today = date.today()
    window_start = today - timedelta(days=lookback_days)

    customer_visits = defaultdict(set)
    for item in items:
        cls = classify_item(item.get("Name") or "", item.get("Sku") or "")
        if not (cls.get("is_groom") and cls.get("groom_category") == "core"):
            continue
        cid = item.get("CustomerId")
        if not cid:
            continue
        try:
            dt = date.fromisoformat((item.get("CreatedOn") or "")[:10])
        except (ValueError, TypeError):
            continue
        customer_visits[cid].add(dt)

    gaps = []
    for visits in customer_visits.values():
        sorted_v = sorted(visits)
        for i in range(1, len(sorted_v)):
            if not (window_start <= sorted_v[i] <= today):
                continue
            gap_days = (sorted_v[i] - sorted_v[i-1]).days
            if 14 <= gap_days <= 180:
                gaps.append(gap_days)

    if not gaps:
        return None

    def bucket_of(g):
        if g <= 21: return "2-3wk"
        if g <= 28: return "3-4wk"
        if g <= 42: return "4-6wk"
        if g <= 56: return "6-8wk"
        if g <= 84: return "8-12wk"
        return "12wk+"

    counts = defaultdict(int)
    for g in gaps:
        counts[bucket_of(g)] += 1
    total = len(gaps)
    dist = {b: round(counts.get(b, 0) / total * 100, 1)
            for b in ["2-3wk", "3-4wk", "4-6wk", "6-8wk", "8-12wk", "12wk+"]}

    return {
        "window_days": lookback_days,
        "total_gaps": total,
        "mean_gap": round(mean(gaps), 1),
        "median_gap": round(median(gaps), 1),
        "distribution": dist,
    }
```

**Add to `build_store_briefing`** — only run on the 1st of each month (or if the cached version doesn't exist for current month):

```python
# Visit cadence — recompute monthly (cached to data dir)
cadence_cache = data_dir / f"cadence_{today.strftime('%Y-%m')}.json"
if not cadence_cache.exists():
    cadence = compute_visit_cadence_distribution(items, lookback_days=90)
    if cadence:
        with open(cadence_cache, "w") as f:
            json.dump(cadence, f)
        # Also load prior month for delta comparison
        prior_month = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        prior_cache = data_dir / f"cadence_{prior_month}.json"
        if prior_cache.exists():
            with open(prior_cache) as f:
                cadence["prior_month"] = json.load(f)
else:
    with open(cadence_cache) as f:
        cadence = json.load(f)
briefing["cadence"] = cadence
```

---

## Change 5 — Lapse pool KPI (monthly, cached)

**Where:** New function in `build_morning_briefing.py`.

```python
def compute_lapse_pool(items):
    """Compute the recoverable lapse pool: customers who haven't visited in
    60-365 days, with at least 2 prior grooming visits.

    Returns: {
        'drifting': {'count': int, 'annual_value': float},
        'lapsed': {'count': int, 'annual_value': float},
        'dormant': {'count': int, 'annual_value': float},
        'total_pool': int,
        'total_annual_value': float,
    }
    """
    today = date.today()
    cust = defaultdict(lambda: {"visits": [], "revenue": 0.0})

    for item in items:
        cls = classify_item(item.get("Name") or "", item.get("Sku") or "")
        if not (cls.get("is_groom") and cls.get("groom_category") == "core"):
            continue
        cid = item.get("CustomerId")
        if not cid:
            continue
        try:
            dt = date.fromisoformat((item.get("CreatedOn") or "")[:10])
            price = float(item.get("Price") or 0) * float(item.get("Quantity") or 1)
            price -= float(item.get("Discount") or 0)
        except (ValueError, TypeError):
            continue
        cust[cid]["visits"].append(dt)
        cust[cid]["revenue"] += price

    buckets = {
        "drifting": {"count": 0, "annual_value": 0.0},  # 60-89d
        "lapsed":   {"count": 0, "annual_value": 0.0},  # 90-179d
        "dormant":  {"count": 0, "annual_value": 0.0},  # 180-365d
    }
    for d in cust.values():
        if len(d["visits"]) < 2:
            continue
        last = max(d["visits"])
        first = min(d["visits"])
        days_since = (today - last).days
        span_years = max((last - first).days / 365.0, 0.25)
        annual_val = d["revenue"] / span_years
        if 60 <= days_since < 90:
            buckets["drifting"]["count"] += 1
            buckets["drifting"]["annual_value"] += annual_val
        elif 90 <= days_since < 180:
            buckets["lapsed"]["count"] += 1
            buckets["lapsed"]["annual_value"] += annual_val
        elif 180 <= days_since < 365:
            buckets["dormant"]["count"] += 1
            buckets["dormant"]["annual_value"] += annual_val

    for k in buckets:
        buckets[k]["annual_value"] = round(buckets[k]["annual_value"], 0)
    total_pool = sum(b["count"] for b in buckets.values())
    total_val = sum(b["annual_value"] for b in buckets.values())
    return {
        **buckets,
        "total_pool": total_pool,
        "total_annual_value": total_val,
    }
```

**Cache same way as cadence:** only compute on 1st of month or if missing for current month. Store at `data_dir / f"lapse_{today.strftime('%Y-%m')}.json"`. Load prior month from cache if available for `delta` comparison. Attach to `briefing["lapse_pool"]`.

---

## Change 6 — Update AI prompt & data brief

**Where:** Inside `generate_executive_summary`. Three sub-changes:

### 6a. Add to the data brief (per-store section):

```python
# Add-on revenue tracking (after the existing yesterday lines)
lines.append(f"  - Core grooms revenue: ${y.get('core_groom_rev',0):,.0f} (vs same day last wk: ${prev.get('core_groom_rev',0):,.0f})")
lines.append(f"  - Add-on revenue: ${y.get('addon_rev',0):,.0f} (vs ${prev.get('addon_rev',0):,.0f}); attach {y.get('addon_attach_rate',0):.0f}% (vs {prev.get('addon_attach_rate',0):.0f}%)")

# DOW-adjusted MTD comparison
if briefing.get("mtd_dow_adjusted_expected"):
    actual = store["mtd"]["total_rev"]
    expected = briefing["mtd_dow_adjusted_expected"]
    diff_pct = (actual - expected) / expected * 100 if expected else 0
    lines.append(f"  - MTD vs DOW-adjusted expectation: actual ${actual:,.0f} vs expected ${expected:,.0f} ({diff_pct:+.1f}% — this isolates real performance from calendar mix)")

# Cadence drift signal (monthly)
cad = store.get("cadence")
if cad and cad.get("prior_month"):
    cur_long = cad["distribution"].get("12wk+", 0)
    prev_long = cad["prior_month"]["distribution"].get("12wk+", 0)
    delta_long = cur_long - prev_long
    if abs(delta_long) >= 1.5:  # only surface meaningful shifts
        direction = "growing" if delta_long > 0 else "shrinking"
        lines.append(f"  - Visit-cadence drift: 12+ wk bucket {direction} ({prev_long:.1f}% → {cur_long:.1f}%, {delta_long:+.1f} pp) — leading indicator of {'stealth churn' if delta_long > 0 else 'recovery'}")

# Lapse pool (monthly)
lp = store.get("lapse_pool")
if lp:
    lines.append(f"  - Lapse pool: {lp['total_pool']} customers ({lp['drifting']['count']} drifting / {lp['lapsed']['count']} lapsed / {lp['dormant']['count']} dormant), combined annual value ${lp['total_annual_value']:,.0f}")
```

### 6b. Add holiday context line at top of each store section:

```python
# Near-term holiday flag
for d_iso in [output["yesterday"], output["today"], output.get("tomorrow", output["today"])]:
    hc = holiday_context(d_iso, window_days=2)
    if hc:
        lines.append(f"  - Holiday note: {hc['label']} {hc['when_text']} — typically {hc['direction']}es revenue ~{abs(hc['magnitude_pct'])}% vs typical {datetime.fromisoformat(d_iso).strftime('%A')}")
        break
```

### 6c. Update the AI prompt itself (replace the existing `prompt = f"""..."""`):

```python
prompt = f"""You are the Chief of Staff to Kyle, CEO and President of Woof Gang Bakery & Grooming — a 2-location pet grooming and retail business (Port Washington #264 and Hicksville #265 in Long Island, NY).

Write his morning briefing. Model your style on the way a trusted chief of staff would brief Jeff Bezos: direct, specific, strategic, efficient. No fluff. Lead with what matters most. Highlight one thing he should be proud of and one thing he should pay attention to. If you notice a pattern across both stores, name it. If something is concerning, say so plainly. If something is going well, say so.

CRITICAL FRAMING — capacity is the master driver: The number of groomers working each day is THE driver of revenue at this business. Always interpret revenue numbers through the lens of capacity. If yesterday's revenue was down but only 2 groomers worked vs the typical 4, that is a capacity issue, not a performance issue. If revenue per active groomer is above the trailing-30d baseline, the team is running hot regardless of total dollars. Use $/groomer-day as your primary productivity yardstick.

CAUSE ATTRIBUTION — the second most important skill: When revenue is up or down, identify WHY. Kyle does not want raw deltas — he wants attribution. Specifically:
  1. If MTD is behind prior month, ALWAYS compare actual MTD to DOW-adjusted expectation (provided in the data). If the gap is calendar-driven (DOW mix unfavorable, holiday landed differently), say so and quantify it. If it's real performance loss, say that plainly.
  2. If a holiday occurred in the last 3 days or is coming in the next 3 days, factor it into your commentary. Mother's Day, Memorial Day, July 4th, Thanksgiving, and Christmas all materially affect bookings — don't sound an alarm about a Sunday dip that was Mother's Day.
  3. If ONE line item moved disproportionately to the rest (e.g., add-on revenue down 21% while core grooms down 8%), flag it as a specific operational signal, not a generic miss. Add-on revenue divergence = upsell discipline. Retail divergence = front-of-house focus. Etc.
  4. Distinguish "structural" from "operational" causes. Calendar/holiday/DOW mix are structural — don't blame the team. Capacity drops, upsell drops, retail miss are operational — name the area.

LEADING INDICATORS — surface these when they move:
  - Visit-cadence drift: when the 12+ week visit-gap bucket grows, customers are stretching out (stealth churn). When it shrinks, retention is improving.
  - Lapse pool: customers 60-365 days dormant who could be reactivated. Frame this as opportunity, not loss.

End the briefing with a single explicit "Capacity & coverage" sentence (just one sentence) calling out today's and tomorrow's scheduled groomer count and bather hours, plus next 7 days groomer-days. This sentence is required even on slow news days.

Write 3-4 short paragraphs, prose only — no bullet points, no headers, no markdown. Keep it under 240 words total. Address him by name (Kyle) once at the start.

Here is the data:

{data_brief}

Write the briefing now."""
```

---

## Validation steps

After implementing, run locally:

```bash
cd ~/Desktop/store-analysis
python3 scripts/build_morning_briefing.py
```

Confirm:
1. ✅ `data/morning_briefing.json` exists and has new fields: `core_groom_rev`, `addon_rev`, `addon_attach_rate`, `mtd_dow_adjusted_expected`, `prev_mtd_dow_adjusted_expected`, `cadence` (if first of month), `lapse_pool` (if first of month)
2. ✅ Cache files written: `port-washington/data/cadence_2026-05.json`, `port-washington/data/lapse_2026-05.json` (and Hicksville)
3. ✅ AI summary mentions Mother's Day if running soon after May 10
4. ✅ AI summary mentions DOW-adjusted MTD when there's a meaningful gap
5. ✅ Add-on revenue line appears in summary when significant (>10% movement)
6. ✅ No regression: `total_rev`, `groom_rev`, `retail_rev`, `appointments` numbers match what `morning.html` was showing before

If any of the new fields are missing or zero unexpectedly, check:
- Classifier (`classifier.py`) returns `"addon"` or `"spa"` for SPA/add-on items
- Holiday dates resolve correctly (run `holiday_context("2026-05-10")` — should return Mother's Day)
- DOW baseline window has at least 4 weeks of data (otherwise expectation will be noisy)

---

## Optional polish (skip if pressed for time)

- **Holiday-aware DOW baseline:** exclude prior holidays from the trailing baseline so they don't distort the expectation
- **Add-on attach rate trend line:** track attach rate over rolling 4 weeks to spot a discipline slide before it shows in revenue
- **Front-end:** update `morning.html` to display the new fields (DOW-adjusted MTD diff, lapse pool, cadence drift). Not strictly necessary — the AI prose summary will surface them.

---

## File touch summary

Files this session will modify:
- `scripts/build_morning_briefing.py` — all 6 changes above

Files this session will create:
- `port-washington/data/cadence_YYYY-MM.json` (cache, monthly)
- `port-washington/data/lapse_YYYY-MM.json` (cache, monthly)
- `hicksville/data/cadence_YYYY-MM.json`
- `hicksville/data/lapse_YYYY-MM.json`

No changes to: `config.py`, `classifier.py`, `morning.html`, or any other script.
