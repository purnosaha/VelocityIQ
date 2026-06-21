#!/usr/bin/env python
"""VelocityIQ — Seasonal Revenue Forecast Model (SARIMA).

Trains a SARIMAX model on monthly net_revenue from v_monthly_sales_summary
and serialises the fitted results for use by GET /reports/seasonal-forecast.

    python scripts/forecast_model.py [--db-path PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd

DEFAULT_DB_PATH = os.environ.get("DUCKDB_PATH", "./data/velocityiq.duckdb")
MODEL_PATH = "./models/seasonal_forecast_latest.pkl"
LOG_PATH = "./logs/forecast_retrain_log.json"
SEASONAL_PERIOD = 12
DEFAULT_HORIZON = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("forecast_model")


def extract_series(db_path: str) -> pd.Series:
    """Pull completed monthly net_revenue, excluding the current partial month."""
    conn = duckdb.connect(db_path, read_only=True)
    try:
        sql = """
        SELECT "year", month, SUM(net_revenue) AS net_revenue
        FROM v_monthly_sales_summary
        WHERE MAKE_DATE("year"::INT, month::INT, 1) < DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY "year", month
        ORDER BY "year", month
        """
        df = conn.execute(sql).df()
    finally:
        conn.close()

    logger.info("Pulled %d monthly observations", len(df))
    if df.empty:
        raise ValueError(
            "No data returned from v_monthly_sales_summary — ensure sample data is loaded."
        )

    dates = pd.PeriodIndex.from_fields(year=df["year"].astype(int), month=df["month"].astype(int), freq="M").to_timestamp()
    series = pd.Series(df["net_revenue"].values, index=dates, dtype=float, name="net_revenue")
    series = series.asfreq("MS")
    return series


def fit(series: pd.Series):
    """Select SARIMA orders by AIC grid search; fall back to (1,1,1)(0,1,1,12) on failure."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    best_aic = float("inf")
    best_result = None
    best_orders = None

    for p in (0, 1):
        for d in (0, 1):
            for q in (0, 1):
                for P in (0, 1):
                    for D in (0, 1):
                        for Q in (0, 1):
                            try:
                                mod = SARIMAX(
                                    series,
                                    order=(p, d, q),
                                    seasonal_order=(P, D, Q, SEASONAL_PERIOD),
                                    enforce_stationarity=False,
                                    enforce_invertibility=False,
                                )
                                result = mod.fit(disp=False)
                                if result.aic < best_aic:
                                    best_aic = result.aic
                                    best_result = result
                                    best_orders = ((p, d, q), (P, D, Q, SEASONAL_PERIOD))
                            except Exception:
                                continue

    if best_result is None:
        logger.warning("Grid search yielded no valid models — using safe fallback orders")
        try:
            mod = SARIMAX(
                series,
                order=(1, 1, 1),
                seasonal_order=(0, 1, 1, SEASONAL_PERIOD),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            best_result = mod.fit(disp=False)
            best_orders = ((1, 1, 1), (0, 1, 1, SEASONAL_PERIOD))
            best_aic = best_result.aic
        except Exception as e:
            raise RuntimeError(f"SARIMA fallback also failed: {e}") from e

    logger.info(
        "Best SARIMA orders=%s seasonal=%s  AIC=%.2f",
        best_orders[0], best_orders[1], best_aic,
    )
    return best_result, best_orders, best_aic


def save(results) -> str:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    results.save(MODEL_PATH)
    logger.info("Saved SARIMA model to %s", MODEL_PATH)
    return MODEL_PATH


def forecast(results, horizon: int) -> list[dict]:
    """Return horizon-step ahead predictions with 95 % confidence intervals."""
    fc = results.get_forecast(steps=horizon)
    mean = fc.predicted_mean
    ci = fc.conf_int()

    output = []
    for dt, pred in mean.items():
        lower = float(ci.loc[dt].iloc[0])
        upper = float(ci.loc[dt].iloc[1])
        output.append({
            "year": int(dt.year),
            "month": int(dt.month),
            "predicted_net_revenue": round(float(pred), 2),
            "lower": round(lower, 2),
            "upper": round(upper, 2),
        })
    return output


def train_and_save(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Run the full pipeline: extract → fit → save → log. Returns the run summary."""
    started = datetime.now(timezone.utc)

    series = extract_series(db_path)
    n_obs = len(series)

    results, chosen_orders, aic = fit(series)

    fitted = results.fittedvalues
    residuals = (series - fitted).dropna()
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    mae = float(np.mean(np.abs(residuals)))

    model_path = save(results)

    finished = datetime.now(timezone.utc)
    summary = {
        "retrain_timestamp": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "n_obs": n_obs,
        "order": list(chosen_orders[0]),
        "seasonal_order": list(chosen_orders[1]),
        "aic": round(aic, 4),
        "rmse": round(rmse, 4),
        "mae": round(mae, 4),
        "model_path": model_path,
    }

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as fh:
        json.dump(summary, fh, indent=2)
    logger.info("Wrote forecast retrain log to %s", LOG_PATH)

    _print_report(summary)
    return summary


def _print_report(s: dict) -> None:
    line = "=" * 60
    print(f"\n{line}")
    print("VelocityIQ — Seasonal Forecast Retrain Report")
    print(line)
    print(f"  Observations .............. {s['n_obs']}")
    print(f"  Order (p,d,q) ............. {s['order']}")
    print(f"  Seasonal order (P,D,Q,s) .. {s['seasonal_order']}")
    print(f"  AIC ....................... {s['aic']:.4f}")
    print(f"  In-sample RMSE ............ {s['rmse']:.4f}")
    print(f"  In-sample MAE ............. {s['mae']:.4f}")
    print(f"  Model saved to ............ {s['model_path']}")
    print(f"  Retrain timestamp ......... {s['retrain_timestamp']}")
    print(f"{line}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train the VelocityIQ seasonal SARIMA forecast model."
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    train_and_save(args.db_path)


if __name__ == "__main__":
    main()
