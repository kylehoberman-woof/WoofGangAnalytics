"""
Centralized configuration for Woof Gang Store Performance Analysis.
All store configs, business constants, and brand colors in one place.
"""

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

# ─── Store Configuration ────────────────────────────────────────────────────

@dataclass
class StoreConfig:
    name: str
    location_id: int
    token: str
    start_date: str
    end_date: str
    data_dir: Path
    output_dir: Path
    short_name: str = ""

    def __post_init__(self):
        if not self.short_name:
            self.short_name = self.name.split("--")[-1].strip().split("(")[0].strip().replace(", ", "_").replace(" ", "")


STORES = {
    "port-washington": StoreConfig(
        name="Woof Gang Bakery & Grooming -- Port Washington, NY (#264)",
        location_id=203698,
        token="FAF11AD5771F74C3ABF5D6FB2965A21BF4F9A93B5FB3ED2AE7DF66B53C0AE23B058586C983CE6B53660530838748D490D6E10291F158DF791B1BA278FEDAA844",
        start_date="2024-09-26",
        end_date="2026-12-31",
        data_dir=PROJ_ROOT / "port-washington" / "data",
        output_dir=PROJ_ROOT / "port-washington",
    ),
    "hicksville": StoreConfig(
        name="Woof Gang Bakery & Grooming -- Hicksville, NY (#265)",
        location_id=205993,
        token="E57ACC082340B7FF58B5ABA5A99BE77D9501852A6C8F4D759A64D02EAED38ABE214A4026CA9A5D962ED5A141B7C061BC9BAA609365F8FE21F2F9EEF80DE04CA8",
        start_date="2025-12-11",
        end_date="2026-12-31",
        data_dir=PROJ_ROOT / "hicksville" / "data",
        output_dir=PROJ_ROOT / "hicksville",
    ),
}


def get_store(name="port-washington"):
    """Get store config by name. Token from env var FRANPOS_TOKEN overrides stored token."""
    store = STORES[name]
    env_token = os.environ.get("FRANPOS_TOKEN")
    if env_token:
        store.token = env_token
    return store


# ─── Brand Colors (Excel hex — no # prefix) ─────────────────────────────────

PAW_MAGENTA = "C4276E"
TEDDY_BROWN = "6B3520"
LIGHT_PINK = "FDF0F5"
DARK_TEAL = "1B6B6B"
WHITE = "FFFFFF"
LIGHT_GRAY = "F5F5F5"

# HTML color palette (with # prefix)
C = {
    "magenta": "#C4276E",
    "brown": "#6B3520",
    "pink_bg": "#FDF0F5",
    "teal": "#1B6B6B",
    "white": "#FFFFFF",
    "light_gray": "#F8F9FA",
    "green": "#2E7D32",
    "red": "#C62828",
    "amber": "#F9A825",
    "chart": ["#C4276E", "#1B6B6B", "#6B3520", "#E91E63", "#26A69A", "#8D6E63",
              "#F06292", "#4DB6AC", "#A1887F", "#CE93D8", "#80CBC4", "#FFAB91"],
}


# ─── Supabase (Override Storage) ───────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://bqzinttbjeeaybywhhet.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "sb_publishable_J-0b5wB84dbLQ5sRErwEAw_1qaX5ZX-")


# ─── API Configuration ──────────────────────────────────────────────────────

BASE_URL = "https://publicapi.franpos.com"
PAGE_SIZE = 500
ET_BUFFER_DAYS = 2
INCREMENTAL_DAYS = 60


# ─── FDD Industry Standards ─────────────────────────────────────────────────

FDD_RETAIL_COGS_PCT = 48.5    # Retail cost of goods as % of retail revenue
FDD_GROOM_COGS_PCT = 51.5     # Grooming cost (commission/labor) as % of groom revenue


# ─── Commission & Payroll ───────────────────────────────────────────────────

COMMISSION_RATE = 0.50
BATHER_RATE = 17.0  # $/hr

# Guarantees: {name: (daily_rate, start_date, end_date)}
# All groomers except Kimberly get $200/day for first 90 days.
# Maria C ($200) and Sue M ($300) have permanent guarantees.
GUARANTEES = {
    "Ashley Fribbley": (200.0, "2024-09-27", "2024-12-26"),
    "Cindy Szczudlo":  (200.0, "2025-04-19", "2025-07-18"),
    "Danielle L":      (200.0, "2025-03-05", "2025-06-03"),
    "Ingrid R":        (200.0, "2025-09-23", "2025-12-22"),
    "Jackie G. ":      (200.0, "2025-05-24", "2025-08-22"),
    "Joant C":         (200.0, "2024-12-01", "2025-03-01"),
    "Joi Ockimey":     (200.0, "2024-10-01", "2024-12-30"),
    "Joyce P":         (200.0, "2024-10-21", "2025-01-19"),
    "Maria C":         (200.0, "2024-09-29", "2099-12-31"),  # permanent
    "Marie D.":        (200.0, "2025-04-15", "2025-07-14"),
    "Olivia M":        (200.0, "2025-05-27", "2025-08-25"),
    "Stacey W":        (200.0, "2024-10-02", "2024-12-31"),
    "Sue M":           (300.0, "2024-10-23", "2099-12-31"),  # permanent
    # Hicksville groomers — $200/day for first 90 days
    "Carol W":         (200.0, "2025-12-12", "2026-03-11"),
    "Maria V":         (200.0, "2025-12-13", "2026-03-12"),
    "Sereena C":       (200.0, "2025-12-15", "2026-03-14"),
    "Alex  I":         (200.0, "2026-01-19", "2026-04-18"),
    "Julia B":         (200.0, "2026-02-03", "2026-05-04"),
}

RETAIL_RATES = {
    "Chris": 20.0,      # $/hr
    "Casey": 19.0,      # $/hr
    "Sitara": 19.0,     # $/hr (former)
    "Ali": 19.0,        # $/hr (former)
    "Giana": 19.0,      # $/hr (former)
    "Parker": 19.0,     # $/hr (former)
}

RETAIL_NAME_MAP = {
    "Christine Brower": "Chris",
    "Casey Makowski": "Casey",
    "Sitara Nagrani": "Sitara",
    "Alize James": "Ali",
    "Giana Golden": "Giana",
    "Parker Spooner": "Parker",
}

BATHER_NAME_MAP = {
    "Jessica G": "Jessica G",
    "Angela R": "Angela R",
    "Isabelle O": "Isabelle O",
    "Brian Labianca": "Brian L",
}

EXCLUDE_EMPLOYEES = {
    "Unknown", "Wgb Port Washington", "Wgb Hicksville", "Kyle Hoberman", "Julie Schorr",
    "Cindy Szczudlo",                             # manager (paid from PW only)
    "Jessica G", "Angela R",                      # PW current bathers
    "Isabelle O", "Brian Labianca",               # PW former bathers
    "Hailey Imhof",                               # HV retail associate
    "Nicole Alarcon", "Naomi Dutes",              # HV sales associates
    "Sophia Kurkowski", "Christina Ramkissoon",   # HV sales associates
    "Sitara Nagrani", "Alize James",              # PW former sales associates
    "Giana Golden", "Parker Spooner",             # PW former sales associates
    "Casey Makowski", "Christine Brower",         # PW current sales associates
}


# ─── Manager Configuration ──────────────────────────────────────────────────

MANAGER_NAME = "Cindy Szczudlo"
MANAGER_SALARY_OLD = 65000.0   # Mar 1 2025 – Feb 28 2026
MANAGER_SALARY_NEW = 67000.0   # Mar 1 2026 onward
MANAGER_RAISE_DATE = date(2026, 3, 1)
MANAGER_BONUS_DATE = date(2026, 3, 1)
MANAGER_BONUS = 2000.0
MANAGER_START = date(2025, 3, 1)


# ─── Financial Constants ────────────────────────────────────────────────────

MONTHLY_RENT = 7700.0  # Port Washington default
ROYALTY_RATE = 0.07     # Legacy flat rate (use get_royalty_rate() for accurate tiered rate)


def get_royalty_rate(store_name, month_date):
    """Get combined royalty + marketing fee rate for a given month.

    Per multi-store franchise agreement:
    Royalty: 5% (months 1-12), 6% (months 13-24), 7% (month 25+)
    Marketing: 1% (months 1-24), 2% (month 25+)
    Combined: 6% (months 1-12), 7% (months 13-24), 9% (month 25+)

    month_date: date or first day of the month to calculate for
    Returns: float (e.g., 0.06 for 6%)
    """
    open_date = STORE_OPEN_DATES.get(store_name, date(2024, 9, 26))
    # Count complete calendar months since opening
    # Opening month = month 0, first complete month after = month 1
    months_since = (month_date.year - open_date.year) * 12 + (month_date.month - open_date.month)
    if months_since < 0:
        months_since = 0

    # Royalty tiers (opening month + 12 complete months = months 0-12)
    if months_since <= 12:
        royalty = 0.05
    elif months_since <= 24:
        royalty = 0.06
    else:
        royalty = 0.07

    # Marketing tiers (opening month + 24 complete months = months 0-24)
    if months_since <= 24:
        marketing = 0.01
    else:
        marketing = 0.02

    return royalty + marketing

# Store-specific financial constants
STORE_RENT = {
    "port-washington": 7724.0,   # Legacy flat (use get_monthly_rent for accurate tiered)
    "hicksville": 6056.07,
}

# Rent schedules: list of (start_date, monthly_rent) — sorted chronologically
STORE_RENT_SCHEDULE = {
    "port-washington": [
        (date(2024, 9, 27), 7560.00),   # Year 1
        (date(2025, 9, 27), 7724.00),   # Year 2
    ],
    "hicksville": [
        (date(2025, 12, 11), 6056.07),  # Year 1
    ],
}


def get_monthly_rent(store_name, month_date):
    """Get monthly rent for a given month based on the lease schedule.

    Uses the rent rate in effect at the start of the given month.
    Returns the monthly rent amount.
    """
    schedule = STORE_RENT_SCHEDULE.get(store_name, [])
    if not schedule:
        return STORE_RENT.get(store_name, 7700.0)

    # Find the most recent rate that's <= month_date
    rent = schedule[0][1]  # default to first rate
    for start, rate in schedule:
        if month_date >= start:
            rent = rate
        else:
            break
    return rent

STORE_OPEN_DATES = {
    "port-washington": date(2024, 9, 26),
    "hicksville": date(2025, 12, 11),
}

# Hicksville retail staff
HICKSVILLE_RETAIL_RATES = {
    "Hailey": 21.0,     # $/hr — current
    "Chris": 20.0,      # $/hr — shared with PW (partial hours)
    "Sophia": 19.0,     # $/hr — fired
    "Naomi": 19.0,      # $/hr — fired
    "Nicole": 19.0,     # $/hr — fired
    "Christina": 19.0,  # $/hr — fired
}

HICKSVILLE_RETAIL_NAME_MAP = {
    "Hailey Imhof": "Hailey",
    "Christine Brower": "Chris",
    "Sophia Kurkowski": "Sophia",
    "Naomi Dutes": "Naomi",
    "Nicole Alarcon": "Nicole",
    "Christina Ramkissoon": "Christina",
}

HICKSVILLE_BATHER_NAME_MAP = {}  # Hicksville has no bathers

# ─── Pay Period Configuration ─────────────────────────────────────────────

ANCHOR_START = date(2026, 2, 23)   # PW: Monday anchor (bi-weekly Mon–Sun)
STORE_OPEN = date(2024, 9, 26)

# Store-specific pay period settings
PAY_PERIOD_CONFIG = {
    "port-washington": {
        "length_days": 14,           # bi-weekly
        "anchor": date(2026, 2, 23), # Monday
    },
    "hicksville": {
        "length_days": 7,            # weekly
        "anchor": date(2026, 3, 28), # Saturday (Sat–Fri periods)
    },
}


# ─── Known Closures ─────────────────────────────────────────────────────────

KNOWN_CLOSURES = {
    "2024-09-26", "2024-11-28", "2024-12-25", "2025-01-01", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25", "2026-01-01", "2025-12-27",
    "2026-01-25", "2026-01-26", "2026-02-23",
}


# ─── Service / Inventory Classification ─────────────────────────────────────

SERVICE_PREFIXES = ("987", "765", "543", "432", "986", "985", "984", "983", "982")
SERVICE_KEYWORDS = (
    "groom", "bath", "add-on", "walk-in", "spa upgrade", "nail", "brush", "teeth",
    "deshedd", "de-shed", "full groom", "mini groom", "classic bath", "lux bath",
    "miscellaneous", "gift card", "gratuity", "tip", "hypo allergenic",
)

BAKERY_SKUS = ("000", "001", "TREAT01", "TREAT02",
               "1001", "1002", "1003", "1004", "1005", "1006",
               "1007", "1008", "1009", "1010")


# ─── Untracked Stock SKUs ───────────────────────────────────────────────────

UNTRACKED_SKUS = [
    ("TREAT02", "Farmers Market Bulk"),
    ("TREAT01", "Bakery Bay Bulk Treats"),
    ("1002", "Cookie Large"),
    ("1003", "Cookie XL"),
    ("1004", "Cookie Small"),
    ("1005", "Cookie Medium"),
    ("1006", "Cookie XS"),
    ("2001", "Farmers Market Bulk Treats"),
    ("2002", "Bakery Bulk Treats"),
    ("82101", "Preppy Puppy Birthday Cupcakes"),
    ("81909", "Preppy Puppy Blue 4\" Birthday Cake"),
    ("000", "Miscellaneous"),
]
