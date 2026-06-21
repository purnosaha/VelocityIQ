# VelocityIQ — End-to-End Architectural Review

## Context

VelocityIQ is a local-first sales intelligence POC. It combines a DuckDB star-schema, a medallion ETL pipeline (Bronze→Silver→Gold), an LLM-powered natural-language query interface (Ollama + qwen2.5:7b), and an XGBoost revenue-forecasting model. The current branch (`etl`) just shipped the Bronze→Silver→Gold pipeline with backfill. This review assesses the full system — data layer, ETL, API, UI, ML, testing, security, and operations — and proposes a prioritised roadmap.

---

## System Map

```
┌─────────────────────────────────────────────────────────────────┐
│  docker-compose                                                  │
│                                                                 │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐  │
│  │  ollama │→  │app:8000  │←  │streamlit │   │ model-init  │  │
│  │  :11434 │   │ FastAPI  │   │  :8501   │   │  (one-shot) │  │
│  └─────────┘   └────┬─────┘   └──────────┘   └─────────────┘  │
│                     │                                           │
│              ┌──────▼──────┐                                    │
│              │  DuckDB     │ ← etl (backfill + current month)  │
│              │  star schema│                                    │
│              └─────────────┘                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## What's Working Well

| Area | Strength |
|------|----------|
| **ETL idempotency** | Deterministic `month_seed()` + Bronze append + Silver upsert + Gold `ON CONFLICT DO NOTHING` = true re-run no-ops |
| **Medallion separation** | Bronze (raw), Silver (clean/audited), Gold (analytics) are distinct with clear contracts |
| **Declarative adapter** | YAML specs decouple source-schema mapping from code; adding a 4th POS source = one new YAML file |
| **Pandera lazy validation** | Catches all violations in one pass; rejected rows go to audit table, never silently dropped |
| **Cross-batch dedup** | `ingested_at`/`batch_id` tiebreak handles re-sends across month boundaries correctly |
| **Reconciliation identity** | `rows_in == loaded + rejected + deduped` enforced per run |
| **Security basics** | SQL injection blocked via forbidden-keyword list + read-only DuckDB connections; Pydantic input validation |
| **Star schema design** | Pre-built views + composite indexes; SCD Type 2 shape on `product_master`; LEFT JOIN for `weather_overlay` |
| **Developer ergonomics** | `uv` for deterministic installs; Docker Compose for full-stack parity; comprehensive `md/` docs |

---

## Findings by Severity

### P0 — Blocks production hardening

**1. No API authentication**
- All 4 endpoints are publicly accessible; no API key, JWT, or session middleware.
- Risk: Any user on the network can call `/predict`, `/create_model`, or `/insight`.
- Fix: Add `Depends(verify_api_key)` FastAPI dependency; pull key from env var.

**2. No request/access logging in FastAPI**
- ETL has structured audit tables; API has nothing.
- Risk: Cannot audit usage, debug LLM failures, or detect abuse.
- Fix: Add `logging.getLogger("uvicorn.access")` middleware; emit request_id, latency, status code per request.

**3. Synchronous LLM calls block FastAPI workers**
- `/insight` calls Ollama twice (SQL gen + summary) with 180 s timeout inside a sync FastAPI handler.
- Risk: One slow Ollama call holds a uvicorn worker thread; under moderate load the API becomes unresponsive.
- Fix: Make `generate_sql_from_question` and `summarize_results` async with `httpx.AsyncClient`; FastAPI then awaits them without blocking.

---

### P1 — Significant gaps

**4. No rate limiting**
- No per-IP or per-key throttle on any endpoint.
- Risk: Resource exhaustion; Ollama inference is expensive.
- Fix: Add `slowapi` or FastAPI middleware with a sensible default (e.g., 10 req/min on `/insight`).

**5. LLM fallback / resilience**
- If Ollama is unavailable or returns malformed output, `/insight` raises an unhandled exception (returns HTTP 500).
- Fix: Wrap LLM calls in try/except; return a structured `{"error": "LLM unavailable"}` with HTTP 503; consider a circuit-breaker pattern.

**6. Hard-coded CAD→USD FX rate (0.73)**
- Lives in `etl/adapter.py`; never refreshed.
- Risk: FX exposure silently mis-prices all online-channel transactions over time.
- Fix: Pull daily rate from a free API (e.g., frankfurter.app) at backfill/ETL startup; cache in a config table in DuckDB.

**7. Timezone handling missing**
- `regional_reference.timezone` column is stored but never applied to `txn_timestamp`.
- Risk: Cross-region time-of-day and "midnight" boundary queries are incorrect.
- Fix: Store `txn_timestamp` as UTC; add a `txn_local_time` derived column in Silver using the region's IANA zone.

**8. Dashboard and Reports tabs are stubs**
- `streamlit_app.py` renders "Dashboard" and "Reports" tabs with "Coming soon" placeholders.
- Risk: Users see a broken/incomplete UI.
- Fix: Either wire up pre-built views (`v_sales_by_product`, `v_daily_sales_summary`) via Altair/Plotly charts, or remove the tabs until implemented.

---

### P2 — Technical debt

**9. No Gold-level change log**
- Silver has `_rejected` and `_dedup_audit` tables, but there is no record of what was promoted to Gold and when.
- Fix: Add `gold_promotion_log` table recording `(batch_id, transaction_id, promoted_at)` rows inside `gold.promote()`.

**10. Late arrivals promoted without quarantine**
- Rows with `late_arrival=TRUE` are flagged in Silver but still promoted to Gold unchanged.
- Risk: Monthly aggregation windows (e.g., `v_daily_sales_summary`) include transactions whose `txn_timestamp` is in a prior period, silently distorting historical numbers.
- Fix: Exclude `late_arrival=TRUE` rows from Gold promotion (leave in Silver); or add a `correction_period` column to the fact table for separate late-arrival reporting.

**11. `product_master` SCD Type 2 columns are unused**
- `effective_date`/`end_date`/`is_current` exist in schema; Gold promotion uses `is_current=TRUE` filter but never writes Type 2 history rows.
- Fix: Either commit to SCD Type 1 (drop the unused columns) or implement the SCD Type 2 merge in `gold.promote()`. Half-implemented patterns create confusion.

**12. Model retraining is a synchronous blocking call**
- `POST /create_model` calls `subprocess.run(["python", ...])` and waits for completion (can take minutes).
- Fix: Emit a 202 Accepted immediately, run the subprocess in a background thread, and expose a `/model_status` endpoint.

**13. No schema migration strategy**
- `etl/sql/init.sql` uses `CREATE TABLE IF NOT EXISTS`; changing a column type or adding NOT NULL requires manual intervention.
- Fix: Adopt a lightweight migration tool (e.g., `yoyo-migrations` or numbered `.sql` files applied at startup) so schema evolution is trackable and repeatable.

**14. `sql/` root directory is empty (dead code)**
- CLAUDE.md references `sql/init.sql` but actual schema lives in `etl/sql/init.sql`.
- Fix: Remove the empty `sql/` root directory, or add a comment redirect.

---

### P3 — Observability & testing

**15. No API test suite**
- ETL has 17 pytest tests; the FastAPI layer has zero.
- Fix: Add `tests/test_api.py` using `fastapi.testclient.TestClient`; mock Ollama with `httpx` respx; cover `/health`, `/predict`, `/insight`, and `/create_model`.

**16. No integration test (end-to-end ETL → API → query)**
- Each layer is tested in isolation.
- Fix: Add `tests/test_integration.py` that runs the full stack against a temp DuckDB and asserts `/insight` returns plausible results.

**17. No metrics / monitoring**
- No Prometheus endpoint, no health-check depth beyond `{"status": "ok"}`.
- Fix: Expose `/metrics` via `prometheus-fastapi-instrumentator`; extend `/health` to ping DuckDB and Ollama.

---

## Prioritised Roadmap

| Priority | Item | Effort |
|----------|------|--------|
| P0 | API auth (API key middleware) | S |
| P0 | FastAPI request logging | S |
| P0 | Async Ollama calls | M |
| P1 | Rate limiting (`slowapi`) | S |
| P1 | LLM error handling + 503 response | S |
| P1 | FX rate refresh from live API | M |
| P1 | Timezone-aware timestamps in Silver | M |
| P1 | Wire up Dashboard/Reports tabs or remove | M |
| P2 | Gold promotion log table | S |
| P2 | Quarantine late arrivals from Gold | S |
| P2 | Resolve SCD Type 2 vs Type 1 ambiguity | S |
| P2 | Async `/create_model` (202 + status endpoint) | M |
| P2 | Schema migration strategy | M |
| P2 | Remove empty `sql/` root dir | XS |
| P3 | FastAPI pytest suite | M |
| P3 | End-to-end integration test | M |
| P3 | `/metrics` endpoint + deep `/health` | S |

---

## Critical Files

| File | Role |
|------|------|
| `main.py` | All API endpoints (P0 auth, logging, async) |
| `etl/silver.py` | Late-arrival and timezone handling |
| `etl/gold.py` | Promotion log, late-arrival exclusion, SCD clarity |
| `etl/adapter.py` | FX rate hard-code |
| `etl/sql/init.sql` | Schema (SCD columns, migration baseline) |
| `streamlit_app.py` | Dashboard/Reports stubs |
| `scripts/retrain_model.py` | Blocking retraining |
| `tests/` | Coverage gaps |

---

## Verification

After each fix:
- `pytest tests/` — ETL suite must remain green
- `uv run uvicorn main:app --reload` + curl all 4 endpoints
- `docker-compose up --build` — full stack end-to-end smoke test
- For auth: confirm unauthenticated requests return 401
- For async: confirm `/insight` returns within 5 s under concurrent load (e.g., `ab -n 10 -c 5`)
- For late arrivals: query `SELECT COUNT(*) FROM gold.sales_transactions JOIN silver_transactions ON ... WHERE late_arrival = TRUE` — expect 0
