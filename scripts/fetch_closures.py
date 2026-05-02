"""Fetch store closure data from Supabase store_closures table.

Returns per-store sets of ISO date strings, merged with the hardcoded
KNOWN_CLOSURES from config.py as a fallback / historical seed. If
Supabase is unreachable, falls back to KNOWN_CLOSURES applied to both
stores (preserves prior behavior).
"""
import sys
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

REST_URL = f"{config.SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": config.SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
}


def fetch_closures(store=None):
    """Fetch closures for a specific store (or all stores).

    Returns a set of ISO date strings ('YYYY-MM-DD').

    On Supabase failure, returns KNOWN_CLOSURES from config.py so the
    legacy hardcoded set continues to apply.
    """
    try:
        qs = "select=store,closure_date"
        if store:
            qs += f"&store=eq.{store}"
        resp = requests.get(f"{REST_URL}/store_closures?{qs}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        rows = resp.json() if isinstance(resp.json(), list) else []
        # Merge Supabase closures with hardcoded KNOWN_CLOSURES.
        # KNOWN_CLOSURES applies to all stores (legacy behavior).
        result = set(config.KNOWN_CLOSURES)
        for r in rows:
            d = r.get("closure_date")
            if d:
                result.add(d)
        return result
    except Exception as exc:
        print(f"[fetch_closures] Supabase unavailable, using KNOWN_CLOSURES only: {exc}", file=sys.stderr)
        return set(config.KNOWN_CLOSURES)


def fetch_closures_with_reasons(store=None):
    """Same as fetch_closures but returns dict {iso_date: reason}.

    Used by morning briefing to display the reason text. On Supabase
    failure, returns KNOWN_CLOSURES dates with reason='Closed'.
    """
    try:
        qs = "select=store,closure_date,reason"
        if store:
            qs += f"&store=eq.{store}"
        resp = requests.get(f"{REST_URL}/store_closures?{qs}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        rows = resp.json() if isinstance(resp.json(), list) else []
        result = {d: "Closed" for d in config.KNOWN_CLOSURES}
        for r in rows:
            d = r.get("closure_date")
            if d:
                result[d] = r.get("reason") or "Closed"
        return result
    except Exception as exc:
        print(f"[fetch_closures_with_reasons] Supabase unavailable: {exc}", file=sys.stderr)
        return {d: "Closed" for d in config.KNOWN_CLOSURES}


if __name__ == "__main__":
    store_arg = sys.argv[1] if len(sys.argv) > 1 else None
    closures = fetch_closures(store_arg)
    print(f"Found {len(closures)} closure(s)" + (f" for {store_arg}" if store_arg else ""))
    for d in sorted(closures):
        print(f"  {d}")
