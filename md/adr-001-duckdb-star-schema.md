# ADR-001: DuckDB with Star Schema as the Analytical Store

**Status:** Accepted  
**Date:** 2026-06-22

## Context

VelocityIQ requires an analytical data store that can handle complex aggregation queries across sales transactions, product dimensions, and time — while remaining operationally simple enough to run inside a Docker container without external services. The store must be queryable by both the FastAPI backend and ML training scripts, and must support the full star schema needed for OLAP reporting.

## Options Considered

- **PostgreSQL:** Designed primarily for transactional (OLTP) workloads. Analytical queries are supported, but as a row-store, Postgres must read full tuples even when a query touches only a few columns. Columnar engines such as DuckDB offer superior scan efficiency and compression for OLAP workloads, particularly when most queries aggregate a subset of columns across large datasets — column pruning and per-column compression (dictionary/RLE) reduce I/O and improve cache locality in exactly that access pattern.

- **SQLite:** Embedded and lightweight but row-oriented and not optimized for analytical workloads.

- **BigQuery / Snowflake:** Highly scalable cloud warehouses but require network access and incur operational and egress costs.

- **DuckDB:** Embedded columnar OLAP engine with zero server administration.

## Decision

DuckDB was selected because its columnar storage and vectorized execution provide excellent analytical query performance on medium-sized datasets, while remaining file-based and operationally simple. It supports SQL window functions, joins, COPY, and native Parquet, CSV, and Arrow integration.

**Schema:** A dimensional star schema consisting of one fact table (`sales_transactions`) and five dimensions:

| Table | Type | Notes |
|---|---|---|
| `product_master` | Dimension | SCD Type 2 — tracks price and metadata history via `effective_date` / `end_date` / `is_current` |
| `store_reference` | Dimension | Store-to-region mapping, store type (online / physical) |
| `regional_reference` | Dimension | Region demographics, timezone, income level |
| `seasonal_calendar` | Dimension | Date spine covering 2023–2026; season, holiday, marketing event attributes |
| `weather_overlay` | Dimension | Optional; keyed on `(region_id, date)`; LEFT JOINed to avoid excluding rows with missing weather data |

Pre-built analytical views (`v_sales_by_product`, `v_sales_by_store_region`, `v_daily_sales_summary`, `v_sales_weather_context`, `v_monthly_sales_by_category_region`) avoid repeated complex JOINs at query time.

## Consequences

**Accepted trade-offs:**
- Single-writer constraint — DuckDB does not support concurrent writers. The ETL pipeline must complete and close its connection before the API or model scripts open theirs. Enforced via Docker Compose `depends_on` ordering.
- No horizontal read scaling — a single file-based database cannot be distributed across nodes. Acceptable at POC scale; revisit if dataset exceeds ~100M rows or concurrent read load increases significantly.

**Benefits realised:**
- Zero server administration — no Postgres instance to provision, back up, or tune.
- Columnar compression — dictionary and RLE encoding on dimension columns significantly reduces storage footprint.
- Full SQL surface — window functions, CTEs, `COPY`, `UNNEST`, and Parquet/CSV/Arrow I/O work out of the box.
- Portable — the entire database is a single file (`./data/velocityiq.duckdb`) mountable as a Docker volume.
