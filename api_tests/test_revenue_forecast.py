"""API tests for the per-(category, region) SARIMA revenue forecast.

Covers POST /train_revenue_forecast and GET /reports/revenue-forecast. Mirrors
the aggregate-model tests in test_reports.py::TestSeasonalForecast, but trains
the 20 slice models once per module (fitting all of them is ~25-35s) via the
module-scoped ``trained`` fixture.
"""
from __future__ import annotations

import pytest

REQUIRED_POINT_KEYS = {"year", "month", "predicted_net_revenue", "lower", "upper"}


@pytest.fixture(scope="module")
def trained(client):
    """Train all slice models once for the module; return the training summary."""
    r = client.post("/train_revenue_forecast")
    assert r.status_code == 200, r.text
    return r.json()["summary"]


class TestRevenueForecast:
    def test_503_before_training(self, client, tmp_path, monkeypatch):
        import revenue_forecast_model
        monkeypatch.setattr(
            revenue_forecast_model, "MANIFEST_PATH", str(tmp_path / "no_manifest.json")
        )
        r = client.get("/reports/revenue-forecast")
        assert r.status_code == 503

    def test_train_returns_200_with_summary(self, trained):
        s = trained
        assert s["slices_trained"] > 0
        assert "retrain_timestamp" in s
        assert s["slices_trained"] + s["slices_skipped"] + s["slices_failed"] == s["slices_total"]

    def test_forecast_returns_slices_and_aggregate(self, client, trained):
        r = client.get("/reports/revenue-forecast?horizon=3")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "revenue-forecast"
        assert body["horizon"] == 3

        slices = body["slices"]
        assert len(slices) > 0
        for sl in slices:
            assert {"category", "region_id", "region_name", "forecast"} <= sl.keys()
            assert len(sl["forecast"]) == 3
            for point in sl["forecast"]:
                assert REQUIRED_POINT_KEYS.issubset(point.keys())

        aggregate = body["aggregate"]
        assert len(aggregate) == 3
        for point in aggregate:
            assert REQUIRED_POINT_KEYS.issubset(point.keys())

    def test_filter_by_category(self, client, trained):
        # Discover an available category from the unfiltered report.
        all_slices = client.get("/reports/revenue-forecast?horizon=1").json()["slices"]
        category = all_slices[0]["category"]

        r = client.get(f"/reports/revenue-forecast?category={category}&horizon=1")
        assert r.status_code == 200
        body = r.json()
        assert body["filters"]["category"] == category
        assert len(body["slices"]) > 0
        assert all(sl["category"] == category for sl in body["slices"])

    def test_filter_by_region(self, client, trained):
        all_slices = client.get("/reports/revenue-forecast?horizon=1").json()["slices"]
        region_name = all_slices[0]["region_name"]
        region_id = all_slices[0]["region_id"]

        # Filter by region_name and by region_id should both resolve to that region.
        for region in (region_name, region_id):
            r = client.get(f"/reports/revenue-forecast?region={region}&horizon=1")
            assert r.status_code == 200
            slices = r.json()["slices"]
            assert len(slices) > 0
            assert all(sl["region_id"] == region_id for sl in slices)


class TestRevenueByCategoryRegion:
    """Historical actuals endpoint (no trained model required)."""

    def test_returns_data_points(self, client):
        r = client.get("/reports/revenue-by-category-region")
        assert r.status_code == 200
        body = r.json()
        assert body["report"] == "revenue-by-category-region"
        dp = body["data_points"]
        assert len(dp) > 0
        for key in ("category", "region_id", "region_name", "year", "month", "net_revenue"):
            assert key in dp[0]

    def test_filter_by_category_and_region(self, client):
        all_pts = client.get("/reports/revenue-by-category-region").json()["data_points"]
        cat = all_pts[0]["category"]
        reg = all_pts[0]["region_name"]
        r = client.get(f"/reports/revenue-by-category-region?category={cat}&region={reg}")
        assert r.status_code == 200
        pts = r.json()["data_points"]
        assert len(pts) > 0
        assert all(p["category"] == cat and p["region_name"] == reg for p in pts)
