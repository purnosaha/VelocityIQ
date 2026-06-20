"""Shared pytest fixtures for the ETL test suite.

Each test gets a fresh temp-file DuckDB with the frozen Gold schema applied, the
ETL bookkeeping tables created, and a tiny fixed dimension seed (two known SKUs,
one physical + one online store, a short calendar). No external services.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

# Make the ``etl`` package importable when pytest runs from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl import config, generate_synthetic, silver  # noqa: E402

# Fixed test reference data.
KNOWN_SKUS = ["SKU-A", "SKU-B"]
ORPHAN_SKU = "SKU-ORPHAN"
PHYS_STORE = "STORE-PHYS"
ONLINE_STORE = "STORE-ONL"
REGION = "REGION-T"
TEST_MONTH = date(2026, 3, 1)


def _insert_df(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    conn.register("_seed_tmp", df[columns])
    try:
        col_list = ", ".join(columns)
        conn.execute(f"INSERT INTO {table} ({col_list}) SELECT * FROM _seed_tmp")
    finally:
        conn.unregister("_seed_tmp")


def _seed_dimensions(conn: duckdb.DuckDBPyConnection) -> None:
    today = date.today()
    conn.execute(
        "INSERT INTO regional_reference (region_id, region_name, country, created_date) VALUES (?, ?, ?, ?)",
        [REGION, "Test Region", "USA", today],
    )
    conn.execute(
        "INSERT INTO store_reference (store_id, store_name, region_id, store_type, created_date) VALUES (?, ?, ?, ?, ?)",
        [PHYS_STORE, "Test Physical", REGION, "physical", today],
    )
    conn.execute(
        "INSERT INTO store_reference (store_id, store_name, region_id, store_type, created_date) VALUES (?, ?, ?, ?, ?)",
        [ONLINE_STORE, "Test Online", REGION, "online", today],
    )
    for i, sku in enumerate(KNOWN_SKUS):
        conn.execute(
            """
            INSERT INTO product_master (sku_id, product_name, category, brand, list_price, is_current)
            VALUES (?, ?, ?, ?, ?, TRUE)
            """,
            [sku, f"Product {i}", "Electronics", "TestBrand", 10.0 + i],
        )
    # Calendar spanning the test month plus the prior month (for late arrivals).
    cal = generate_synthetic.generate_calendar(date(2026, 2, 1), date(2026, 3, 31))
    _insert_df(
        conn, "seasonal_calendar", cal,
        ["date", "day_of_week", "day_of_week_num", "week_number", "month",
         "quarter", "year", "is_holiday", "season", "marketing_event"],
    )


@pytest.fixture()
def conn(tmp_path) -> duckdb.DuckDBPyConnection:
    db_file = tmp_path / "test.duckdb"
    connection = duckdb.connect(str(db_file))
    connection.execute(config.SQL_INIT.read_text(encoding="utf-8"))
    silver.ensure_tables(connection)
    _seed_dimensions(connection)
    yield connection
    connection.close()


def make_canonical(rows: list[dict]) -> pd.DataFrame:
    """Build an adapted/canonical DataFrame from concise row dicts.

    Sensible defaults are filled so each test only specifies the fields it cares
    about. ``txn_timestamp`` accepts a datetime or an ISO string.
    """
    base_ts = datetime(2026, 3, 10, 12, 0, 0)
    out = []
    for i, r in enumerate(rows):
        rec = {
            "source_system": r.get("source_system", "pos_system_a"),
            "source_txn_id": r.get("source_txn_id", f"T-{i:04d}"),
            "txn_timestamp": r.get("txn_timestamp", datetime(2026, 3, 10, 9, 30)),
            "sku": r.get("sku", KNOWN_SKUS[0]),
            "quantity": r.get("quantity", 2),
            "unit_price": r.get("unit_price", 10.0),
            "discount_amount": r.get("discount_amount", 0.0),
            "store_id": r.get("store_id", PHYS_STORE),
            "batch_id": r.get("batch_id", "batch_1"),
            "ingested_at": r.get("ingested_at", base_ts),
        }
        out.append(rec)
    df = pd.DataFrame(out, columns=config.CANONICAL_COLUMNS)
    df["txn_timestamp"] = pd.to_datetime(df["txn_timestamp"], errors="coerce")
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], errors="coerce")
    return df
