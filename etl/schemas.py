"""Pandera validation schema for the canonical Silver layer.

Used with lazy validation so every failing row/column in a batch is reported at
once (not just the first). Checks rely on Pandera's default ``ignore_na=True``:
null handling is governed by each column's ``nullable`` flag, while the value
checks (``> 0``, ``>= 0``, not-in-future) apply only to non-null values.
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema


def _not_in_future(s: pd.Series) -> pd.Series:
    # A small grace window avoids false positives from clock skew between the
    # generator's timestamps and validation time.
    return s <= (pd.Timestamp.now() + pd.Timedelta(days=1))


silver_schema = DataFrameSchema(
    {
        "source_system": Column(nullable=False),
        "source_txn_id": Column(nullable=False),
        "txn_timestamp": Column(
            nullable=False,
            checks=Check(_not_in_future, error="txn_timestamp_in_future"),
        ),
        "sku": Column(nullable=False),
        "quantity": Column(
            nullable=False,
            checks=Check.gt(0, error="quantity_not_positive"),
        ),
        "unit_price": Column(
            nullable=False,
            checks=Check.ge(0, error="unit_price_negative"),
        ),
        "discount_amount": Column(
            nullable=True,
            checks=Check.ge(0, error="discount_negative"),
        ),
        "store_id": Column(nullable=False),
        "batch_id": Column(nullable=False),
        "ingested_at": Column(nullable=False),
    },
    strict=False,
    ordered=False,
    name="silver_transactions",
)
