"""Central configuration for the VelocityIQ ETL pipeline.

Paths, constants, the canonical Silver column contract, the source registry,
and the small helpers for date windows / batch + run identifiers all live here
so the stage modules stay free of magic values.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ONE_DAY = timedelta(days=1)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETL_DIR = PROJECT_ROOT / "etl"
MAPPINGS_DIR = ETL_DIR / "mappings"
SQL_INIT = PROJECT_ROOT / "sql" / "init.sql"


def db_path() -> str:
    """Resolve the DuckDB file path (env override, else repo default)."""
    return os.environ.get("DUCKDB_PATH", str(PROJECT_ROOT / "data" / "velocityiq.duckdb"))


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# POC fixed FX rate. In production this would be fetched per-day from a live
# rates API (e.g. ECB / OpenExchangeRates); hard-coded here to keep the POC
# offline and deterministic.
CAD_USD_RATE = 0.73

# How far back the backfill reaches, in whole months.
BACKFILL_MONTHS = 36

# Approximate transaction volume produced per monthly batch, split across the
# three POS sources. Kept modest so the whole pipeline runs in seconds.
ROWS_PER_MONTH = 2500

# Defect injection rates (per source, per batch).
NULL_RATE = 0.05          # ~5% nulls in non-key fields
DUPLICATE_RATE = 0.02     # ~2% duplicate (re-sent) transactions
LATE_ARRIVAL_COUNT = 5    # handful of records dated in a prior month
MALFORMED_DATE_COUNT = 3  # rows with unparseable timestamps
NEGATIVE_QTY_COUNT = 3    # rows with negative quantity
ORPHAN_SKU_COUNT = 2      # rows whose SKU is absent from product_master

# Canonical Silver column contract. Extends the spec's list with
# ``discount_amount`` (a downstream model feature) per the agreed design.
CANONICAL_COLUMNS = [
    "source_system",
    "source_txn_id",
    "txn_timestamp",
    "sku",
    "quantity",
    "unit_price",
    "discount_amount",
    "store_id",
    "batch_id",
    "ingested_at",
]

# Source registry. Each POS source maps a native bronze table to its YAML spec.
# Extensibility point: a 4th POS source = one new entry here + one new YAML
# file, with zero changes to adapter.py.
POS_SOURCES = {
    "pos_system_a": "bronze_pos_system_a",
    "pos_system_b": "bronze_pos_system_b",
    "online_channel": "bronze_online_channel",
}


# --------------------------------------------------------------------------- #
# Date-window helpers
# --------------------------------------------------------------------------- #
def first_of_month(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, delta: int) -> date:
    """Return the first day of the month ``delta`` months from ``d``."""
    base = first_of_month(d)
    month_index = (base.year * 12 + (base.month - 1)) + delta
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def month_bounds(month_start: date) -> tuple[date, date]:
    """Return (first_day, last_day) for the calendar month of ``month_start``."""
    start = first_of_month(month_start)
    nxt = add_months(start, 1)
    return start, nxt - _ONE_DAY


def current_month_start(today: date | None = None) -> date:
    return first_of_month(today or date.today())


def backfill_month_starts(today: date | None = None) -> list[date]:
    """Oldest-first list of the previous ``BACKFILL_MONTHS`` month starts."""
    cur = current_month_start(today)
    return [add_months(cur, -n) for n in range(BACKFILL_MONTHS, 0, -1)]


def full_window(today: date | None = None) -> tuple[date, date]:
    """Inclusive date span covering backfill + current month.

    Used to seed seasonal_calendar / weather_overlay so every transaction date
    satisfies the Gold FK to seasonal_calendar.
    """
    cur = current_month_start(today)
    start = add_months(cur, -BACKFILL_MONTHS)
    _, end = month_bounds(cur)
    return start, end


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #
def make_batch_id(source: str, month_start: date) -> str:
    """Deterministic-ish batch id: source + month + short random suffix.

    The random suffix lets a re-send of the same month form a distinct batch
    (so dedup has something to choose between), while staying readable in logs.
    """
    return f"{source}_{month_start:%Y%m}_{uuid.uuid4().hex[:6]}"


def make_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"


def month_seed(month_start: date) -> int:
    """Stable RNG seed derived from a month.

    Synthetic POS data is generated over a *fixed* native-id space per month
    (e.g. ``A-202606-00000…``). Seeding generation deterministically by month
    makes a re-run of the same month reproduce the exact same batch, so the
    idempotent Gold promotion (``ON CONFLICT DO NOTHING`` on ``transaction_id``)
    is a true no-op on re-run rather than slowly accreting rows as a fresh
    random defect mix reshuffles which ids are clean. This matters for repeated
    ``docker compose up`` against a persistent volume.
    """
    return month_start.year * 100 + month_start.month


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def get_logger(name: str) -> logging.Logger:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        )
    return logging.getLogger(name)
