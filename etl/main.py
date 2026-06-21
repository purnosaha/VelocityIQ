#!/usr/bin/env python3
"""VelocityIQ ETL entrypoint — loads the CURRENT month.

Plain sequential Python by design — there is NO orchestration framework (no
Airflow / Dagster / Prefect, no DAG, no scheduler). This is a deliberate
architectural choice for a local-first POC, documented in md/etl_pipeline.md.
Read this file top to bottom: schema -> dimensions -> generate -> bronze ->
adapt -> silver (validate/dedupe/quarantine) -> gold (promote) -> summary.

The stage helpers (``seed_dimensions``, ``run_month``) are imported by
``etl/backfill.py`` so the historical loader runs the exact same chain.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Allow ``uv run python etl/main.py`` (script mode) to import the ``etl`` package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
import pandas as pd

from etl import adapter, bronze, config, generate_synthetic, gold, silver

logger = config.get_logger("etl.main")


def ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Apply the frozen Gold schema (idempotent — init.sql is all IF NOT EXISTS)."""
    conn.execute(config.SQL_INIT.read_text(encoding="utf-8"))
    silver.ensure_tables(conn)


def seed_dimensions(conn: duckdb.DuckDBPyConnection, today: date | None = None) -> dict:
    """Generate + land + build all Gold dimensions once (idempotent)."""
    dims = generate_synthetic.generate_dimensions(today)
    bronze.land(
        conn, "bronze_product_master", dims["product_master"],
        source_system="product_master",
        source_file="product_master.csv",
        batch_id=config.make_batch_id("product_master", config.current_month_start(today)),
    )
    bronze.land(
        conn, "bronze_region_reference", dims["region_reference"],
        source_system="region_reference",
        source_file="region_reference.csv",
        batch_id=config.make_batch_id("region_reference", config.current_month_start(today)),
    )
    return gold.build_dimensions(conn, dims["calendar"], dims["weather"])


def run_month(conn: duckdb.DuckDBPyConnection, month_start: date, *, seed: int | None = None) -> dict:
    """Run the full fact chain for one calendar month and return its metrics."""
    run_id = config.make_run_id()
    started_at = config.utcnow()

    # --- generate native POS data for the month ---
    # Default to a deterministic per-month seed so re-running a month reproduces
    # the same batch and Gold promotion stays a true no-op on re-run (the native
    # id space is fixed per month; a fresh random defect mix would otherwise let
    # previously-rejected ids become promotable and accrete rows). See
    # config.month_seed / md/etl_pipeline.md.
    if seed is None:
        seed = config.month_seed(month_start)
    batch = generate_synthetic.generate_pos_batch(month_start, seed=seed)

    # --- bronze land + adapt (per source) ---
    bronze_counts: dict[str, int] = {}
    adapted_frames = []
    for source, table in config.POS_SOURCES.items():
        batch_id = config.make_batch_id(source, month_start)
        bronze_counts[source] = bronze.land(
            conn, table, batch[source],
            source_system=source,
            source_file=f"{source}_{month_start:%Y%m}.csv",
            batch_id=batch_id,
        )
        landed = conn.execute(
            f"SELECT * FROM {table} WHERE batch_id = ?", [batch_id]
        ).fetchdf()
        adapted_frames.append(adapter.adapt(landed, source))

    adapted = pd.concat(adapted_frames, ignore_index=True)

    # --- silver: validate / quarantine / dedup / flag / load ---
    metrics = silver.process_batch(
        conn, adapted, target_month=month_start, run_id=run_id, started_at=started_at
    )

    # --- gold: promote clean, FK-valid rows ---
    metrics["rows_promoted_gold"] = gold.promote(conn)
    metrics["bronze_counts"] = bronze_counts
    return metrics


def print_summary(runs: list[dict], dim_counts: dict, db_path: str) -> None:
    print("\n" + "=" * 68)
    print("  VelocityIQ ETL — run summary")
    print("=" * 68)
    for m in runs:
        bc = m["bronze_counts"]
        print(
            f"  {m['target_month']:%Y-%m}  "
            f"bronze[A={bc.get('pos_system_a', 0)}, B={bc.get('pos_system_b', 0)}, "
            f"online={bc.get('online_channel', 0)}]  "
            f"in={m['rows_in']} loaded={m['rows_loaded']} "
            f"rejected={m['rows_rejected']} deduped={m['rows_deduped']} "
            f"-> gold+{m['rows_promoted_gold']}  [{m['status']}]"
        )
    if len(runs) > 1:
        agg = {k: sum(m[k] for m in runs) for k in ("rows_in", "rows_loaded", "rows_rejected", "rows_deduped", "rows_promoted_gold")}
        print("-" * 68)
        print(
            f"  TOTAL  in={agg['rows_in']} loaded={agg['rows_loaded']} "
            f"rejected={agg['rows_rejected']} deduped={agg['rows_deduped']} "
            f"-> gold+{agg['rows_promoted_gold']}"
        )
    print("-" * 68)
    print(f"  Gold dimensions: {dim_counts}")
    print(f"  Database: {db_path}")
    print("=" * 68 + "\n")


def main() -> None:
    db = config.db_path()
    logger.info("Opening DuckDB (read-write): %s", db)
    conn = duckdb.connect(db)
    try:
        ensure_schema(conn)
        dim_counts = seed_dimensions(conn)
        month = config.current_month_start()
        logger.info("Loading current month: %s", month.strftime("%Y-%m"))
        metrics = run_month(conn, month)
    finally:
        conn.close()  # single-writer: release the file before any reader opens it

    print_summary([metrics], dim_counts, db)

    # lazy import — keeps ETL import-light; scripts/ not on sys.path by default
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    import forecast_model
    logger.info("Retraining SARIMA seasonal forecast model...")
    forecast_model.train_and_save(db)


if __name__ == "__main__":
    main()
