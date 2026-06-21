"""The reconciliation identity rows_in == loaded + rejected + deduped holds."""
from __future__ import annotations

from datetime import datetime

from conftest import KNOWN_SKUS, ORPHAN_SKU, TEST_MONTH, make_canonical

from etl import config, silver


def test_reconciliation_identity_on_mixed_batch(conn):
    rows = [
        {"source_txn_id": "ok-1", "sku": KNOWN_SKUS[0]},
        {"source_txn_id": "ok-2", "sku": KNOWN_SKUS[1]},
        {"source_txn_id": "orphan", "sku": ORPHAN_SKU},               # loaded (flagged)
        {"source_txn_id": "neg", "quantity": -1},                     # rejected
        {"source_txn_id": "nullprice", "unit_price": None},           # rejected
        {"source_txn_id": "DUP", "batch_id": "e", "ingested_at": datetime(2026, 3, 10, 9, 0, 0)},
        {"source_txn_id": "DUP", "batch_id": "l", "ingested_at": datetime(2026, 3, 10, 9, 0, 1)},  # deduped
    ]
    m = silver.process_batch(
        conn, make_canonical(rows),
        target_month=TEST_MONTH, run_id="run_recon", started_at=config.utcnow(),
    )

    assert m["rows_in"] == 7
    assert m["rows_rejected"] == 2
    assert m["rows_deduped"] == 1
    assert m["rows_loaded"] == 4  # ok-1, ok-2, orphan(flagged), DUP(latest)
    assert m["rows_in"] == m["rows_loaded"] + m["rows_rejected"] + m["rows_deduped"]
    assert m["status"] == "ok"


def test_etl_runs_row_persisted_and_balances(conn):
    rows = [{"source_txn_id": "a"}, {"source_txn_id": "b", "quantity": -1}]
    silver.process_batch(
        conn, make_canonical(rows),
        target_month=TEST_MONTH, run_id="run_persist", started_at=config.utcnow(),
    )
    rec = conn.execute(
        """
        SELECT rows_in, rows_loaded, rows_rejected, rows_deduped, status
        FROM etl_runs WHERE run_id = 'run_persist'
        """
    ).fetchone()
    rows_in, loaded, rejected, deduped, status = rec
    assert rows_in == loaded + rejected + deduped
    assert status == "ok"
