#!/usr/bin/env python
"""VelocityIQ — Revenue Forecasting Model retraining pipeline.

Forecasts ``net_amount`` from ``sales_transactions`` using an XGBoost regressor.

The pipeline:
  1. Pulls current-month "new" records (capped at 500) and a per-month sample of
     the prior 36 months of "historical" records from the DuckDB star schema.
  2. Tags each row with its source, merges, and assigns exponential-decay weights
     (half-life 180 days) so recent rows count more; current-month rows get the
     maximum weight.
  3. Imputes missing values, one-hot encodes categoricals, trains a weighted
     XGBoost regressor, and evaluates on a stratified 80/20 split.
  4. Saves the model to ./models/revenue_forecast_latest.json and a run summary
     to ./logs/retrain_log.json.

Designed to run unattended (cron / Airflow) — no manual steps, no prompts.

    python scripts/retrain_model.py [--db-path PATH]
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
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = os.environ.get("DUCKDB_PATH", "./data/velocityiq.duckdb")
MODEL_PATH = "./models/revenue_forecast_latest.json"
LOG_PATH = "./logs/retrain_log.json"

NUMERIC_FEATURES = ["quantity", "unit_price", "discount_amount"]
CATEGORICAL_FEATURES = ["category", "store_type"]
BOOL_FEATURE = "is_holiday"
PREDICTORS = NUMERIC_FEATURES + CATEGORICAL_FEATURES + [BOOL_FEATURE]
TARGET = "net_amount"

HALF_LIFE_DAYS = 180.0
NEW_RECORD_CAP = 500
HISTORICAL_MONTHS = 36
HISTORICAL_ROWS_PER_MONTH = 100
TEST_SIZE = 0.20
RANDOM_STATE = 42

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("retrain_model")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

NEW_RECORDS_SQL = f"""
SELECT
    st.quantity,
    st.unit_price,
    st.discount_amount,
    st.net_amount,
    st.transaction_date,
    pm.category,
    sr.store_type,
    sc.is_holiday
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
WHERE DATE_TRUNC('month', st.transaction_date) = DATE_TRUNC('month', CURRENT_DATE)
ORDER BY st.transaction_date DESC
LIMIT {NEW_RECORD_CAP}
"""

HISTORICAL_SQL = f"""
SELECT
    st.quantity,
    st.unit_price,
    st.discount_amount,
    st.net_amount,
    st.transaction_date,
    pm.category,
    sr.store_type,
    sc.is_holiday,
    DATE_TRUNC('month', st.transaction_date) AS month_bucket
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
WHERE st.transaction_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '{HISTORICAL_MONTHS} months'
  AND DATE_TRUNC('month', st.transaction_date) < DATE_TRUNC('month', CURRENT_DATE)
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY DATE_TRUNC('month', st.transaction_date)
    ORDER BY RANDOM()
) <= {HISTORICAL_ROWS_PER_MONTH}
"""


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def extract_data(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run both queries read-only and return (new_df, historical_df)."""
    logger.info("Connecting to DuckDB at %s (read-only)", db_path)
    conn = duckdb.connect(db_path, read_only=True)
    try:
        new_df = conn.execute(NEW_RECORDS_SQL).df()
        logger.info("Pulled %d new (current-month) records", len(new_df))
        hist_df = conn.execute(HISTORICAL_SQL).df()
        logger.info(
            "Pulled %d historical records across %d month buckets",
            len(hist_df),
            hist_df["month_bucket"].nunique() if not hist_df.empty else 0,
        )
    finally:
        conn.close()
    return new_df, hist_df


def combine_and_weight(new_df: pd.DataFrame, hist_df: pd.DataFrame) -> pd.DataFrame:
    """Tag source, merge, and compute exponential-decay sample weights."""
    new_df = new_df.copy()
    hist_df = hist_df.drop(columns=["month_bucket"], errors="ignore").copy()
    new_df["source"] = "new"
    hist_df["source"] = "historical"

    combined = pd.concat([new_df, hist_df], ignore_index=True)
    if combined.empty:
        raise RuntimeError(
            "No training rows returned. Ensure sample data is loaded "
            "(python scripts/load_sample_data.py)."
        )

    combined["transaction_date"] = pd.to_datetime(combined["transaction_date"])

    # Exponential decay: weight = 0.5 ** (age_days / half_life). The most recent
    # row in the dataset anchors age 0 (weight 1.0); older rows decay toward 0.
    reference_date = combined["transaction_date"].max()
    age_days = (reference_date - combined["transaction_date"]).dt.days.astype(float)
    combined["sample_weight"] = np.power(0.5, age_days / HALF_LIFE_DAYS)

    # Guarantee current-month ("new") rows receive the maximum weight present.
    max_weight = float(combined["sample_weight"].max())
    combined.loc[combined["source"] == "new", "sample_weight"] = max_weight

    logger.info(
        "Combined %d rows (new=%d, historical=%d); weights in [%.4f, %.4f]",
        len(combined),
        int((combined["source"] == "new").sum()),
        int((combined["source"] == "historical").sum()),
        float(combined["sample_weight"].min()),
        float(combined["sample_weight"].max()),
    )
    return combined


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Impute missing values, encode categoricals, return (X, y)."""
    X = df[PREDICTORS].copy()
    y = df[TARGET].astype(float)

    # Median imputation for numerics.
    for col in NUMERIC_FEATURES:
        X[col] = pd.to_numeric(X[col], errors="coerce")
        if X[col].isna().any():
            median = X[col].median()
            logger.info("Imputing %d missing %r with median %.4f", int(X[col].isna().sum()), col, median)
            X[col] = X[col].fillna(median)

    # Mode imputation for categoricals.
    for col in CATEGORICAL_FEATURES:
        if X[col].isna().any():
            mode = X[col].mode(dropna=True)
            fill = mode.iloc[0] if not mode.empty else "unknown"
            logger.info("Imputing %d missing %r with mode %r", int(X[col].isna().sum()), col, fill)
            X[col] = X[col].fillna(fill)

    # is_holiday -> int (mode-impute any nulls first).
    if X[BOOL_FEATURE].isna().any():
        mode = X[BOOL_FEATURE].mode(dropna=True)
        X[BOOL_FEATURE] = X[BOOL_FEATURE].fillna(mode.iloc[0] if not mode.empty else False)
    X[BOOL_FEATURE] = X[BOOL_FEATURE].astype(bool).astype(int)

    # One-hot encode category and store_type.
    X = pd.get_dummies(X, columns=CATEGORICAL_FEATURES, prefix=CATEGORICAL_FEATURES)
    # XGBoost wants numeric dtypes; cast bool one-hot columns to int.
    bool_cols = X.select_dtypes(include="bool").columns
    X[bool_cols] = X[bool_cols].astype(int)

    logger.info("Prepared feature matrix: %d rows x %d encoded features", X.shape[0], X.shape[1])
    return X, y


def _stratify_bins(y: pd.Series) -> pd.Series | None:
    """Build target bins for a stratified split, shrinking bin count until every
    bin has >= 2 members. Returns None if stratification is not feasible."""
    n = len(y)
    for q in (10, 5, 4, 3, 2):
        if n < 2 * q:
            continue
        bins = pd.qcut(y, q=q, duplicates="drop")
        if bins.value_counts().min() >= 2 and bins.nunique() >= 2:
            return bins
    return None


def split(X: pd.DataFrame, y: pd.Series, weights: pd.Series):
    """Stratified 80/20 train/test split (falls back to random if needed)."""
    bins = _stratify_bins(y)
    if bins is not None:
        logger.info("Using stratified split on %d target bins", bins.nunique())
        stratify = bins
    else:
        logger.warning("Stratification infeasible for this sample; using random split")
        stratify = None

    return train_test_split(
        X, y, weights,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=stratify,
    )


def train(X_train, y_train, w_train) -> XGBRegressor:
    """Train an XGBoost regressor with sample weights."""
    model = XGBRegressor(
        objective="reg:squarederror",
        n_estimators=400,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    logger.info("Training XGBoost regressor on %d rows", len(X_train))
    model.fit(X_train, y_train, sample_weight=w_train)
    return model


def evaluate(model: XGBRegressor, X_test, y_test) -> dict[str, float]:
    """Compute RMSE, MAE, R² on the held-out test set."""
    preds = model.predict(X_test)
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
    mae = float(mean_absolute_error(y_test, preds))
    r2 = float(r2_score(y_test, preds))
    logger.info("Evaluation — RMSE=%.4f  MAE=%.4f  R2=%.4f", rmse, mae, r2)
    return {"rmse": rmse, "mae": mae, "r2": r2}


def feature_importances(model: XGBRegressor, feature_names: list[str]) -> list[dict]:
    """Feature importances ranked by gain (descending)."""
    gain = model.get_booster().get_score(importance_type="gain")
    ranked = [
        {"feature": name, "gain": float(gain.get(name, 0.0))}
        for name in feature_names
    ]
    ranked.sort(key=lambda d: d["gain"], reverse=True)
    return ranked


def save_model(model: XGBRegressor) -> str:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    model.save_model(MODEL_PATH)
    logger.info("Saved model to %s", MODEL_PATH)
    return MODEL_PATH


def write_log(summary: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as fh:
        json.dump(summary, fh, indent=2)
    logger.info("Wrote run summary to %s", LOG_PATH)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def retrain(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Run the full retraining pipeline end-to-end. Returns the run summary."""
    started = datetime.now(timezone.utc)

    new_df, hist_df = extract_data(db_path)
    rows_new = int(len(new_df))
    rows_historical = int(len(hist_df))
    hist_months = int(hist_df["month_bucket"].nunique()) if not hist_df.empty else 0

    combined = combine_and_weight(new_df, hist_df)
    X, y = prepare_features(combined)
    weights = combined["sample_weight"]

    X_train, X_test, y_train, y_test, w_train, _w_test = split(X, y, weights)
    model = train(X_train, y_train, w_train)
    metrics = evaluate(model, X_test, y_test)
    importances = feature_importances(model, list(X.columns))
    model_path = save_model(model)

    finished = datetime.now(timezone.utc)
    summary = {
        "retrain_timestamp": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "rows_new": rows_new,
        "rows_historical": rows_historical,
        "historical_months_covered": hist_months,
        "total_rows": int(len(combined)),
        "rmse": metrics["rmse"],
        "mae": metrics["mae"],
        "r2": metrics["r2"],
        "feature_importances": importances,
        "model_path": model_path,
    }
    write_log(summary)
    print_report(summary)
    return summary


def print_report(s: dict) -> None:
    line = "=" * 60
    print(f"\n{line}")
    print("VelocityIQ — Revenue Forecast Retrain Report")
    print(line)
    print("Row counts:")
    print(f"  New records used .......... {s['rows_new']}")
    print(f"  Historical months covered . {s['historical_months_covered']}")
    print(f"  Historical rows ........... {s['rows_historical']}")
    print(f"  Total training rows ....... {s['total_rows']}")
    print("Evaluation (test set):")
    print(f"  RMSE ...................... {s['rmse']:.4f}")
    print(f"  MAE ....................... {s['mae']:.4f}")
    print(f"  R² ........................ {s['r2']:.4f}")
    print("Top 6 features by gain:")
    for i, fi in enumerate(s["feature_importances"][:6], 1):
        print(f"  {i}. {fi['feature']:<28} gain={fi['gain']:.4f}")
    print(f"Model saved to ............... {s['model_path']}")
    print(f"Retrain timestamp ............ {s['retrain_timestamp']}")
    print(f"{line}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain the VelocityIQ revenue-forecast model.")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the DuckDB database (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    retrain(args.db_path)


if __name__ == "__main__":
    main()
