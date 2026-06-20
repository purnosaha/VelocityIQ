"""Gold layer — the frozen dimensional model (existing ``sql/init.sql`` schema).

This module does NOT change the Gold schema. It only:
  - builds the Gold dimensions idempotently (``ON CONFLICT DO NOTHING``):
    product_master + regional_reference + store_reference are loaded from their
    bronze landings; seasonal_calendar + weather_overlay are loaded from the
    ETL-generated reference frames (calendar is pure date math, weather is
    synthetic — neither has a meaningful "raw POS" form);
  - promotes only clean, FK-satisfiable rows from ``silver_transactions`` into
    the ``sales_transactions`` fact table. Rows flagged ``sku_unmatched`` — or
    whose store/date can't be matched — are intentionally left in Silver.

``transaction_id`` is deterministic (``source_system || '-' || source_txn_id``)
so promotion is idempotent: re-running never double-inserts.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from etl import config

logger = config.get_logger("etl.gold")


def _table_count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def build_dimensions(
    conn: duckdb.DuckDBPyConnection,
    calendar_df: pd.DataFrame,
    weather_df: pd.DataFrame,
) -> dict:
    """Idempotently populate all Gold dimensions. Returns row counts."""
    # --- product_master (from bronze) ---
    conn.execute(
        """
        INSERT INTO product_master
            (sku_id, product_name, category, brand, package_size,
             list_price, launch_date, status, is_current)
        SELECT sku, product_name, category, brand, package_size,
               list_price, CAST(launch_date AS DATE), status, TRUE
        FROM bronze_product_master
        QUALIFY ROW_NUMBER() OVER (PARTITION BY sku ORDER BY ingested_at DESC) = 1
        ON CONFLICT DO NOTHING
        """
    )

    # --- regional_reference (from bronze region/store reference) ---
    conn.execute(
        """
        INSERT INTO regional_reference
            (region_id, region_name, country, timezone, population, income_level, created_date)
        SELECT region_id, region_name, country, timezone, population, income_level, CURRENT_DATE
        FROM bronze_region_reference
        QUALIFY ROW_NUMBER() OVER (PARTITION BY region_id ORDER BY ingested_at DESC) = 1
        ON CONFLICT DO NOTHING
        """
    )

    # --- store_reference (from bronze region/store reference) ---
    conn.execute(
        """
        INSERT INTO store_reference
            (store_id, store_name, region_id, store_type, created_date)
        SELECT store_id, store_name, region_id, store_type, CURRENT_DATE
        FROM bronze_region_reference
        QUALIFY ROW_NUMBER() OVER (PARTITION BY store_id ORDER BY ingested_at DESC) = 1
        ON CONFLICT DO NOTHING
        """
    )

    # --- seasonal_calendar (ETL-generated, direct to Gold) ---
    conn.register("_cal_df", calendar_df)
    try:
        conn.execute(
            """
            INSERT INTO seasonal_calendar
                (date, day_of_week, day_of_week_num, week_number, month, quarter,
                 year, is_holiday, season, marketing_event)
            SELECT date, day_of_week, day_of_week_num, week_number, month, quarter,
                   year, is_holiday, season, marketing_event
            FROM _cal_df
            ON CONFLICT DO NOTHING
            """
        )
    finally:
        conn.unregister("_cal_df")

    # --- weather_overlay (ETL-generated, direct to Gold) ---
    conn.register("_wx_df", weather_df)
    try:
        conn.execute(
            """
            INSERT INTO weather_overlay
                (region_id, date, temperature_avg, temperature_min, temperature_max,
                 precipitation, humidity, weather_condition, weather_alert)
            SELECT region_id, date, temperature_avg, temperature_min, temperature_max,
                   precipitation, humidity, weather_condition, weather_alert
            FROM _wx_df
            ON CONFLICT DO NOTHING
            """
        )
    finally:
        conn.unregister("_wx_df")

    counts = {
        "product_master": _table_count(conn, "product_master"),
        "regional_reference": _table_count(conn, "regional_reference"),
        "store_reference": _table_count(conn, "store_reference"),
        "seasonal_calendar": _table_count(conn, "seasonal_calendar"),
        "weather_overlay": _table_count(conn, "weather_overlay"),
    }
    logger.info("Gold dimensions: %s", counts)
    return counts


def promote(conn: duckdb.DuckDBPyConnection) -> int:
    """Promote FK-valid, non-orphan Silver rows into sales_transactions.

    Returns the number of newly inserted fact rows.
    """
    before = _table_count(conn, "sales_transactions")
    conn.execute(
        """
        INSERT INTO sales_transactions
            (transaction_id, sku_id, store_id, transaction_date, transaction_time,
             quantity, unit_price, total_amount, discount_amount)
        SELECT
            s.source_system || '-' || s.source_txn_id AS transaction_id,
            s.sku                                      AS sku_id,
            s.store_id,
            CAST(s.txn_timestamp AS DATE)              AS transaction_date,
            s.txn_timestamp                            AS transaction_time,
            s.quantity,
            s.unit_price,
            ROUND(s.quantity * s.unit_price, 2)        AS total_amount,
            s.discount_amount
        FROM silver_transactions s
        WHERE s.sku_unmatched = FALSE
          AND EXISTS (SELECT 1 FROM product_master pm   WHERE pm.sku_id = s.sku)
          AND EXISTS (SELECT 1 FROM store_reference st  WHERE st.store_id = s.store_id)
          AND EXISTS (SELECT 1 FROM seasonal_calendar sc WHERE sc.date = CAST(s.txn_timestamp AS DATE))
        ON CONFLICT DO NOTHING
        """
    )
    after = _table_count(conn, "sales_transactions")
    promoted = after - before
    logger.info("Gold: promoted %d new fact rows (sales_transactions now %d)", promoted, after)
    return promoted
