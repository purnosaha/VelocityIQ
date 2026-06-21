#!/usr/bin/env python
"""VelocityIQ — Demand (Quantity) Forecast Model retraining pipeline.

Targets ``quantity`` (units sold) rather than ``net_amount``, so discount_rate
is a real causal driver and the model learns elasticity rather than arithmetic.

Pipeline (reuses generic steps from retrain_model.py):
  1. Pulls current-month records (≤500) + per-month sample of prior 36 months.
  2. Applies exponential-decay weighting (half-life 180 days).
  3. Encodes features (brand, category, store_type, season, marketing_event OHE;
     unit_price, list_price, discount_rate numeric; month/quarter int).
  4. Trains a weighted XGBoost regressor; saves model + feature column list.

    python scripts/demand_model.py [--db-path PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import duckdb
import pandas as pd
from xgboost import XGBRegressor

# Reuse generic pipeline steps — no duplication needed.
from retrain_model import (
    combine_and_weight,
    evaluate,
    feature_importances,
    print_report,
    split,
    train,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = os.environ.get("DUCKDB_PATH", "./data/velocityiq.duckdb")
MODEL_PATH = "./models/demand_model_latest.json"
LOG_PATH = "./logs/demand_retrain_log.json"
FEATURE_COLS_PATH = "./models/demand_model_features.json"

NUMERIC_FEATURES = ["unit_price", "list_price", "discount_rate"]
INT_FEATURES = ["month", "quarter"]
CATEGORICAL_FEATURES = ["category", "brand", "store_type", "season", "marketing_event"]
BOOL_FEATURE = "is_holiday"
TARGET = "quantity"

HALF_LIFE_DAYS = 180.0
NEW_RECORD_CAP = 500
HISTORICAL_MONTHS = 36
HISTORICAL_ROWS_PER_MONTH = 100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger("demand_model")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_COLS = """
    st.quantity,
    st.unit_price,
    pm.list_price,
    COALESCE(st.discount_amount / NULLIF(st.total_amount, 0), 0.0) AS discount_rate,
    st.transaction_date,
    pm.category,
    pm.brand,
    sr.store_type,
    sc.is_holiday,
    sc.season,
    sc.month,
    sc.quarter,
    sc.marketing_event
"""

_JOINS = """
FROM sales_transactions st
JOIN product_master pm  ON st.sku_id   = pm.sku_id  AND pm.is_current = TRUE
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
"""

NEW_RECORDS_SQL = f"""
SELECT {_COLS}
{_JOINS}
WHERE DATE_TRUNC('month', st.transaction_date) = DATE_TRUNC('month', CURRENT_DATE)
ORDER BY st.transaction_date DESC
LIMIT {NEW_RECORD_CAP}
"""

HISTORICAL_SQL = f"""
SELECT {_COLS},
    DATE_TRUNC('month', st.transaction_date) AS month_bucket
{_JOINS}
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


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    all_feature_cols = NUMERIC_FEATURES + INT_FEATURES + CATEGORICAL_FEATURES + [BOOL_FEATURE]
    X = df[all_feature_cols].copy()
    y = df[TARGET].astype(float)

    for col in NUMERIC_FEATURES:
        X[col] = pd.to_numeric(X[col], errors="coerce")
        if X[col].isna().any():
            median = X[col].median()
            logger.info("Imputing %d missing %r with median %.4f", int(X[col].isna().sum()), col, median)
            X[col] = X[col].fillna(median)

    for col in INT_FEATURES:
        X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(int)

    X["marketing_event"] = X["marketing_event"].fillna("none")
    for col in [c for c in CATEGORICAL_FEATURES if c != "marketing_event"]:
        if X[col].isna().any():
            mode = X[col].mode(dropna=True)
            fill = mode.iloc[0] if not mode.empty else "unknown"
            logger.info("Imputing %d missing %r with mode %r", int(X[col].isna().sum()), col, fill)
            X[col] = X[col].fillna(fill)

    if X[BOOL_FEATURE].isna().any():
        mode = X[BOOL_FEATURE].mode(dropna=True)
        X[BOOL_FEATURE] = X[BOOL_FEATURE].fillna(mode.iloc[0] if not mode.empty else False)
    X[BOOL_FEATURE] = X[BOOL_FEATURE].astype(bool).astype(int)

    X = pd.get_dummies(X, columns=CATEGORICAL_FEATURES, prefix=CATEGORICAL_FEATURES)
    bool_cols = X.select_dtypes(include="bool").columns
    X[bool_cols] = X[bool_cols].astype(int)

    # Persist column order for inference alignment.
    os.makedirs(os.path.dirname(FEATURE_COLS_PATH), exist_ok=True)
    with open(FEATURE_COLS_PATH, "w") as fh:
        json.dump(sorted(X.columns.tolist()), fh)
    X = X.reindex(columns=sorted(X.columns))

    logger.info("Prepared feature matrix: %d rows x %d encoded features", X.shape[0], X.shape[1])
    return X, y


def save_model(model: XGBRegressor) -> str:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    model.save_model(MODEL_PATH)
    logger.info("Saved demand model to %s", MODEL_PATH)
    return MODEL_PATH


def write_log(summary: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as fh:
        json.dump(summary, fh, indent=2)
    logger.info("Wrote run summary to %s", LOG_PATH)


# ---------------------------------------------------------------------------
# Inference utilities (used by main.py)
# ---------------------------------------------------------------------------


def load_demand_model() -> tuple[XGBRegressor | None, list[str] | None]:
    """Return (model, feature_cols) or (None, None) if not yet trained."""
    if not os.path.exists(MODEL_PATH) or not os.path.exists(FEATURE_COLS_PATH):
        return None, None
    try:
        model = XGBRegressor()
        model.load_model(MODEL_PATH)
        with open(FEATURE_COLS_PATH) as fh:
            feature_cols = json.load(fh)
        return model, feature_cols
    except Exception as exc:
        logger.warning("Failed to load demand model: %s", exc)
        return None, None


def build_inference_df(rows: list[dict], feature_cols: list[str]) -> pd.DataFrame:
    """Build an aligned inference DataFrame from a list of raw feature dicts."""
    df = pd.DataFrame(rows)

    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    for col in INT_FEATURES:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    if "marketing_event" not in df.columns:
        df["marketing_event"] = "none"
    df["marketing_event"] = df["marketing_event"].fillna("none")

    for col in [c for c in CATEGORICAL_FEATURES if c != "marketing_event"]:
        if col not in df.columns:
            df[col] = "unknown"
        df[col] = df[col].fillna("unknown")

    if BOOL_FEATURE not in df.columns:
        df[BOOL_FEATURE] = 0
    df[BOOL_FEATURE] = df[BOOL_FEATURE].astype(bool).astype(int)

    df = pd.get_dummies(df, columns=CATEGORICAL_FEATURES, prefix=CATEGORICAL_FEATURES)
    bool_cols = df.select_dtypes(include="bool").columns
    df[bool_cols] = df[bool_cols].astype(int)

    # Align to training columns; fill any missing OHE columns with 0.
    df = df.reindex(columns=feature_cols, fill_value=0)
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def retrain(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Run the full demand model retraining pipeline. Returns run summary."""
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
        "features_path": FEATURE_COLS_PATH,
    }
    write_log(summary)
    print_report(summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain the VelocityIQ demand (quantity) model.")
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the DuckDB database (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    retrain(args.db_path)


if __name__ == "__main__":
    main()
