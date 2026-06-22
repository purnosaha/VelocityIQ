# Production Readiness Classification

Per-component maturity assessment for VelocityIQ, intended for a team inheriting the skeleton. Each
component is rated, with what is already solid, what blocks production, and a concrete **"to
productionise"** checklist. The full risk register (with severity and mitigations) is in
[Risks & Known Limitations](risks.md).

## Classification scheme

| Level | Meaning |
|---|---|
| **Prototype** | Works end-to-end and is demo-grade, but has significant gaps; not safe for production. |
| **Beta — needs hardening** | Solid core with tests; specific, well-understood blockers remain. |
| **Production-ready** | Hardened, observable, and safe to expose. |

## Summary

| Component | Status |
|---|---|
| Ingestion / ETL | Beta — needs hardening |
| Model creation | Prototype |
| Insights / LLM | Prototype (experimental) |
| API layer | Beta — needs hardening |

---

## Ingestion / ETL

**Status: Beta — needs hardening**

**What's solid**
- Medallion pipeline (Bronze → Silver → Gold) with declarative, multi-format ingestion.
- Unit tests covering adapter mapping, dedup, Silver validation, Gold grain, and reconciliation.
- Pandera validation, a reconciliation identity assertion, and audit tables for rejected/deduped rows.
- Historical backfill plus per-month incremental loads.

**Gaps blocking production**
- Manually triggered (`docker compose exec`) — no scheduler.
- Hardcoded CAD→USD FX rate (`0.73`, `etl/config.py`).
- Single-writer DuckDB; ETL and API writes contend.
- No schema-migration strategy; timezone handling not addressed.

**To productionise (what to add)**
- [ ] Orchestrate loads with a scheduler (Airflow / Prefect / cron).
- [ ] Fetch live daily FX rates instead of the fixed constant.
- [ ] Adopt versioned schema migrations.
- [ ] Separate read/write DB paths (or move to a concurrent server DB).
- [ ] Normalise timestamps to UTC and persist zone metadata.

---

## Model creation

**Status: Prototype**

**What's solid**
- SARIMA revenue forecasting plus a demand model, trained from the curated Gold tables.
- **Model drift is handled**: the model is **retrained on every new data load** via the ETL pipeline
  trigger, so forecasts never run on stale data.
- On-demand training/retraining endpoints with structured error handling.

**Gaps blocking production**
- **No MLOps layer**, and the open risk is that **only one model artifact is stored** — each retrain
  **overwrites** the previous one. There is no versioning/registry, no rollback, and no drift or
  accuracy metrics, so a bad retrain is unrecoverable and silent.
- Trained on synthetic data; real-world accuracy unverified.
- `net_amount` regressor tautology biases some reports.
- Retrain runs synchronously (`subprocess.run()`), blocking the caller.

**To productionise (what to add)**
- [ ] Stand up an **MLOps** stack: model **registry + versioning** (e.g. MLflow).
- [ ] Retain **N historical model artifacts** with **rollback** (remove the single-model risk).
- [ ] Add **drift detection** and evaluation/accuracy metrics with monitoring + alerting.
- [ ] Promote new models via a gate (shadow / champion-challenger) rather than blind overwrite.
- [ ] Re-target the model off `net_amount` (to `quantity`) and retrain/validate on real data.
- [ ] Run retraining as an orchestrated background job (return `202` + status endpoint).

---

## Insights / LLM

**Status: Prototype (experimental)**

**What's solid**
- Natural-language → SQL via a local Ollama model (`qwen2.5:7b`); no external API keys or cost.
- SQL guardrails (forbidden-keyword blocklist) and HTTP error mapping (502/422).

**Gaps blocking production**
- Synchronous Ollama calls block FastAPI workers under load.
- No fallback/resilience if Ollama is unavailable.
- SQL validation is keyword-based, not AST-based — bypassable.
- No automated tests for `/insight`; `qwen2.5:7b` needs ~6 GB RAM.

**To productionise (what to add)**
- [ ] Use an async `httpx.AsyncClient` for Ollama calls.
- [ ] Add a fallback path, timeouts, and graceful degradation.
- [ ] Replace keyword filtering with AST-based, read-only SQL validation.
- [ ] Add a request audit log and pytest coverage (mocked Ollama).
- [ ] Document the memory requirement; offer a smaller-model fallback.

---

## API layer

**Status: Beta — needs hardening**

**What's solid**
- Report and forecast endpoints with structured `HTTPException` handling.
- Integration tests (`api_tests/`) covering the report and revenue-forecast endpoints.
- Config via environment variables (e.g. `DUCKDB_PATH`, Ollama settings).

**Gaps blocking production**
- **No authentication** — all endpoints are public (P0).
- **No request/access logging** (P0).
- No rate limiting (P1).
- `/health` is shallow — it does not verify DuckDB or Ollama.

**To productionise (what to add)**
- [ ] Add authentication (API key / JWT / session middleware).
- [ ] Add structured request/access logging (correlation IDs, status, latency).
- [ ] Add per-IP / per-key rate limiting.
- [ ] Deepen `/health` to ping DuckDB and Ollama.
