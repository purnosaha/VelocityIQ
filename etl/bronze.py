"""Bronze layer — raw landing with audit columns.

Lands each source's native rows *as-is* (no type coercion beyond what DuckDB
needs to store the frame) and stamps every row with the standard audit columns:
``source_system, source_file, batch_id, ingested_at, row_hash``.

``ingested_at`` is assigned per row as a strictly increasing timestamp within a
landing so that the Silver dedup has a deterministic "latest" to keep when the
same transaction is re-sent inside one batch.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import duckdb
import pandas as pd

from etl import config

logger = config.get_logger("etl.bronze")

AUDIT_COLUMNS = ["source_system", "source_file", "batch_id", "ingested_at", "row_hash"]


def _row_hash(record: dict) -> str:
    """Stable sha256 over the raw native row (sorted keys, str-coerced)."""
    payload = json.dumps({k: ("" if v is None else str(v)) for k, v in record.items()}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def land(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    *,
    source_system: str,
    source_file: str,
    batch_id: str,
) -> int:
    """Append ``df`` to bronze ``table`` with audit columns. Returns rows landed.

    The bronze table is created on first use from the frame's native shape plus
    the audit columns. Append-only: re-running a batch simply lands more raw
    rows; Silver dedup keeps the layer honest.
    """
    if df.empty:
        logger.warning("No rows to land for %s", source_system)
        return 0

    raw = df.copy().reset_index(drop=True)
    record_dicts = raw.to_dict(orient="records")

    base_ts = config.utcnow()
    raw["source_system"] = source_system
    raw["source_file"] = source_file
    raw["batch_id"] = batch_id
    # Strictly increasing per-row ingested_at (microsecond steps) -> deterministic
    # dedup ordering even for rows that land in the same batch.
    raw["ingested_at"] = [base_ts + timedelta(microseconds=i) for i in range(len(raw))]
    raw["row_hash"] = [_row_hash(rec) for rec in record_dicts]

    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM raw WHERE 1=0")
    conn.execute(f"INSERT INTO {table} SELECT * FROM raw")
    logger.info("Bronze: landed %d rows into %s (batch %s)", len(raw), table, batch_id)
    return len(raw)
