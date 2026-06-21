#!/usr/bin/env python3
"""VelocityIQ ETL backfill — loads the previous 36 months, one month at a time.

Reuses the exact same stage chain as ``etl/main.py`` (no separate logic path):
schema -> dimensions (once) -> for each historical month: generate -> bronze ->
adapt -> silver -> gold. Each month is its own batch/run, so per-month metrics
land in ``etl_runs`` and the reconciliation identity is asserted per month.

Run order is oldest-first so late-arriving records (dated in a prior month) and
cross-month re-sends dedupe against already-loaded history the way they would in
a real daily pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow ``uv run python etl/backfill.py`` (script mode) to import the ``etl`` package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from etl import config
from etl.main import ensure_schema, print_summary, run_month, seed_dimensions

logger = config.get_logger("etl.backfill")


def main() -> None:
    db = config.db_path()
    logger.info("Opening DuckDB (read-write): %s", db)
    conn = duckdb.connect(db)
    try:
        ensure_schema(conn)
        dim_counts = seed_dimensions(conn)

        months = config.backfill_month_starts()
        logger.info(
            "Backfilling %d months: %s … %s",
            len(months), months[0].strftime("%Y-%m"), months[-1].strftime("%Y-%m"),
        )
        runs = []
        for month in months:
            logger.info("Backfill month: %s", month.strftime("%Y-%m"))
            runs.append(run_month(conn, month))
    finally:
        conn.close()  # single-writer: release the file before any reader opens it

    print_summary(runs, dim_counts, db)


if __name__ == "__main__":
    main()
