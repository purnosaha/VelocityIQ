# ETL Pipeline — Bronze → Silver → Gold (ADR + Operations)

VelocityIQ's data foundation. Ingests three POS sources with deliberately
different native schemas, lands them raw, maps them through a declarative
adapter, validates/dedupes/quarantines into a canonical Silver layer, and
promotes only clean, FK-valid rows into the existing Gold star schema
(`sql/init.sql`, unchanged).

## How to run

```bash
# Local — current month only
uv run python etl/main.py

# Local — backfill the previous 36 months (one batch per month), then current
uv run python etl/backfill.py
uv run python etl/main.py

# Tests (no external services)
uv run pytest

# Docker — one-shot etl service (backfill + current month), then API/model
docker compose run --rm etl
docker compose up
```

The output DuckDB file is `./data/velocityiq.duckdb` (override with `DUCKDB_PATH`).

## Layers

| Layer  | Tables | Purpose |
|--------|--------|---------|
| Bronze | `bronze_pos_system_a`, `bronze_pos_system_b`, `bronze_online_channel`, `bronze_product_master`, `bronze_region_reference` | Raw landing, native columns + audit (`source_system, source_file, batch_id, ingested_at, row_hash`) |
| Silver | `silver_transactions` (+ `silver_transactions_rejected`, `silver_transactions_dedup_audit`, `etl_runs`) | Canonical, validated, deduped, flagged |
| Gold   | `sales_transactions` + the `product_master` / `regional_reference` / `store_reference` / `seasonal_calendar` / `weather_overlay` dimensions | Frozen dimensional model (existing schema) |

## Code map

```
etl/
  main.py              entrypoint — current month (top-to-bottom, no orchestrator)
  backfill.py          previous 36 months, one at a time (reuses main's stages)
  config.py            paths, constants (CAD/USD rate), date windows, id helpers
  generate_synthetic.py  3 POS sources (native, with defects) + dimension seeds
  bronze.py            raw landing + audit columns
  adapter.py           ONE shared YAML-driven mapping engine
  mappings/*.yaml      per-source declarative specs (native -> canonical)
  schemas.py           Pandera DataFrameSchema for Silver
  silver.py            validate / quarantine / dedup / flag / load + run metrics
  gold.py              idempotent dimension build + Silver -> Gold promotion
tests/                 pytest suite (temp DuckDB, no external services)
```

## Key decisions

**No orchestration framework (ADR).** The pipeline is plain sequential Python
called from a single entrypoint — no Airflow / Dagster / Prefect, no DAG, no
scheduler. For a local-first POC where speed and zero-setup runnability are the
priorities, an orchestrator adds operational weight with no payoff. `main.py`
reads top to bottom as the pipeline. Revisit if/when scheduling, retries, or
cross-machine fan-out become real requirements.

**Single-writer DuckDB.** The ETL job fully completes and closes its write
connection before any reader opens the file. Compose enforces this with the
chain `duckdb` (schema) → `etl` (writes) → `app` / `model-init` (read-only),
each a separate sequential container.

**Bad data never reaches Gold.** Every row that fails Pandera validation is
written to `silver_transactions_rejected` with a `reject_reason` before being
removed from the flow — never dropped silently. Dropped duplicates are recorded
in `silver_transactions_dedup_audit`. **Quarantine tables are the "separate
pipeline" for invalid data**: they are the hand-off sink. An automated
re-ingest/repair job that drains them is intentionally out of scope for the POC.

**Orphan SKUs are flagged, not dropped.** A transaction whose SKU is absent from
`product_master` is loaded into `silver_transactions` with `sku_unmatched =
TRUE` and is held back from Gold (the Gold `fk_sales_product` foreign key stays
intact). Downstream consumers can choose how to treat them.

**Reconciliation.** Each run asserts `rows_in == rows_loaded + rows_rejected +
rows_deduped` over the current batch's rows and records it in `etl_runs`. A
mismatch logs a WARNING (status `reconciliation_warning`) but never crashes the
pipeline.

**Dimensions.** `product_master` and the region/store reference flow Bronze →
Gold like the POS data. `seasonal_calendar` (pure date math) and
`weather_overlay` (synthetic) are generated directly into the Gold dimensions —
they have no meaningful "raw POS" form. All dimension loads use
`ON CONFLICT DO NOTHING`, so re-runs are idempotent.

**Idempotent & reproducible runs.** Gold promotion is keyed on a deterministic
`transaction_id = source_system || '-' || source_txn_id` with
`ON CONFLICT DO NOTHING`, so a transaction is never double-inserted. To make
re-runs a *true* no-op, synthetic generation is seeded deterministically by
month (`config.month_seed`): the native id space for a month is fixed
(`A-202606-00000…`), so without a stable seed each re-run would draw a fresh
random defect mix, flip which ids are clean vs rejected, and slowly accrete Gold
rows toward the full id space. Per-month seeding means re-running `etl/main.py`
or `etl/backfill.py` — or a second `docker compose up` against a persistent
`./data` volume — reproduces the identical batch and promotes `gold+0`. (Bronze
landing remains append-only by design; Silver dedup keeps the latest
`ingested_at`, so the canonical Silver/Gold state is unchanged on re-run.)

## Schema drift & extensibility

The three sources differ on purpose: US date strings + `$`-prefixed prices
(System A), ISO datetimes + integer cents (System B), and ISO-8601 + CAD
currency + `line_total` instead of unit price (online). All differences are
absorbed declaratively in `etl/mappings/*.yaml`; `adapter.py` has no
per-source branches.

**Adding a 4th POS source requires only:** a new `etl/mappings/<source>.yaml`
and one entry in `config.POS_SOURCES` — zero changes to adapter code.

## Injected defects (for demonstrating DQ handling)

The synthetic generator injects, per source/batch: ~5% nulls in non-key fields,
~2% duplicate (re-sent) transactions, a handful of late-arriving records (dated
in a prior month, flagged `late_arrival`), a few malformed/unparseable dates and
negative quantities (→ quarantined), and at least one orphan SKU (→ flagged).
Currency for the online channel is normalized CAD → USD using a fixed POC
constant (`config.CAD_USD_RATE`); production would source a live FX rate.
