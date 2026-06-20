"""The shared adapter applies each source's YAML mapping correctly."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from etl import adapter, config


def _with_audit(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.copy()
    df["source_system"] = source
    df["source_file"] = f"{source}.csv"
    df["batch_id"] = "batch_1"
    df["ingested_at"] = datetime(2026, 3, 10, 12, 0, 0)
    df["row_hash"] = "hash"
    return df


def test_pos_system_a_us_dates_and_currency_string():
    native = _with_audit(
        pd.DataFrame(
            [{"txn_id": "A-1", "ts": "03/10/2026 14:30", "sku": "SKU-A",
              "qty": 3, "unit_price": "$19.99", "disc": 1.50, "store_id": "STORE-PHYS"}]
        ),
        "pos_system_a",
    )
    out = adapter.adapt(native, "pos_system_a")
    row = out.iloc[0]
    assert list(out.columns) == config.CANONICAL_COLUMNS
    assert row["source_txn_id"] == "A-1"
    assert row["txn_timestamp"] == pd.Timestamp("2026-03-10 14:30:00")
    assert row["unit_price"] == pytest.approx(19.99)
    assert row["discount_amount"] == pytest.approx(1.50)
    assert row["store_id"] == "STORE-PHYS"


def test_pos_system_b_iso_dates_and_integer_cents():
    native = _with_audit(
        pd.DataFrame(
            [{"transaction_ref": "B-1", "sale_dt": "2026-03-10 08:15:00", "item_code": "SKU-B",
              "units": 2, "price_cents": 2599, "discount_cents": 300, "location": "STORE-PHYS"}]
        ),
        "pos_system_b",
    )
    out = adapter.adapt(native, "pos_system_b")
    row = out.iloc[0]
    assert row["source_txn_id"] == "B-1"
    assert row["txn_timestamp"] == pd.Timestamp("2026-03-10 08:15:00")
    assert row["unit_price"] == pytest.approx(25.99)   # cents -> dollars
    assert row["discount_amount"] == pytest.approx(3.00)
    assert row["store_id"] == "STORE-PHYS"


def test_online_channel_cad_to_usd_and_unit_price_backcalc():
    # 4 units, CAD line total 100.0 -> USD line total 73.0 -> unit price 18.25.
    native = _with_audit(
        pd.DataFrame(
            [{"order_id": "ONL-1", "created_at": "2026-03-10T19:45:00", "product_sku": "SKU-A",
              "qty_ordered": 4, "line_total": 100.0, "discount": 10.0,
              "currency": "CAD", "store_id": "STORE-ONL"}]
        ),
        "online_channel",
    )
    out = adapter.adapt(native, "online_channel")
    row = out.iloc[0]
    assert row["source_txn_id"] == "ONL-1"
    assert row["txn_timestamp"] == pd.Timestamp("2026-03-10 19:45:00")
    assert row["unit_price"] == pytest.approx(round(100.0 * config.CAD_USD_RATE / 4, 2))
    assert row["discount_amount"] == pytest.approx(10.0 * config.CAD_USD_RATE)
    assert row["store_id"] == "STORE-ONL"


def test_malformed_date_becomes_nat_not_error():
    native = _with_audit(
        pd.DataFrame(
            [{"txn_id": "A-bad", "ts": "not-a-date", "sku": "SKU-A",
              "qty": 1, "unit_price": "$5.00", "disc": 0, "store_id": "STORE-PHYS"}]
        ),
        "pos_system_a",
    )
    out = adapter.adapt(native, "pos_system_a")
    assert pd.isna(out.iloc[0]["txn_timestamp"])  # coerced, not raised


def test_null_discount_coalesced_to_zero():
    native = _with_audit(
        pd.DataFrame(
            [{"txn_id": "A-2", "ts": "03/10/2026 10:00", "sku": "SKU-A",
              "qty": 1, "unit_price": "$5.00", "disc": None, "store_id": "STORE-PHYS"}]
        ),
        "pos_system_a",
    )
    out = adapter.adapt(native, "pos_system_a")
    assert out.iloc[0]["discount_amount"] == 0.0
