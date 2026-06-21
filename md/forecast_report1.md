# Plan: Report 1 — Seasonal Revenue Trend & Forecast (add a separate SARIMA model)

## Context

Report 1 (`GET /reports/seasonal-trend`) today returns **only historical** monthly
revenue + YoY growth — there is **no forecast**, despite the report being named
"…& Forecast". The existing XGBoost model (`scripts/retrain_model.py` → `XGBRegressor`
for per-transaction `net_amount`) can't fill the gap: it's transaction-grained, and
trees produce piecewise-constant output bounded by the training range, so they
structurally cannot project a rising revenue series forward.

So we add a **separate, self-contained SARIMA time-series model** for Report 1.
Decisions locked with the user:

- **XGBoost is kept as dormant, reusable code.** `scripts/retrain_model.py` and
  `POST /create_model` stay in the repo **untouched**, but XGBoost is **NOT trained or
  retrained anywhere** — not at startup, not in ETL, not automatically. It's left for
  possible reuse later. **Only `POST /predict` + `PredictRequest` are removed.**
- **SARIMA is fully separate** (new `scripts/forecast_model.py`, new artifacts) and is
  the **only** model that gets trained/retrained.
- **SARIMA trains/retrains in three places:**
  1. **Docker startup** — the one-shot `model-init` service (re-pointed from XGBoost to SARIMA).
  2. **ETL run** — a training step at the end of `etl/main.py`.
  3. **On-demand HTTP** — `POST /train_seasonal_forecast`.

Scope: **Report 1 only.** No Streamlit changes. Reports 2/5/8 out of scope.

## Why SARIMA

~36 monthly points (Jun 2023–Jun 2026), seasonal period **s = 12** → ~3 cycles. Too few
for tree/feature ML; right-sized for a low-order SARIMA, which extrapolates trend (via
`d`/`D` differencing), captures yearly seasonality with few parameters, and returns
confidence intervals for free.

## Changes

### 1. Dependency — `pyproject.toml`
Add `statsmodels>=0.14` to `[project].dependencies`. Run `uv sync`.

### 2. New training module — `scripts/forecast_model.py`
Standalone module mirroring the structure/logging of `scripts/retrain_model.py`
(extract → fit → save → log). `retrain_model.py` itself is **not modified**.

- **Constants:** `MODEL_PATH = "./models/seasonal_forecast_latest.pkl"`,
  `LOG_PATH = "./logs/forecast_retrain_log.json"`, `SEASONAL_PERIOD = 12`,
  `DEFAULT_HORIZON = 3`, `DEFAULT_DB_PATH` from `DUCKDB_PATH`.
- **`extract_series(db_path)`** — read-only DuckDB; pull monthly `net_revenue` from
  `v_monthly_sales_summary` ordered by `year, month`. Build a pandas Series indexed by
  **month-start datetime, explicit `freq="MS"`**. **Exclude the current partial month**
  (`< DATE_TRUNC('month', CURRENT_DATE)`) so the trend isn't dragged down.
- **`fit(series)`** — `SARIMAX(series, order, seasonal_order=(P,D,Q,12),
  enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)`. Pick orders
  by a small AIC grid over low candidates; try/except with a **safe fallback**
  `order=(1,1,1), seasonal_order=(0,1,1,12)` if a candidate fails to converge.
- **`save(results)`** — `results.save(MODEL_PATH)`.
- **`train_and_save(db_path)`** — orchestrates; computes in-sample RMSE/MAE; writes a run
  summary to `LOG_PATH` (timestamp, n_obs, chosen orders, AIC, RMSE, MAE, model_path);
  returns it. Reuse the JSON-log pattern from `retrain_model.py`.
- **`forecast(results, horizon)`** — `results.get_forecast(steps=horizon)`; return
  `[{year, month, predicted_net_revenue, lower, upper}, …]` from `.predicted_mean` /
  `.conf_int()`, rounded.
- `if __name__ == "__main__":` CLI for manual runs.

### 3. Train trigger A — Docker startup (`docker-compose.yml`)
Re-point the existing one-shot `model-init` service from XGBoost to **SARIMA**: change
its command from `scripts/retrain_model.py` to `scripts/forecast_model.py`. It already
mounts `./models` + `./logs` and depends on `duckdb: service_completed_successfully`, so
sample data exists before it trains. (XGBoost is no longer trained at startup.)

### 4. Train trigger B — ETL run (`etl/main.py`)
Add a SARIMA training step at the **end of `main()`**, after `conn.close()` (the file
comment notes the writer is released there so a reader can open it) and after
`print_summary`. Call `forecast_model.train_and_save(db_path)` (lazy-import inside the
step to keep ETL import-light) on its own read-only connection. By this point the current
month is loaded — and history too, if `backfill.py` ran first per the documented
`backfill && main` sequence — so the forecast sees the full series. **XGBoost is not
trained here.**

### 5. Train trigger C + read — API endpoints (`main.py`)
Mirror the lazy-import + error handling of the existing `/create_model`:

- **Add `POST /train_seasonal_forecast`** → `forecast_model.train_and_save(db_path)`;
  return `{status, model_path, log_path, summary}`. 503 if DB missing; 422 on fit/data error.
- **Add `GET /reports/seasonal-forecast?horizon=3`** (clamp 1–3) → 503 if `MODEL_PATH`
  absent ("run training first"); else `SARIMAXResults.load(MODEL_PATH)`, `forecast(...)`,
  return `{report: "seasonal-forecast", generated_at, horizon, forecast: […],
  model_trained_at: <from log>}`. Reuse `_serialize`.
- **Remove `POST /predict`** and the `PredictRequest` model.
- **Keep `POST /create_model`** unchanged (dormant XGBoost retrain, available for reuse).

### 6. Tests — `api_tests/test_reports.py`
Add `TestSeasonalForecast`:
- `GET /reports/seasonal-forecast` → **503 before training**.
- `POST /train_seasonal_forecast` → 200 with `summary` (orders + metrics).
- After training, `GET …?horizon=3` → 200 with exactly `horizon` points, each having
  `year, month, predicted_net_revenue, lower, upper`; check horizon clamping (`99` → 3).
- `POST /predict` → **404** (endpoint removed).

## Critical files
- `pyproject.toml` — add `statsmodels`.
- `scripts/forecast_model.py` — **new** SARIMA training module.
- `docker-compose.yml` — `model-init` command → `scripts/forecast_model.py`.
- `etl/main.py` — SARIMA training step at end of `main()`.
- `main.py` — add `/train_seasonal_forecast` + `/reports/seasonal-forecast`; remove
  `/predict` + `PredictRequest`; keep `/create_model` + `retrain_model.py` untouched.
- `api_tests/test_reports.py` — `TestSeasonalForecast`.
- Runtime artifacts: `./models/seasonal_forecast_latest.pkl`,
  `./logs/forecast_retrain_log.json`. (XGBoost's `revenue_forecast_latest.json` is no
  longer regenerated; any existing file just lingers.)

## Verification
1. `uv sync` (pulls statsmodels).
2. Data loaded (`python scripts/load_sample_data.py`), and `etl/backfill.py` for history.
3. `python scripts/forecast_model.py` — fits, prints orders/AIC/RMSE, writes the `.pkl` + log.
4. `python etl/main.py` — runs the ETL chain **and** retrains SARIMA at the end (XGBoost untouched).
5. `uv run uvicorn main:app --reload`:
   - `GET /reports/seasonal-forecast` → 503 before training.
   - `POST /train_seasonal_forecast` → 200; then `GET …?horizon=3` → 3 forward months + CIs.
   - `POST /predict` now 404; `POST /create_model` still present.
6. `uv run pytest api_tests/test_reports.py` — all green.
7. `docker compose up --build` — startup `model-init` trains **SARIMA** before `app` starts.
