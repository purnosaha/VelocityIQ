"""Tests for GET /reports/* endpoints.

Each test uses the module-scoped TestClient fixture from conftest.py.
If the database file is absent the whole module is skipped.

Coverage strategy:
  - Response structure: required top-level keys present and correctly typed.
  - Data integrity: data_points is a non-empty list; rows have expected columns.
  - Summary correctness: derived fields make sense (e.g. cumulative_pct ends ≤1).
  - Query params: valid filters narrow results; invalid params return 422.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Report 1 — /reports/seasonal-trend
# ---------------------------------------------------------------------------

class TestSeasonalTrend:
    def test_200_and_structure(self, client):
        r = client.get("/reports/seasonal-trend")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "seasonal-trend"
        assert "generated_at" in body
        assert isinstance(body["data_points"], list)
        assert isinstance(body["summary"], dict)

    def test_data_points_have_required_columns(self, client):
        r = client.get("/reports/seasonal-trend")
        rows = r.json()["data_points"]
        if not rows:
            pytest.skip("No data in DB")
        required = {"year", "month", "quarter", "season", "net_revenue",
                    "transaction_count", "total_quantity", "avg_transaction_value",
                    "holiday_net_revenue", "yoy_growth_pct"}
        assert required.issubset(rows[0].keys())

    def test_ordered_by_year_then_month(self, client):
        rows = client.get("/reports/seasonal-trend").json()["data_points"]
        if len(rows) < 2:
            pytest.skip("Not enough rows to check ordering")
        keys = [(r["year"], r["month"]) for r in rows]
        assert keys == sorted(keys)

    def test_season_rollup_keys_are_valid_seasons(self, client):
        summary = client.get("/reports/seasonal-trend").json()["summary"]
        valid = {"Spring", "Summer", "Fall", "Winter"}
        assert set(summary["season_rollup"].keys()).issubset(valid)

    def test_year_filter_narrows_results(self, client):
        all_rows = client.get("/reports/seasonal-trend").json()["data_points"]
        if not all_rows:
            pytest.skip("No data")
        sample_year = all_rows[0]["year"]
        filtered = client.get(f"/reports/seasonal-trend?year={sample_year}").json()["data_points"]
        assert all(r["year"] == sample_year for r in filtered)
        assert len(filtered) <= len(all_rows)

    def test_year_filter_unknown_year_returns_empty(self, client):
        body = client.get("/reports/seasonal-trend?year=1900").json()
        assert body["data_points"] == []
        assert body["summary"]["total_months"] == 0


# ---------------------------------------------------------------------------
# Report 2 — /reports/category-leakage
# ---------------------------------------------------------------------------

class TestCategoryLeakage:
    def test_200_and_structure(self, client):
        r = client.get("/reports/category-leakage")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "category-leakage"
        assert isinstance(body["data_points"], list)
        assert isinstance(body["summary"], dict)

    def test_data_points_have_required_columns(self, client):
        rows = client.get("/reports/category-leakage").json()["data_points"]
        if not rows:
            pytest.skip("No data")
        required = {"category", "gross_revenue", "total_discount", "net_revenue",
                    "discount_rate", "transaction_count", "total_quantity",
                    "revenue_per_transaction"}
        assert required.issubset(rows[0].keys())

    def test_discount_rate_in_unit_range(self, client):
        rows = client.get("/reports/category-leakage").json()["data_points"]
        for row in rows:
            if row["discount_rate"] is not None:
                assert 0.0 <= row["discount_rate"] <= 1.0, (
                    f"discount_rate out of range for {row['category']}: {row['discount_rate']}"
                )

    def test_net_revenue_leq_gross(self, client):
        rows = client.get("/reports/category-leakage").json()["data_points"]
        for row in rows:
            assert row["net_revenue"] <= row["gross_revenue"], (
                f"net_revenue > gross_revenue for {row['category']}"
            )

    def test_summary_flags_highest_leakage(self, client):
        body = client.get("/reports/category-leakage").json()
        if not body["data_points"]:
            pytest.skip("No data")
        summary = body["summary"]
        assert summary["highest_leakage_category"] == body["data_points"][0]["category"]
        assert summary["highest_discount_rate"] is not None

    def test_ordered_by_total_discount_desc(self, client):
        rows = client.get("/reports/category-leakage").json()["data_points"]
        if len(rows) < 2:
            pytest.skip("Not enough rows")
        discounts = [r["total_discount"] for r in rows]
        assert discounts == sorted(discounts, reverse=True)


# ---------------------------------------------------------------------------
# Report 5 — /reports/discount-effectiveness
# ---------------------------------------------------------------------------

class TestDiscountEffectiveness:
    VALID_BANDS = {"0%", "0-10%", "10-20%", "20%+"}

    def test_200_and_structure(self, client):
        r = client.get("/reports/discount-effectiveness")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "discount-effectiveness"
        assert isinstance(body["data_points"], list)
        assert isinstance(body["summary"], dict)

    def test_data_points_have_required_columns(self, client):
        rows = client.get("/reports/discount-effectiveness").json()["data_points"]
        if not rows:
            pytest.skip("No data")
        required = {"category", "discount_band", "transaction_count",
                    "avg_quantity", "avg_net_amount", "net_revenue", "total_discount"}
        assert required.issubset(rows[0].keys())

    def test_discount_bands_are_valid(self, client):
        rows = client.get("/reports/discount-effectiveness").json()["data_points"]
        for row in rows:
            assert row["discount_band"] in self.VALID_BANDS

    def test_category_filter_narrows_results(self, client):
        all_rows = client.get("/reports/discount-effectiveness").json()["data_points"]
        if not all_rows:
            pytest.skip("No data")
        cat = all_rows[0]["category"]
        filtered = client.get(
            f"/reports/discount-effectiveness?category={cat}"
        ).json()["data_points"]
        assert all(r["category"].lower() == cat.lower() for r in filtered)
        assert len(filtered) <= len(all_rows)

    def test_category_filter_unknown_returns_empty(self, client):
        body = client.get("/reports/discount-effectiveness?category=__NONEXISTENT__").json()
        assert body["data_points"] == []

    def test_summary_band_avg_quantity_keys_are_valid(self, client):
        summary = client.get("/reports/discount-effectiveness").json()["summary"]
        for band in summary["band_avg_quantity"]:
            assert band in self.VALID_BANDS

    def test_summary_lifts_is_bool_or_none(self, client):
        summary = client.get("/reports/discount-effectiveness").json()["summary"]
        lifts = summary["higher_discount_lifts_quantity"]
        assert lifts is None or isinstance(lifts, bool)


# ---------------------------------------------------------------------------
# Report 8 — /reports/concentration-risk
# ---------------------------------------------------------------------------

class TestConcentrationRisk:
    def test_200_and_structure(self, client):
        r = client.get("/reports/concentration-risk")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "concentration-risk"
        assert isinstance(body["data_points"], list)
        assert isinstance(body["summary"], dict)

    def test_data_points_have_required_columns(self, client):
        rows = client.get("/reports/concentration-risk").json()["data_points"]
        if not rows:
            pytest.skip("No data")
        required = {"sku_id", "product_name", "category", "brand", "net_revenue",
                    "revenue_rank", "revenue_pct", "cumulative_revenue_pct"}
        assert required.issubset(rows[0].keys())

    def test_default_top_n_is_10(self, client):
        rows = client.get("/reports/concentration-risk").json()["data_points"]
        assert len(rows) <= 10

    def test_top_n_param(self, client):
        rows = client.get("/reports/concentration-risk?top_n=5").json()["data_points"]
        assert len(rows) <= 5

    def test_top_n_too_large_rejected(self, client):
        r = client.get("/reports/concentration-risk?top_n=9999")
        assert r.status_code == 422

    def test_top_n_zero_rejected(self, client):
        r = client.get("/reports/concentration-risk?top_n=0")
        assert r.status_code == 422

    def test_revenue_rank_ordered_ascending(self, client):
        rows = client.get("/reports/concentration-risk?top_n=50").json()["data_points"]
        if len(rows) < 2:
            pytest.skip("Not enough rows")
        ranks = [r["revenue_rank"] for r in rows]
        assert ranks == sorted(ranks)

    def test_revenue_pct_in_unit_range(self, client):
        rows = client.get("/reports/concentration-risk?top_n=50").json()["data_points"]
        for row in rows:
            assert 0.0 <= row["revenue_pct"] <= 1.0

    def test_cumulative_revenue_pct_monotone(self, client):
        rows = client.get("/reports/concentration-risk?top_n=50").json()["data_points"]
        if len(rows) < 2:
            pytest.skip("Not enough rows")
        cumulative = [r["cumulative_revenue_pct"] for r in rows]
        for a, b in zip(cumulative, cumulative[1:]):
            assert b >= a - 1e-6, "cumulative_revenue_pct must be non-decreasing"

    def test_cumulative_pct_ends_at_or_below_one(self, client):
        rows = client.get("/reports/concentration-risk?top_n=500").json()["data_points"]
        if not rows:
            pytest.skip("No data")
        assert rows[-1]["cumulative_revenue_pct"] <= 1.0 + 1e-6

    def test_summary_skus_at_80pct(self, client):
        summary = client.get("/reports/concentration-risk").json()["summary"]
        skus = summary["skus_covering_80pct_revenue"]
        assert skus is None or (isinstance(skus, int) and skus >= 1)

    def test_summary_top_n_echoed(self, client):
        summary = client.get("/reports/concentration-risk?top_n=7").json()["summary"]
        assert summary["top_n"] == 7
