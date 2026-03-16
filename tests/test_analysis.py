"""Tests for analysis compute functions with synthetic data."""

import pandas as pd

from analysis import compute_executive_summary, compute_service_mix, compute_dog_sizes


def _make_items_df(rows):
    """Create a DataFrame matching the structure that compute functions expect."""
    defaults = {
        "name": "Test Item", "sku": "98701", "price": 50.0, "quantity": 1.0,
        "discount": 0.0, "net_sales": 50.0, "employee": "Alice",
        "order_id": 1, "created": pd.Timestamp("2025-06-15"),
        "year": 2025, "month": 6, "day_name": "Sunday",
        "is_groom": True, "is_retail": False, "is_gift_card": False,
        "service_type": "Full Groom", "groom_category": "core",
        "retail_category": None, "dog_size": "21-40 lbs", "is_doodle": False,
    }
    records = []
    for i, row in enumerate(rows):
        r = {**defaults, "order_id": i + 1}
        r.update(row)
        records.append(r)
    return pd.DataFrame(records)


def _make_orders_df(n=5):
    """Create a minimal orders DataFrame."""
    return pd.DataFrame({
        "order_id": range(1, n + 1),
        "created": pd.date_range("2025-06-01", periods=n),
        "total": [100.0] * n,
        "year": [2025] * n,
        "month": [6] * n,
    })


class TestComputeExecutiveSummary:
    def test_basic_output(self):
        df = _make_items_df([
            {"is_groom": True, "is_retail": False, "net_sales": 80, "price": 80},
            {"is_groom": True, "is_retail": False, "net_sales": 60, "price": 60},
            {"is_groom": False, "is_retail": True, "net_sales": 20, "price": 20, "retail_category": "Toys"},
        ])
        orders = _make_orders_df(3)
        result = compute_executive_summary(df, orders)
        assert "total_net_sales" in result
        assert result["total_net_sales"] == 160.0
        assert result["grooming_revenue"] == 140.0
        assert result["retail_revenue"] == 20.0

    def test_zero_data(self):
        # Need at least the column schema even with zero rows
        df = _make_items_df([{}]).iloc[:0]
        orders = _make_orders_df(1).iloc[:0]
        result = compute_executive_summary(df, orders)
        assert result["total_net_sales"] == 0


class TestComputeServiceMix:
    def test_service_types_counted(self):
        df = _make_items_df([
            {"service_type": "Full Groom", "groom_category": "core", "is_groom": True, "net_sales": 80},
            {"service_type": "Full Groom", "groom_category": "core", "is_groom": True, "net_sales": 90},
            {"service_type": "Mini Groom", "groom_category": "core", "is_groom": True, "net_sales": 50},
            {"service_type": "Teeth Brushing", "groom_category": "addon", "is_groom": True, "net_sales": 10},
        ])
        result = compute_service_mix(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3
        assert "service_type" in result.columns


class TestComputeDogSizes:
    def test_size_distribution(self):
        df = _make_items_df([
            {"dog_size": "0-20 lbs", "groom_category": "core", "is_groom": True, "net_sales": 50},
            {"dog_size": "0-20 lbs", "groom_category": "core", "is_groom": True, "net_sales": 50},
            {"dog_size": "21-40 lbs", "groom_category": "core", "is_groom": True, "net_sales": 60},
            {"dog_size": "41-75 lbs", "groom_category": "core", "is_groom": True, "net_sales": 80},
        ])
        all_sizes, std_sizes, dood_sizes = compute_dog_sizes(df)
        assert isinstance(all_sizes, pd.DataFrame)
        assert len(all_sizes) == 3  # 3 distinct sizes
