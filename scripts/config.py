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
        start_date="2025-12-01",
        end_date="2026-03-06",
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

GUARANTEES = {"Maria C": 200.0, "Sue M": 300.0}

RETAIL_RATES = {
    "Chris": 20.0,   # $/hr
    "Casey": 19.0,   # $/hr
}

RETAIL_NAME_MAP = {
    "Christine Brower": "Chris",
    "Casey Makowski": "Casey",
}

BATHER_NAME_MAP = {
    "Jessica G": "Jessica G",
    "Angela R": "Angela R",
}

EXCLUDE_EMPLOYEES = {"Unknown", "Wgb Port Washington", "Kyle Hoberman", "Jessica G", "Angela R"}


# ─── Manager Configuration ──────────────────────────────────────────────────

MANAGER_NAME = "Cindy Szczudlo"
MANAGER_SALARY_OLD = 65000.0   # Mar 1 2025 – Feb 28 2026
MANAGER_SALARY_NEW = 67000.0   # Mar 1 2026 onward
MANAGER_RAISE_DATE = date(2026, 3, 1)
MANAGER_BONUS_DATE = date(2026, 3, 1)
MANAGER_BONUS = 2000.0
MANAGER_START = date(2025, 3, 1)


# ─── Financial Constants ────────────────────────────────────────────────────

MONTHLY_RENT = 7700.0
ROYALTY_RATE = 0.07


# ─── Pay Period Anchor ──────────────────────────────────────────────────────

ANCHOR_START = date(2026, 2, 23)
STORE_OPEN = date(2024, 9, 26)


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
