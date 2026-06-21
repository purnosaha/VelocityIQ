"""Dedup keeps the latest ingested_at; dropped duplicates are audited."""
from __future__ import annotations

from datetime import datetime

from conftest import TEST_MONTH, make_canonical

from etl import config, silver


def _run(conn, rows, run_id="run_test"):
    return silver.process_batch(
        conn, make_canonical(rows),
        target_month=TEST_MONTH, run_id=run_id, started_at=config.utcnow(),
    )


def test_within_batch_duplicate_keeps_latest_and_audits_dropped(conn):
    # Same (source_system, source_txn_id) re-sent; the later ingested_at wins.
    rows = [
        {"source_txn_id": "DUP", "batch_id": "early", "unit_price": 10.0,
         "ingested_at": datetime(2026, 3, 10, 9, 0, 0)},
        {"source_txn_id": "DUP", "batch_id": "late", "unit_price": 99.0,
         "ingested_at": datetime(2026, 3, 10, 9, 0, 1)},
    ]
    metrics = _run(conn, rows)

    assert metrics["rows_in"] == 2
    assert metrics["rows_deduped"] == 1
    assert metrics["rows_loaded"] == 1

    kept = conn.execute(
        "SELECT batch_id, unit_price FROM silver_transactions WHERE source_txn_id = 'DUP'"
    ).fetchall()
    assert kept == [("late", 99.0)]  # latest ingested_at survived

    audit = conn.execute(
        """
        SELECT source_txn_id, kept_batch_id, dropped_batch_id
        FROM silver_transactions_dedup_audit
        """
    ).fetchall()
    assert audit == [("DUP", "late", "early")]


def test_cross_batch_resend_dedupes_against_existing_silver(conn):
    # First batch loads the row; a later batch re-sends it with a newer ts.
    _run(conn, [{"source_txn_id": "X", "batch_id": "b1", "unit_price": 5.0,
                 "ingested_at": datetime(2026, 3, 1, 8, 0, 0)}], run_id="run1")
    _run(conn, [{"source_txn_id": "X", "batch_id": "b2", "unit_price": 7.0,
                 "ingested_at": datetime(2026, 3, 2, 8, 0, 0)}], run_id="run2")

    rows = conn.execute(
        "SELECT batch_id, unit_price FROM silver_transactions WHERE source_txn_id = 'X'"
    ).fetchall()
    assert rows == [("b2", 7.0)]  # exactly one row, the newer re-send
