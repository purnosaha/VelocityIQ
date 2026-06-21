"""Gold fact grain: one row per transaction, correct measures, orphans excluded."""
from __future__ import annotations

from conftest import KNOWN_SKUS, ONLINE_STORE, ORPHAN_SKU, PHYS_STORE, TEST_MONTH, make_canonical

from etl import config, gold, silver


def _load_and_promote(conn, rows):
    silver.process_batch(
        conn, make_canonical(rows),
        target_month=TEST_MONTH, run_id="run_test", started_at=config.utcnow(),
    )
    return gold.promote(conn)


def test_grain_measures_and_orphan_exclusion(conn):
    rows = [
        {"source_txn_id": "g1", "sku": KNOWN_SKUS[0], "store_id": PHYS_STORE,
         "quantity": 2, "unit_price": 10.0, "discount_amount": 1.0},
        {"source_txn_id": "g2", "sku": KNOWN_SKUS[1], "store_id": ONLINE_STORE,
         "quantity": 3, "unit_price": 4.0, "discount_amount": 0.0},
        {"source_txn_id": "orphan", "sku": ORPHAN_SKU, "store_id": PHYS_STORE,
         "quantity": 1, "unit_price": 5.0},
    ]
    promoted = _load_and_promote(conn, rows)
    assert promoted == 2  # orphan held back

    total = conn.execute("SELECT COUNT(*) FROM sales_transactions").fetchone()[0]
    distinct = conn.execute("SELECT COUNT(DISTINCT transaction_id) FROM sales_transactions").fetchone()[0]
    assert total == distinct == 2  # no fan-out from dimension joins

    # orphan SKU never reaches Gold
    assert conn.execute(
        "SELECT COUNT(*) FROM sales_transactions WHERE sku_id = ?", [ORPHAN_SKU]
    ).fetchone()[0] == 0

    # measures: total_amount = qty*unit_price, net_amount = total - discount (generated)
    g1 = conn.execute(
        "SELECT quantity, unit_price, total_amount, discount_amount, net_amount "
        "FROM sales_transactions WHERE transaction_id LIKE '%g1'"
    ).fetchone()
    qty, price, total_amt, disc, net = g1
    assert (qty, float(price)) == (2, 10.0)
    assert float(total_amt) == 20.0
    assert float(net) == float(total_amt) - float(disc) == 19.0


def test_every_gold_sku_resolves_to_product_master(conn):
    rows = [{"source_txn_id": f"g{i}", "sku": KNOWN_SKUS[i % 2], "store_id": PHYS_STORE}
            for i in range(5)]
    _load_and_promote(conn, rows)
    dangling = conn.execute(
        """
        SELECT COUNT(*) FROM sales_transactions s
        LEFT JOIN product_master p ON s.sku_id = p.sku_id
        WHERE p.sku_id IS NULL
        """
    ).fetchone()[0]
    assert dangling == 0


def test_promotion_is_idempotent(conn):
    rows = [{"source_txn_id": "g1", "sku": KNOWN_SKUS[0], "store_id": PHYS_STORE}]
    _load_and_promote(conn, rows)
    second = gold.promote(conn)  # re-promote without new silver rows
    assert second == 0
    assert conn.execute("SELECT COUNT(*) FROM sales_transactions").fetchone()[0] == 1
