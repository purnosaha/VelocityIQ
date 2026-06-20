"""Silver layer — canonical, validated transactions.

Per batch, in order:
  1. **Validate** with Pandera (lazy) -> failing rows go to ``_rejected`` with a
     reason; they are never dropped silently and never reach Gold.
  2. **Flag** ``sku_unmatched`` (SKU absent from product_master — kept, not
     dropped) and ``late_arrival`` (transaction dated before the batch month).
  3. **Dedup** on ``(source_system, source_txn_id)`` keeping the row with the
     latest ``ingested_at`` (tie-break: larger ``batch_id``); existing Silver
     rows for the same keys are considered too, so cross-month re-sends dedupe
     correctly. Dropped duplicates are written to ``_dedup_audit``.
  4. **Load** survivors into ``silver_transactions``.
  5. **Record** a row in ``_etl_runs`` and assert the reconciliation identity
     ``rows_in == rows_loaded + rows_rejected + rows_deduped`` (warn, don't crash).

Reconciliation counts the *current batch's* partition: a current-batch row
either loads, is rejected, or is deduped. Prior-batch rows that a re-send
supersedes are still audited in ``_dedup_audit`` but don't affect this run's
identity.
"""
from __future__ import annotations

import duckdb
import pandas as pd
from pandera.errors import SchemaErrors

from etl import config
from etl.schemas import silver_schema

logger = config.get_logger("etl.silver")

SILVER_COLUMNS = config.CANONICAL_COLUMNS + ["sku_unmatched", "late_arrival"]

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS silver_transactions (
        source_system   VARCHAR NOT NULL,
        source_txn_id   VARCHAR NOT NULL,
        txn_timestamp   TIMESTAMP NOT NULL,
        sku             VARCHAR NOT NULL,
        quantity        INTEGER NOT NULL,
        unit_price      DOUBLE NOT NULL,
        discount_amount DOUBLE NOT NULL DEFAULT 0,
        store_id        VARCHAR NOT NULL,
        batch_id        VARCHAR NOT NULL,
        ingested_at     TIMESTAMP NOT NULL,
        sku_unmatched   BOOLEAN NOT NULL DEFAULT FALSE,
        late_arrival    BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (source_system, source_txn_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS silver_transactions_rejected (
        source_system   VARCHAR,
        source_txn_id   VARCHAR,
        txn_timestamp   TIMESTAMP,
        sku             VARCHAR,
        quantity        DOUBLE,
        unit_price      DOUBLE,
        discount_amount DOUBLE,
        store_id        VARCHAR,
        batch_id        VARCHAR,
        ingested_at     TIMESTAMP,
        reject_reason   VARCHAR,
        rejected_at     TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS silver_transactions_dedup_audit (
        source_system      VARCHAR,
        source_txn_id      VARCHAR,
        kept_batch_id      VARCHAR,
        dropped_batch_id   VARCHAR,
        kept_ingested_at   TIMESTAMP,
        dropped_ingested_at TIMESTAMP,
        deduped_at         TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS etl_runs (
        run_id         VARCHAR,
        target_month   DATE,
        started_at     TIMESTAMP,
        completed_at   TIMESTAMP,
        rows_in        BIGINT,
        rows_loaded    BIGINT,
        rows_rejected  BIGINT,
        rows_deduped   BIGINT,
        status         VARCHAR
    )
    """,
]


def ensure_tables(conn: duckdb.DuckDBPyConnection) -> None:
    for ddl in _DDL:
        conn.execute(ddl)


def _append(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, columns: list[str]) -> int:
    if df.empty:
        return 0
    payload = df[columns]
    conn.register("_silver_tmp", payload)
    try:
        conn.execute(f"INSERT INTO {table} SELECT * FROM _silver_tmp")
    finally:
        conn.unregister("_silver_tmp")
    return len(payload)


# --------------------------------------------------------------------------- #
# Stage 1 — validation / quarantine
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, rejected). Rejected rows carry ``reject_reason``."""
    df = df.reset_index(drop=True)
    try:
        silver_schema.validate(df, lazy=True)
        return df, df.iloc[0:0].assign(reject_reason=pd.Series(dtype="string"))
    except SchemaErrors as exc:
        cases = exc.failure_cases
        # failure_cases has 'index' (row position), 'column', 'check'.
        cases = cases.dropna(subset=["index"])
        reasons: dict[int, list[str]] = {}
        for _, row in cases.iterrows():
            idx = int(row["index"])
            col = row.get("column")
            check = row.get("check")
            reasons.setdefault(idx, []).append(f"{col}:{check}")
        bad_idx = sorted(reasons)
        rejected = df.loc[bad_idx].copy()
        rejected["reject_reason"] = ["; ".join(reasons[i]) for i in bad_idx]
        valid = df.drop(index=bad_idx).reset_index(drop=True)
        return valid, rejected


def write_rejected(conn: duckdb.DuckDBPyConnection, rejected: pd.DataFrame) -> int:
    if rejected.empty:
        return 0
    out = rejected.copy()
    out["rejected_at"] = config.utcnow()
    cols = config.CANONICAL_COLUMNS + ["reject_reason", "rejected_at"]
    n = _append(conn, "silver_transactions_rejected", out, cols)
    logger.info("Silver: quarantined %d rows -> silver_transactions_rejected", n)
    return n


# --------------------------------------------------------------------------- #
# Stage 2 — flags
# --------------------------------------------------------------------------- #
def add_flags(conn: duckdb.DuckDBPyConnection, valid: pd.DataFrame, target_month) -> pd.DataFrame:
    if valid.empty:
        valid = valid.copy()
        valid["sku_unmatched"] = pd.Series(dtype="bool")
        valid["late_arrival"] = pd.Series(dtype="bool")
        return valid

    known = conn.execute("SELECT sku_id FROM product_master").fetchdf()["sku_id"].tolist()
    out = valid.copy()
    out["sku_unmatched"] = ~out["sku"].isin(known)
    target_period = pd.Period(config.first_of_month(target_month), freq="M")
    out["late_arrival"] = out["txn_timestamp"].dt.to_period("M") < target_period
    return out


# --------------------------------------------------------------------------- #
# Stage 3 — dedup
# --------------------------------------------------------------------------- #
def dedup(conn: duckdb.DuckDBPyConnection, valid: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Return (survivors_to_write, dedup_audit_rows, rows_deduped_current).

    ``rows_deduped_current`` counts only current-batch rows dropped, for the
    per-run reconciliation identity.
    """
    if valid.empty:
        return valid.assign(_origin="current"), pd.DataFrame(), 0

    current = valid.copy()
    current["_origin"] = "current"

    keys = current[["source_system", "source_txn_id"]].drop_duplicates()
    conn.register("_dedup_keys", keys)
    try:
        existing = conn.execute(
            """
            SELECT s.* FROM silver_transactions s
            JOIN _dedup_keys k
              ON s.source_system = k.source_system
             AND s.source_txn_id = k.source_txn_id
            """
        ).fetchdf()
    finally:
        conn.unregister("_dedup_keys")
    existing["_origin"] = "existing"

    combined = pd.concat([existing, current], ignore_index=True, sort=False)
    combined["_ord"] = range(len(combined))  # stable arrival order for tie-breaks

    # Survivor per key: latest ingested_at, then larger batch_id, then latest arrival.
    ranked = combined.sort_values(
        ["ingested_at", "batch_id", "_ord"], ascending=[False, False, False]
    )
    survivor_pos = ranked.groupby(["source_system", "source_txn_id"], sort=False).head(1)
    survivor_set = set(survivor_pos["_ord"])

    survivors = combined[combined["_ord"].isin(survivor_set)].copy()
    dropped = combined[~combined["_ord"].isin(survivor_set)].copy()

    # Build dedup-audit rows (one per dropped row, paired with its key's survivor).
    audit_rows = []
    kept_by_key = {
        (r["source_system"], r["source_txn_id"]): r for _, r in survivor_pos.iterrows()
    }
    now = config.utcnow()
    for _, d in dropped.iterrows():
        kept = kept_by_key[(d["source_system"], d["source_txn_id"])]
        audit_rows.append(
            {
                "source_system": d["source_system"],
                "source_txn_id": d["source_txn_id"],
                "kept_batch_id": kept["batch_id"],
                "dropped_batch_id": d["batch_id"],
                "kept_ingested_at": kept["ingested_at"],
                "dropped_ingested_at": d["ingested_at"],
                "deduped_at": now,
            }
        )
    audit_df = pd.DataFrame(audit_rows)

    rows_deduped_current = int(((dropped["_origin"] == "current")).sum())
    return survivors, audit_df, rows_deduped_current


def write_dedup_audit(conn: duckdb.DuckDBPyConnection, audit_df: pd.DataFrame) -> int:
    if audit_df.empty:
        return 0
    cols = [
        "source_system", "source_txn_id", "kept_batch_id", "dropped_batch_id",
        "kept_ingested_at", "dropped_ingested_at", "deduped_at",
    ]
    n = _append(conn, "silver_transactions_dedup_audit", audit_df, cols)
    logger.info("Silver: recorded %d dropped duplicates -> silver_transactions_dedup_audit", n)
    return n


def load_silver(conn: duckdb.DuckDBPyConnection, survivors: pd.DataFrame) -> int:
    """Replace the batch's keys in silver_transactions with the survivors.

    Returns the number of *current-batch* rows written (for reconciliation).
    """
    if survivors.empty:
        return 0

    out = survivors.copy()
    out["quantity"] = out["quantity"].round().astype("int64")
    out["unit_price"] = out["unit_price"].astype(float)
    out["discount_amount"] = out["discount_amount"].astype(float)
    out["sku_unmatched"] = out["sku_unmatched"].astype(bool)
    out["late_arrival"] = out["late_arrival"].astype(bool)

    keys = out[["source_system", "source_txn_id"]].drop_duplicates()
    conn.register("_load_keys", keys)
    try:
        conn.execute(
            """
            DELETE FROM silver_transactions s
            USING _load_keys k
            WHERE s.source_system = k.source_system
              AND s.source_txn_id = k.source_txn_id
            """
        )
    finally:
        conn.unregister("_load_keys")

    _append(conn, "silver_transactions", out, SILVER_COLUMNS)
    rows_loaded_current = int((out["_origin"] == "current").sum()) if "_origin" in out else len(out)
    logger.info("Silver: loaded %d survivor rows into silver_transactions", len(out))
    return rows_loaded_current


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def process_batch(
    conn: duckdb.DuckDBPyConnection,
    adapted: pd.DataFrame,
    *,
    target_month,
    run_id: str,
    started_at,
) -> dict:
    ensure_tables(conn)
    rows_in = len(adapted)

    valid, rejected = validate(adapted)
    rows_rejected = write_rejected(conn, rejected)

    valid = add_flags(conn, valid, target_month)

    survivors, audit_df, rows_deduped = dedup(conn, valid)
    write_dedup_audit(conn, audit_df)

    rows_loaded = load_silver(conn, survivors)

    status = "ok"
    if rows_in != rows_loaded + rows_rejected + rows_deduped:
        status = "reconciliation_warning"
        logger.warning(
            "Reconciliation mismatch for run %s: rows_in=%d != loaded=%d + rejected=%d + deduped=%d",
            run_id, rows_in, rows_loaded, rows_rejected, rows_deduped,
        )

    metrics = {
        "run_id": run_id,
        "target_month": config.first_of_month(target_month),
        "started_at": started_at,
        "completed_at": config.utcnow(),
        "rows_in": rows_in,
        "rows_loaded": rows_loaded,
        "rows_rejected": rows_rejected,
        "rows_deduped": rows_deduped,
        "status": status,
    }
    conn.register("_run_row", pd.DataFrame([metrics]))
    try:
        conn.execute(
            """
            INSERT INTO etl_runs
            SELECT run_id, target_month, started_at, completed_at,
                   rows_in, rows_loaded, rows_rejected, rows_deduped, status
            FROM _run_row
            """
        )
    finally:
        conn.unregister("_run_row")
    return metrics
