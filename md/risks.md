# Risks & Known Limitations

A consolidated risk register for VelocityIQ. This is a **skeleton / reference** project; the entries
below are the gaps that must be closed before any production use. Severity follows a simple scale:

- **P0** — blocker for any network-exposed deployment
- **P1** — required for a reliable production service
- **P2** — quality / maintainability; address before scaling

Per-component maturity and the concrete "what to add" checklist live in
[Production Readiness Classification](production-readiness.md).

| ID | Risk | Severity | Component | Impact | Mitigation / next step |
|---|---|---|---|---|---|
| R1 | No authentication | P0 | API | All endpoints are publicly accessible. | Add API key / JWT / session middleware before any network exposure. |
| R2 | No request / access logging | P0 | API | No audit trail of who called what; hard to debug or detect abuse. | Add structured request logging (correlation IDs, status, latency). |
| R3 | No rate limiting | P1 | API | A single client can exhaust workers (esp. the LLM path). | Add per-IP / per-key throttling. |
| R4 | Shallow health check | P1 | API | `/health` returns `{"status":"ok"}` without checking dependencies. | Deepen `/health` to ping DuckDB and Ollama. |
| R5 | DuckDB is single-writer | P1 | Ingestion / API | Concurrent writes from ETL and the API conflict. | Separate read/write paths or a connection pool; consider a server DB for concurrency. |
| R6 | ETL is manually triggered | P1 | Ingestion | No bundled scheduler; loads run via `docker compose exec`. | Add Airflow / Prefect / cron orchestration. |
| R7 | Hardcoded CAD→USD FX rate `0.73` | P2 | Ingestion | Currency conversion is a fixed POC constant (`etl/config.py`). | Fetch live daily rates from an FX source. |
| R8 | No schema-migration strategy | P2 | Ingestion | Schema changes require manual intervention; no versioned migrations. | Introduce a migration tool / versioned DDL. |
| R9 | Timezone handling not addressed | P2 | Ingestion | Timestamps assume a single zone; cross-region correctness unverified. | Normalise to UTC at ingestion and store zone metadata. |
| R10 | No MLOps; only one model artifact stored | P1 | Model | Model drift itself **is** mitigated — the model is retrained on **every new data load** via the ETL trigger. The remaining risk is that each retrain **overwrites** the single stored artifact: no versioning/registry, no rollback, and no drift/accuracy metrics. A bad retrain is unrecoverable. | Add an MLOps layer: model registry + versioning (e.g. MLflow), retain N historical artifacts with rollback, and drift/evaluation metrics + monitoring. |
| R11 | Synthetic data only | P1 | Model | Models are trained on generated data; real-world accuracy is unknown. | Retrain and validate on real transaction data. |
| R12 | `net_amount` regressor tautology | P2 | Model | Using `net_amount` as a feature for revenue is self-predictive; biases some reports. | Re-target the model to `quantity` and recompute revenue downstream. |
| R13 | Synchronous / blocking retrain | P2 | Model | Retrain runs in-process via `subprocess.run()`, blocking the request. | Run retrain in a background task; return `202 Accepted` + a status endpoint. |
| R14 | Synchronous LLM calls | P1 | Insights | Ollama calls block FastAPI workers under load. | Use an async `httpx.AsyncClient` for Ollama requests. |
| R15 | No LLM fallback / resilience | P1 | Insights | If Ollama is unavailable the `/insight` endpoint fails hard. | Add a fallback path, timeouts, and graceful degradation. |
| R16 | Keyword-based SQL validation | P1 | Insights | NL→SQL guardrails are a forbidden-keyword list, not an AST check; bypassable. | Parse and validate the generated SQL (AST), allow-list read-only statements. |
| R17 | Ollama memory requirement (~6 GB) | P2 | Insights | `qwen2.5:7b` fails on low-memory hosts. | Document the requirement; offer a smaller model fallback. |
| R18 | `/insight` has no automated tests | P2 | Insights | The LLM path is only manually exercised. | Add pytest coverage (mocked Ollama) for `/insight`. |
| R19 | No persistent observability | P1 | All | Logs go to stdout only; no metrics or alerting. | Add structured logging, metrics, and alerting before production. |
| R20 | Streamlit UI stubs | P2 | UI | Some tabs are placeholders. | Complete or hide stubbed tabs. |
