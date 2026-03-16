"""
Shared formatting helpers used across dashboards, Excel reports, and analysis scripts.
"""

import math

import pandas as pd


# ─── Excel formatters (return empty string for NaN) ─────────────────────────

def fmt_currency(val):
    if pd.isna(val):
        return ""
    return f"${val:,.2f}"


def fmt_pct(val):
    if pd.isna(val):
        return ""
    return f"{val:.1f}%"


def fmt_int(val):
    if pd.isna(val):
        return ""
    return f"{int(val):,}"


# ─── HTML/Dashboard formatters (return "0" for NaN) ─────────────────────────

def fc(v):
    """Format currency for HTML output."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "$0.00"
    return f"${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def fp(v):
    """Format percentage for HTML output."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "0.0%"
    return f"{v:.1f}%"


def fi(v):
    """Format integer for HTML output."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "0"
    return f"{int(v):,}"


# ─── JS / numpy helpers ─────────────────────────────────────────────────────

def to_py(val):
    """Convert numpy/pandas scalars to plain Python types for JSON-safe JS output."""
    if hasattr(val, 'item'):
        val = val.item()
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return 0
    return val


def jslist(lst):
    """Convert a list of possibly-numpy values to a JSON-safe Python list string."""
    return str([round(to_py(v), 2) for v in lst])


def esc(s):
    """HTML-escape a string."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))
