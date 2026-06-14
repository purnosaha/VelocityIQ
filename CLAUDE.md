# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Conventions

- All `.md` files (except this `CLAUDE.md`) must be created inside the `md/` directory at the project root.

## Commands

**Run the API server (local dev):**
```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Initialize the database schema:**
```bash
python scripts/init_db.py --db-path ./data/velocityiq.duckdb
# Use --force to skip the confirmation prompt on existing DBs
```

**Load sample data:**
```bash
python scripts/load_sample_data.py --rows 10000
# Adjust --rows for larger datasets (e.g., 50000, 100000)
```

**Run with Docker Compose:**
```bash
docker-compose up --build
```

**Install dependencies:**
```bash
uv sync
```

## Architecture

### Stack
- **FastAPI** (`main.py`) — HTTP API entrypoint; currently minimal (only `/health`).
- **DuckDB** — embedded OLAP database stored at `./data/velocityiq.duckdb` (configurable via `DUCKDB_PATH` env var).
- **uv** — package manager; use `uv run` or `uv sync` rather than pip.

### Database: Star Schema
The schema in `sql/init.sql` follows a classic star schema for analytical queries:

**Fact table**: `sales_transactions` — one row per sale; contains `sku_id`, `store_id`, `transaction_date` FKs plus quantity, pricing, and generated `net_amount` column.

**Dimensions**:
- `product_master` — SCD Type 2 (use `is_current = TRUE` to get the current record for any SKU; `effective_date`/`end_date` track history)
- `store_reference` → `regional_reference` (stores belong to regions)
- `seasonal_calendar` — time dimension with season, holiday, and marketing event attributes; covers June 14 2023–June 14 2026
- `weather_overlay` — optional dimension keyed on `(region_id, date)`; LEFT JOIN from sales to avoid excluding rows with missing weather data

**Pre-built analytics views** (`sql/init.sql`):
- `v_sales_by_product` — revenue/quantity aggregated per SKU
- `v_sales_by_store_region` — revenue per store + geographic join
- `v_daily_sales_summary` — daily aggregates with temporal/seasonal context
- `v_sales_weather_context` — sales joined to regional weather

**DuckDB macros** defined in `sql/init.sql`:
- `generate_transaction_id(store_id, transaction_date)` — generates a random unique ID
- `get_fiscal_quarter(month_val)` — returns Q1–Q4 integer

### Data Loading Pipeline
`scripts/load_sample_data.py` generates referential-integrity-safe data: it populates dimension tables first (`regional_reference` → `store_reference` → `product_master` → `seasonal_calendar` → `weather_overlay`), then the fact table. Transactions are distributed evenly across all days in the 3-year window to avoid FK violations on `seasonal_calendar`.

### Docker Setup
Two containers: `velocityiq-app` (FastAPI via uvicorn) and `velocityiq-duckdb` (schema/data init). Both share a `./data` volume mount; the database file path is passed via `DUCKDB_PATH`.
