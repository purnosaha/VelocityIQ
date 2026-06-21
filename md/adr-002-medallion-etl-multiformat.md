# ADR-002: Medallion ETL Architecture with Declarative Multi-Format Ingestion

**Status:** Accepted  
**Date:** 2026-06-22

## Context

CPG transaction data arrives from multiple heterogeneous POS systems, each with different date formats, price encodings, and currencies. Real engagements introduce new sources regularly. Bad data — nulls, duplicates, malformed dates, orphan SKUs, late-arriving records — must be handled explicitly without silent drops. The pipeline must be auditable, idempotent, and extendable without touching shared code.

## Options Considered

- **Single flat load:** Fast to implement, no layering. Fragile — any data quality issue corrupts the analytical layer directly. No audit trail.

- **Per-source hand-coded pipelines:** Each source gets its own ingestion script. Maintainable for 2–3 sources but becomes O(n) code duplication as new sources are added; no shared validation or deduplication logic.

- **Medallion architecture with declarative adapter:** Three named layers (Bronze → Silver → Gold) with a shared adapter driven by per-source YAML mapping specs. One `adapt()` code path for all sources; adding a source requires only a new YAML file.

## Decision

Medallion architecture with a YAML-driven declarative adapter.

**Bronze layer** — raw landing, append-only. Native columns are preserved exactly as received plus audit columns on every row: `source_system`, `source_file`, `batch_id`, `ingested_at` (monotonically increasing per-row timestamp), `row_hash` (SHA-256 of native row). Three bronze tables: `bronze_pos_system_a`, `bronze_pos_system_b`, `bronze_online_channel`.

**Silver layer** — canonical, validated, deduplicated. The adapter maps native columns to a 10-column canonical contract (`source_system`, `source_txn_id`, `txn_timestamp`, `sku`, `quantity`, `unit_price`, `discount_amount`, `store_id`, `batch_id`, `ingested_at`) using the YAML spec for each source. Pandera schema validation runs after adaptation; rejected rows land in `silver_transactions_rejected` with a `reject_reason` column — they are never silently dropped. Duplicates are resolved by keeping the latest `ingested_at` per `(source_system, source_txn_id)`; dropped rows are tracked in `silver_transactions_dedup_audit`. A reconciliation identity is asserted per batch: `rows_in == rows_loaded + rows_rejected + rows_deduped`.

**Gold layer** — FK-valid star schema rows only. Promotion uses `ON CONFLICT DO NOTHING`, making every run idempotent.

**Formats currently handled:**

| Source | Date format | Price encoding | Currency |
|---|---|---|---|
| POS System A | `MM/DD/YYYY HH:MM` | `$`-prefixed string | USD |
| POS System B | ISO-8601 | Integer cents (÷ 100) | USD |
| Online Channel | ISO-8601 | `line_total` ÷ quantity | CAD → USD (`CAD_USD_RATE = 0.73`) |

**Adding a 4th source** requires one new file (`etl/mappings/<source>.yaml`) and one entry in `config.POS_SOURCES`. Zero changes to `adapter.py` or any pipeline stage.

**No orchestration framework** (Airflow, Dagster, Prefect) was introduced — plain sequential Python keeps the skeleton runnable with `uv run python etl/main.py` and zero framework overhead. Revisit if production scheduling, retries, or cross-machine fan-out are required.

## Consequences

**Accepted trade-offs:**
- Synthetic defects are injected at bronze generation (~5% nulls, ~2% duplicates, ~5 late arrivals, ~3 malformed dates, ~3 negative quantities, ~2 orphan SKUs) to validate the quality pipeline. In production these would be real data quality events.
- `CAD_USD_RATE` is a hardcoded POC constant. Production would replace this with a live FX API call.
- No distributed execution — pipeline is single-process. Acceptable for the data volumes in scope.

**Benefits realised:**
- Full audit trail at every layer — rejected data is never lost; deduplication decisions are recorded.
- Reconciliation assertion surfaces pipeline drift immediately rather than silently producing wrong counts.
- Declarative extensibility — new source = new YAML, not new code.
- Idempotent runs — re-running the pipeline on the same data produces no duplicates in Gold.
