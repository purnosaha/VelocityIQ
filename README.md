# VelocityIQ

## Overview

VelocityIQ is a **skeleton / template project** — a reference implementation of a retail sales intelligence platform. It demonstrates how to wire together a full analytics stack: a medallion ETL pipeline (Bronze → Silver → Gold), a DuckDB star-schema warehouse, a FastAPI analytics API with ML-powered forecasting, and an LLM-driven natural-language Q&A dashboard. It is intentionally kept simple and is meant as a starting point, not a production-ready system.

> **Why the name?** — **Velocity** refers to *sales velocity*: the rate and momentum at which products move. **IQ** refers to the *intelligence* layer: analytics, ML forecasting, and LLM-driven insights. Together, the name captures the platform's purpose: measure and accelerate sales intelligence.

---

## Scope

**What's included:**
- Medallion ETL pipeline (Bronze → Silver → Gold) with synthetic POS data from 3 sources, defect injection, and full reconciliation tracking
- DuckDB star schema with pre-built analytics views and DuckDB macros
- FastAPI backend with 14 endpoints: health, model training, NL insights, and 8 report endpoints
- Streamlit dashboard UI with an AI Q&A tab and pre-built report charts
- SARIMA + XGBoost ML models for seasonal, demand, and revenue forecasting
- Local LLM inference via Ollama (`qwen2.5:7b`) powering the NL-to-SQL insight endpoint
- Docker Compose orchestration for the entire stack

**Intentionally out of scope:**
- Authentication / authorization (all API endpoints are open)
- Production-grade scheduling (ETL is triggered manually — no Airflow / Prefect)
- Real external data sources (all data is synthetic)
- Multi-tenant or cloud deployment configs
- Monitoring, alerting, or observability tooling

---

## Risks & Known Limitations

| Risk | Detail |
|---|---|
| **No authentication** | All API endpoints are publicly accessible. Add an auth layer before any network exposure. |
| **Synthetic data only** | Models are trained on generated data. Accuracy on real data is unknown until retrained. |
| **DuckDB is single-writer** | Concurrent writes from ETL and the API will conflict. Add a connection pool or separate read/write paths for production. |
| **ETL is manually triggered** | No scheduler is bundled. Add Airflow, Prefect, or cron for production automation. |
| **Model drift not handled** | No automated retraining schedule or drift detection. Staleness will degrade forecast quality over time. |
| **Ollama memory requirement** | `qwen2.5:7b` requires ~6 GB RAM. The `/insight` endpoint will fail on low-memory hosts. |
| **No persistent observability** | Logs go to stdout only. Add structured logging + alerting before production use. |

---

## Architecture

```
Synthetic POS Sources (3)
        │
        ▼
   ┌─────────┐     audit columns      ┌──────────────────┐
   │  Bronze  │ ──────────────────►  │  silver_*  tables │
   │ (raw land)│     Pandera valid.   │  (dedup, flags)  │
   └─────────┘                        └────────┬─────────┘
                                               │ FK-valid rows
                                               ▼
                                    ┌──────────────────────┐
                                    │  Gold: sales_transactions  │
                                    │  (DuckDB star schema)      │
                                    └──────────┬───────────┘
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                        FastAPI            ML Models         Ollama
                        (main.py)       SARIMA + XGBoost   qwen2.5:7b
                              │
                              ▼
                        Streamlit UI
                        (streamlit_app.py)
```

The ETL pipeline populates a DuckDB star schema. FastAPI reads from DuckDB to serve analytical reports, ML forecasts, and LLM-generated insights. The Streamlit UI consumes the API.

---

## Quick Start (Docker)

**Prerequisites:** Docker and Docker Compose installed.

```bash
# Clone and start the full stack
git clone <repo-url>
cd VelocityIQ
docker compose up --build
```

Docker Compose handles startup ordering automatically. Once all containers are healthy:

| Service | URL |
|---|---|
| API (FastAPI) | http://localhost:8000 |
| Interactive API docs | http://localhost:8000/docs |
| Streamlit dashboard | http://localhost:8501 |
| Ollama LLM server | http://localhost:11434 |

**Run the ETL pipeline** (after the stack is up):

```bash
# Backfill 36 months of historical data (run once on first setup)
docker compose exec etl uv run python etl/backfill.py

# Load current month's data
docker compose exec etl uv run python etl/main.py
```

**Retrain ML models** after loading new data:

```bash
docker compose exec app uv run python scripts/forecast_model.py
docker compose exec app uv run python scripts/demand_model.py
docker compose exec app uv run python scripts/revenue_forecast_model.py
```

Or call the training endpoints directly:
```bash
curl -X POST http://localhost:8000/train_seasonal_forecast
curl -X POST http://localhost:8000/train_demand_model
curl -X POST http://localhost:8000/train_revenue_forecast
```

---

## Docker Containers

The stack is defined in [`docker-compose.yml`](docker-compose.yml) and consists of 7 services:

| Service | Port | Lifecycle | Purpose |
|---|---|---|---|
| `app` | 8000 | Long-running | FastAPI API server (uvicorn). Waits for `model-init` and `ollama-init` to complete before starting. |
| `streamlit` | 8501 | Long-running | Streamlit dashboard UI. Waits for `app` to be healthy. Connects to the API via `VELOCITYIQ_API=http://app:8000`. |
| `duckdb` | — | One-shot | Initialises the DuckDB schema by running `docker_init.py`. Exits after completion. |
| `etl` | — | Manual trigger | ETL worker container. Starts but sleeps (`sleep infinity`). Trigger runs manually via `docker compose exec etl ...`. |
| `model-init` | — | One-shot | Trains the initial SARIMA and XGBoost models on first startup. Exits after completion. Waits for `duckdb`. |
| `ollama` | 11434 | Long-running | Ollama LLM inference server. Serves the `qwen2.5:7b` model for the `/insight` endpoint. |
| `ollama-init` | — | One-shot | Pulls the `qwen2.5:7b` model into Ollama. Waits for `ollama` to be healthy, then exits. |

**Shared volumes:**
- `./data` — mounted into `app`, `streamlit`, `duckdb`, `etl`, and `model-init`; contains the DuckDB file
- `ollama_models` — named Docker volume persisting LLM weights across restarts

**Startup order:**
```
duckdb ──► model-init ──┐
                         ├──► app ──► streamlit
ollama ──► ollama-init ──┘
```

---

## ETL Pipeline

The pipeline follows the **medallion architecture**: Bronze (raw) → Silver (validated) → Gold (analytical).

All ETL code lives in the [`etl/`](etl/) directory.

### Scripts

| Script | What it does |
|---|---|
| [`etl/main.py`](etl/main.py) | Entrypoint for a single ETL cycle (current month). Orchestrates: schema ensure → seed dimensions → generate POS batch → bronze land → adapt → silver validate/dedup → gold promote → print reconciliation summary. Also triggers model retraining on completion. |
| [`etl/backfill.py`](etl/backfill.py) | Replays 36 months of history, oldest-first, using the same stage chain as `main.py`. Run once on initial setup. |
| [`etl/bronze.py`](etl/bronze.py) | Raw landing layer. Appends native source rows to per-source bronze tables with audit columns: `source_system`, `batch_id`, `ingested_at`, `row_hash`. |
| [`etl/silver.py`](etl/silver.py) | Validation and deduplication layer. Validates rows with Pandera schemas, flags `sku_unmatched` and `late_arrival`, deduplicates on `(source_system, source_txn_id)`. Rejected rows go to `silver_transactions_rejected`; dedup audit goes to `silver_transactions_dedup_audit`. |
| [`etl/gold.py`](etl/gold.py) | Promotes clean, FK-valid Silver rows into the `sales_transactions` fact table. Idempotent via `transaction_id`. Also loads dimension tables. |
| [`etl/adapter.py`](etl/adapter.py) | Declarative schema mapping layer. Reads YAML specs from `etl/mappings/` and translates each source's native field names and types into the canonical Silver contract. Supports computed fields (e.g. CAD→USD currency conversion for `online_channel`). |
| [`etl/generate_synthetic.py`](etl/generate_synthetic.py) | Generates synthetic POS data from 3 sources (`pos_system_a`, `pos_system_b`, `online_channel`) with deliberate defects: 5% NULLs, 2% duplicates, late arrivals, malformed dates, negative quantities, and orphan SKUs. |
| [`etl/config.py`](etl/config.py) | Central configuration: DB path, ETL constants (`ROWS_PER_MONTH=2500`, `BACKFILL_MONTHS=36`), date helpers, and source registry. |
| [`etl/schemas.py`](etl/schemas.py) | Pandera schema definitions used by `silver.py` for row-level validation. |

### Data Flow per Month

```
1. Schema       — apply Gold DDL from sql/init.sql (idempotent)
2. Dimensions   — generate + land product_master, region, calendar, weather (once)
3. Generate     — synthetic POS batch for the month across 3 sources
4. Bronze       — land raw rows with audit columns
5. Adapt        — translate native fields → canonical Silver columns (YAML mappings)
6. Silver       — validate, flag, deduplicate; write rejects to audit table
7. Gold         — promote clean rows to fact table
8. Summary      — print reconciliation metrics
```

### Reconciliation Identity

Every batch asserts:
```
rows_in == rows_loaded + rows_rejected + rows_deduped
```
A mismatch emits a warning.

### Audit Tables

| Table | Contents |
|---|---|
| `silver_transactions` | Clean, validated rows (PK: `source_system` + `source_txn_id`) |
| `silver_transactions_rejected` | Rows that failed Pandera validation, with `reject_reason` |
| `silver_transactions_dedup_audit` | Duplicate rows with kept/dropped metadata |
| `etl_runs` | Per-run metrics: `rows_in`, `rows_loaded`, `rows_rejected`, `rows_deduped` |

---

## API Reference

**Base URL:** `http://localhost:8000`  
**Interactive docs (Swagger UI):** `http://localhost:8000/docs`

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status": "ok"}`. Use to confirm the server is up. |

**Example:**
```bash
curl http://localhost:8000/health
```

---

### Model Training

All training endpoints are `POST` with no request body. They run synchronously and return `{"status", "model_path", "log_path", "summary"}`.

| Method | Path | Model | Description |
|---|---|---|---|
| `POST` | `/create_model` | XGBoost | Retrain the revenue-forecast XGBoost model with exponential-decay weighting on recent data. |
| `POST` | `/train_seasonal_forecast` | SARIMA | Retrain the seasonal revenue SARIMA model. Required before using `/reports/seasonal-forecast`. |
| `POST` | `/train_revenue_forecast` | SARIMA | Retrain per-(category, region) SARIMA models. Required before using `/reports/revenue-forecast`. |
| `POST` | `/train_demand_model` | XGBoost | Retrain the demand (quantity) XGBoost model. Unlocks ML insights in category-leakage and discount-effectiveness reports. |

**Example:**
```bash
curl -X POST http://localhost:8000/train_seasonal_forecast
```

---

### NL Insights (LLM-powered)

| Method | Path | Description |
|---|---|---|
| `POST` | `/insight` | Translate a natural-language question to DuckDB SQL via Ollama, execute it, and return a narrative summary. |

**Request body:**
```json
{
  "question": "Which product category has the highest discount rate?",
  "max_rows": 100
}
```

**Response:**
```json
{
  "question": "...",
  "sql": "SELECT ...",
  "summary": "3–5 sentence narrative generated by the LLM.",
  "data_points": [ { "column": "value" } ],
  "row_count": 12,
  "generated_at": "2025-01-15T10:30:00Z"
}
```

Only `SELECT` / `WITH` statements are permitted; write operations are blocked.

**Example questions:**
- `"Top 10 products by net revenue"`
- `"Monthly revenue trend for 2024"`
- `"How does weather affect sales in the North region?"`

---

### Reports

All report endpoints are `GET`. They read from pre-built DuckDB views.

#### `GET /reports/seasonal-trend`

Monthly revenue trend with year-over-year growth rates and season rollup.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `year` | int | — | Filter to a specific year |

**Response highlights:** `data_points[]` with `year`, `month`, `season`, `net_revenue`, `yoy_growth_pct`; `summary.season_rollup` with totals per season.

```bash
curl "http://localhost:8000/reports/seasonal-trend?year=2024"
```

---

#### `GET /reports/seasonal-forecast`

SARIMA forward revenue forecast with 95% confidence intervals.  
**Requires:** `POST /train_seasonal_forecast` run first.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `horizon` | int (1–10) | 3 | Months to forecast ahead |

**Response highlights:** `forecast[]` with `year`, `month`, `predicted_net_revenue`, `lower`, `upper`.

```bash
curl "http://localhost:8000/reports/seasonal-forecast?horizon=6"
```

---

#### `GET /reports/revenue-forecast`

Per-(category, region) SARIMA forecast with optional filters and an aggregate rollup.  
**Requires:** `POST /train_revenue_forecast` run first.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by product category (case-insensitive) |
| `region` | string | — | Filter by region name or ID (case-insensitive) |
| `horizon` | int (1–10) | 3 | Months to forecast ahead |

**Response highlights:** `slices[]` per (category, region) pair; `aggregate[]` summed across all slices.

```bash
curl "http://localhost:8000/reports/revenue-forecast?category=Electronics&horizon=3"
```

---

#### `GET /reports/revenue-by-category-region`

Historical actuals — monthly net revenue by product category × region. The actuals counterpart to `/reports/revenue-forecast`; useful for comparing forecasts against what actually happened.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by product category (case-insensitive) |
| `region` | string | — | Filter by region name or ID (case-insensitive) |
| `year` | int | — | Filter to a specific year |

**Response highlights:** `data_points[]` with `category`, `region_id`, `region_name`, `year`, `month`, `net_revenue`.

```bash
curl "http://localhost:8000/reports/revenue-by-category-region?category=Electronics&year=2024"
```

---

#### `GET /reports/category-leakage`

Gross vs net revenue by category. Shows how much revenue is eroded by discounts. Includes ML-powered opportunity revenue if the demand model is trained.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by category (case-insensitive) |
| `target_discount_rate` | float (0–1) | 0.0 | Counterfactual discount rate for opportunity analysis |

**Response highlights:** `data_points[]` with `total_revenue`, `net_revenue`, `total_discount`, `discount_rate`; `summary.highest_leakage_category`; `model_insights[]` with `opportunity_revenue_usd` per category.

```bash
curl "http://localhost:8000/reports/category-leakage?target_discount_rate=0.05"
```

---

#### `GET /reports/discount-effectiveness`

Average units sold per discount band (`0%`, `0-10%`, `10-20%`, `20%+`). Includes ML elasticity curves and custom-rate predictions if the demand model is trained.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by category (case-insensitive) |
| `target_discount_rate` | float (0–1) | — | Predict demand at a custom discount rate |

**Response highlights:** `data_points[]` per (category, band); `summary.higher_discount_lifts_quantity`; `elasticity[]` model vs SQL comparison; `elasticity_custom[]` custom-rate predictions.

```bash
curl "http://localhost:8000/reports/discount-effectiveness?target_discount_rate=0.15"
```

---

#### `GET /reports/concentration-risk`

SKU Pareto analysis — which SKUs drive 80% of revenue.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `top_n` | int (1–500) | 10 | Number of top SKUs to return |

**Response highlights:** `data_points[]` with `revenue_rank`, `product_name`, `net_revenue`, `revenue_pct`, `cumulative_revenue_pct`; `summary.skus_covering_80pct_revenue`.

```bash
curl "http://localhost:8000/reports/concentration-risk?top_n=20"
```

---

#### `GET /reports/concentration-risk/scenario`

Stress-test: what is the revenue impact if the top-N SKUs decline by `shock_pct` percent?

| Parameter | Type | Default | Description |
|---|---|---|---|
| `top_n` | int (1–500) | 10 | Number of top SKUs to shock |
| `shock_pct` | float (0–100) | 20.0 | Percentage revenue decline to simulate |

**Response highlights:** `baseline_total_revenue`, `scenario_total_revenue`, `revenue_impact_usd`, `revenue_impact_pct`.

```bash
curl "http://localhost:8000/reports/concentration-risk/scenario?top_n=10&shock_pct=30"
```

---

## Development (local, without Docker)

```bash
# Install dependencies
uv sync

# Start the API server (auto-reload on file changes)
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start the Streamlit UI
uv run streamlit run streamlit_app.py

# Initialise the DuckDB schema
python scripts/init_db.py --db-path ./data/velocityiq.duckdb
# Use --force to skip the confirmation prompt on an existing DB

# Generate and load sample data
python scripts/load_sample_data.py --rows 10000
# Adjust --rows for larger datasets (e.g., 50000, 100000)
```

The DuckDB file path defaults to `./data/velocityiq.duckdb` and can be overridden with the `DUCKDB_PATH` environment variable.

---

## Running Tests

```bash
# ETL unit tests (dedup, validation, adapter, gold grain, reconciliation)
uv run pytest tests/

# API integration tests (requires the API server to be running on port 8000)
uv run pytest api_tests/

# Lint
uv run ruff check .
```

---

## Project Structure

```
VelocityIQ/
├── main.py                  # FastAPI application — all API endpoints
├── streamlit_app.py         # Streamlit dashboard UI
├── docker-compose.yml       # Multi-container orchestration
├── Dockerfile               # App / ETL / Streamlit container image
├── Dockerfile.duckdb        # Schema-init container image
├── pyproject.toml           # Python project config (uv)
├── schema_erd.drawio        # Entity-relationship diagram (open in draw.io)
│
├── etl/                     # Medallion ETL pipeline
│   ├── main.py              # Single-month ETL entrypoint
│   ├── backfill.py          # 36-month historical backfill
│   ├── bronze.py            # Raw landing layer
│   ├── silver.py            # Validation and deduplication
│   ├── gold.py              # Fact table promotion
│   ├── adapter.py           # Declarative source-to-silver mapping
│   ├── generate_synthetic.py# Synthetic POS data generator
│   ├── config.py            # ETL configuration and constants
│   ├── schemas.py           # Pandera validation schemas
│   └── mappings/            # Per-source YAML mapping specs
│
├── scripts/                 # Utility scripts
│   ├── init_db.py           # Initialise DuckDB schema
│   ├── load_sample_data.py  # Load sample data (bypasses ETL)
│   ├── forecast_model.py    # Train SARIMA seasonal model
│   ├── demand_model.py      # Train XGBoost demand model
│   └── revenue_forecast_model.py  # Train per-(category,region) SARIMA
│
├── models/                  # Persisted trained models + manifests
├── data/                    # DuckDB database file (git-ignored)
├── logs/                    # Training run logs (git-ignored)
│
├── sql/
│   └── init.sql             # Star schema DDL, views, and DuckDB macros
│
├── tests/                   # ETL unit tests
├── api_tests/               # API integration tests
└── md/                      # Additional markdown documentation
```

---

## Architecture Decision Records (ADRs)

Key architectural decisions are documented in the [`md/`](md/) directory:

| ADR | Decision |
|---|---|
| [ADR-001](md/adr-001-duckdb-star-schema.md) | DuckDB with Star Schema as the Analytical Store |
| [ADR-002](md/adr-002-medallion-etl-multiformat.md) | Medallion ETL Architecture with Declarative Multi-Format Ingestion |
| [ADR-003](md/adr-003-sarima-forecasting.md) | SARIMA for Revenue Forecasting |
| [ADR-004](md/adr-004-ollama-local-llm.md) | Ollama over Cloud LLM APIs for the AI Insight Layer |

---

## Contributing

1. Fork the repository and create a feature branch.
2. Run `uv sync` to install dependencies.
3. Run `uv run pytest tests/` before submitting a PR.
4. Run `uv run ruff check .` to ensure code style passes.
5. Open a pull request with a clear description of the change.
