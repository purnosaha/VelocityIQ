"""Bad rows are quarantined (with a reason), never silently dropped."""
from __future__ import annotations

from datetime import datetime

from conftest import KNOWN_SKUS, ORPHAN_SKU, TEST_MONTH, make_canonical

from etl import config, silver


def _run(conn, rows):
    return silver.process_batch(
        conn, make_canonical(rows),
        target_month=TEST_MONTH, run_id="run_test", started_at=config.utcnow(),
    )


def test_defective_rows_go_to_rejected_not_silver(conn):
    rows = [
        {"source_txn_id": "ok-1", "sku": KNOWN_SKUS[0]},                 # valid
        {"source_txn_id": "neg-qty", "quantity": -3},                   # invalid: quantity <= 0
        {"source_txn_id": "null-price", "unit_price": None},            # invalid: null unit_price
        {"source_txn_id": "null-store", "store_id": None},              # invalid: null store_id
        {"source_txn_id": "bad-date", "txn_timestamp": "not-a-date"},   # invalid: NaT
    ]
    metrics = _run(conn, rows)

    assert metrics["rows_in"] == 5
    assert metrics["rows_rejected"] == 4
    assert metrics["rows_loaded"] == 1

    loaded_ids = {r[0] for r in conn.execute("SELECT source_txn_id FROM silver_transactions").fetchall()}
    assert loaded_ids == {"ok-1"}

    rejected = conn.execute(
        "SELECT source_txn_id, reject_reason FROM silver_transactions_rejected"
    ).fetchall()
    rejected_map = {sid: reason for sid, reason in rejected}
    assert set(rejected_map) == {"neg-qty", "null-price", "null-store", "bad-date"}
    # every rejected row carries a non-empty reason
    assert all(reason for reason in rejected_map.values())
    assert "quantity" in rejected_map["neg-qty"]


def test_orphan_sku_is_kept_in_silver_but_flagged(conn):
    rows = [
        {"source_txn_id": "known", "sku": KNOWN_SKUS[0]},
        {"source_txn_id": "orphan", "sku": ORPHAN_SKU},
    ]
    _run(conn, rows)
    flagged = dict(
        conn.execute("SELECT source_txn_id, sku_unmatched FROM silver_transactions").fetchall()
    )
    assert flagged == {"known": False, "orphan": True}  # orphan kept, not dropped


def test_late_arrival_flagged(conn):
    rows = [
        {"source_txn_id": "current", "txn_timestamp": datetime(2026, 3, 5, 10, 0)},
        {"source_txn_id": "late", "txn_timestamp": datetime(2026, 2, 14, 10, 0)},  # prior month
    ]
    _run(conn, rows)
    flagged = dict(
        conn.execute("SELECT source_txn_id, late_arrival FROM silver_transactions").fetchall()
    )
    assert flagged == {"current": False, "late": True}
