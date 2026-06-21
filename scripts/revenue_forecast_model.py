#!/usr/bin/env python
"""VelocityIQ — Per-(Category, Region) Revenue Forecast Model (SARIMA).

Fits one SARIMA model per (product category x region) slice on monthly
net_revenue from ``v_monthly_sales_by_category_region`` and serialises each
fitted result for use by GET /reports/revenue-forecast.

This is the category/region-aware counterpart to ``forecast_model.py`` (which
fits a single aggregate model). It reuses that module's AIC grid search
(``fit``) and forecast formatter (``forecast``) so the two stay consistent.

    python scripts/revenue_forecast_model.py [--db-path PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd

# Reuse the aggregate model's AIC grid search — no duplication.
from forecast_model import fit

DEFAULT_DB_PATH = os.environ.get("DUCKDB_PATH", "./data/velocityiq.duckdb")
MODEL_DIR = "./models/revenue_forecast_slices"
MANIFEST_PATH = "./models/revenue_forecast_slices_manifest.json"
LOG_PATH = "./logs/revenue_forecast_retrain_log.json"
SEASONAL_PERIOD = 12
MIN_OBS = 24  # need at least two seasonal cycles to fit a monthly SARIMA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("revenue_forecast_model")


def slice_key(category: str, region_id: str) -> str:
    """Return a filesystem-safe key for a (category, region_id) slice."""
    raw = f"{category}__{region_id}"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", raw)


def extract_slices(db_path: str) -> tuple[dict[tuple[str, str], pd.Series], dict[str, str]]:
    """Pull completed monthly net_revenue per (category, region_id) slice.

    Returns (series_by_slice, region_name_by_id). Each series is reindexed to the
    full observed month range with missing months filled as 0.0 (a month with no
    sales is $0 revenue) so SARIMA sees a regular monthly frequency.
    """
    conn = duckdb.connect(db_path, read_only=True)
    try:
        sql = """
        SELECT category, region_id, region_name, "year", month, SUM(net_revenue) AS net_revenue
        FROM v_monthly_sales_by_category_region
        WHERE MAKE_DATE("year"::INT, month::INT, 1) < DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY category, region_id, region_name, "year", month
        ORDER BY category, region_id, "year", month
        """
        df = conn.execute(sql).df()
    finally:
        conn.close()

    if df.empty:
        raise ValueError(
            "No data returned from v_monthly_sales_by_category_region — ensure sample "
            "data is loaded and the view exists (re-run scripts/init_db.py)."
        )

    df["date"] = pd.PeriodIndex.from_fields(
        year=df["year"].astype(int), month=df["month"].astype(int), freq="M"
    ).to_timestamp()

    # Common month range so every slice covers the same span; gaps filled with 0.
    full_index = pd.date_range(df["date"].min(), df["date"].max(), freq="MS")

    region_name_by_id = (
        df.drop_duplicates("region_id").set_index("region_id")["region_name"].to_dict()
    )

    series_by_slice: dict[tuple[str, str], pd.Series] = {}
    for (category, region_id), grp in df.groupby(["category", "region_id"]):
        s = pd.Series(grp["net_revenue"].values, index=grp["date"], dtype=float)
        s = s.reindex(full_index, fill_value=0.0).asfreq("MS")
        s.name = "net_revenue"
        series_by_slice[(category, region_id)] = s

    logger.info(
        "Extracted %d slices spanning %d months (%s to %s)",
        len(series_by_slice),
        len(full_index),
        full_index.min().strftime("%Y-%m"),
        full_index.max().strftime("%Y-%m"),
    )
    return series_by_slice, region_name_by_id


def save_slice(results, key: str) -> str:
    """Serialise one slice's fitted SARIMAX results; return its path."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, f"{key}.pkl")
    results.save(path)
    return path


def load_slice(key: str):
    """Load a single slice's fitted SARIMAX results by key."""
    from statsmodels.tsa.statespace.sarimax import SARIMAXResults

    path = os.path.join(MODEL_DIR, f"{key}.pkl")
    return SARIMAXResults.load(path)


def load_manifest() -> dict | None:
    """Return the manifest dict, or None if no model has been trained yet."""
    if not os.path.exists(MANIFEST_PATH):
        return None
    try:
        with open(MANIFEST_PATH) as fh:
            return json.load(fh)
    except Exception as exc:  # pragma: no cover - corrupt manifest
        logger.warning("Failed to read manifest %s: %s", MANIFEST_PATH, exc)
        return None


def train_and_save_all(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Fit + persist a SARIMA model for every (category, region) slice.

    Slices with fewer than MIN_OBS observations are skipped; any slice whose fit
    raises is recorded as failed and the run continues. Writes a manifest of the
    trained slices and a run-summary log. Returns the run summary.
    """
    started = datetime.now(timezone.utc)

    series_by_slice, region_name_by_id = extract_slices(db_path)

    slices_meta: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for (category, region_id), series in series_by_slice.items():
        region_name = region_name_by_id.get(region_id, region_id)
        n_obs = int(series.notna().sum())
        if n_obs < MIN_OBS:
            logger.warning(
                "Skipping slice %s x %s — only %d obs (< %d)",
                category, region_id, n_obs, MIN_OBS,
            )
            skipped.append({"category": category, "region_id": region_id, "n_obs": n_obs})
            continue

        try:
            results, orders, aic = fit(series)
            fitted = results.fittedvalues
            residuals = (series - fitted).dropna()
            rmse = float(np.sqrt(np.mean(residuals ** 2))) if len(residuals) else None
            mae = float(np.mean(np.abs(residuals))) if len(residuals) else None

            key = slice_key(category, region_id)
            model_path = save_slice(results, key)

            slices_meta.append({
                "category": category,
                "region_id": region_id,
                "region_name": region_name,
                "key": key,
                "order": list(orders[0]),
                "seasonal_order": list(orders[1]),
                "aic": round(float(aic), 4),
                "rmse": round(rmse, 4) if rmse is not None else None,
                "mae": round(mae, 4) if mae is not None else None,
                "n_obs": n_obs,
                "model_path": model_path,
            })
            logger.info("Trained slice %s x %s (n_obs=%d)", category, region_id, n_obs)
        except Exception as exc:
            logger.warning("Failed to fit slice %s x %s: %s", category, region_id, exc)
            failed.append({"category": category, "region_id": region_id, "error": str(exc)})

    finished = datetime.now(timezone.utc)
    manifest = {
        "retrain_timestamp": finished.isoformat(),
        "model_dir": MODEL_DIR,
        "slices": slices_meta,
    }
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w") as fh:
        json.dump(manifest, fh, indent=2)
    logger.info("Wrote manifest with %d slices to %s", len(slices_meta), MANIFEST_PATH)

    summary = {
        "retrain_timestamp": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "slices_total": len(series_by_slice),
        "slices_trained": len(slices_meta),
        "slices_skipped": len(skipped),
        "slices_failed": len(failed),
        "skipped": skipped,
        "failed": failed,
        "manifest_path": MANIFEST_PATH,
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as fh:
        json.dump(summary, fh, indent=2)
    logger.info("Wrote revenue-forecast retrain log to %s", LOG_PATH)

    _print_report(summary)
    return summary


def _print_report(s: dict) -> None:
    line = "=" * 60
    print(f"\n{line}")
    print("VelocityIQ — Revenue Forecast (per category x region) Retrain Report")
    print(line)
    print(f"  Slices total .............. {s['slices_total']}")
    print(f"  Slices trained ............ {s['slices_trained']}")
    print(f"  Slices skipped ............ {s['slices_skipped']}")
    print(f"  Slices failed ............. {s['slices_failed']}")
    print(f"  Duration (s) .............. {s['duration_seconds']}")
    print(f"  Manifest .................. {s['manifest_path']}")
    print(f"  Retrain timestamp ......... {s['retrain_timestamp']}")
    print(f"{line}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train per-(category, region) SARIMA revenue forecast models."
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    train_and_save_all(args.db_path)


if __name__ == "__main__":
    main()
