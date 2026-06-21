import decimal
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="VelocityIQ", version="0.1.0")

# Make scripts/ importable so the API can reuse the retraining pipeline.
_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))

FORBIDDEN_SQL_TOKENS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
    "ATTACH", "COPY", "PRAGMA", "EXPORT", "INSTALL", "LOAD",
    "CALL", "TRUNCATE", "REPLACE", "VACUUM",
)

SCHEMA_CONTEXT = """\
You are translating natural-language questions into a single DuckDB SELECT statement.
The database is a star-schema retail sales analytics platform.

════════════════════════════════════════
DIMENSION TABLES
════════════════════════════════════════
product_master(sku_id PK, product_name, category, brand, package_size, list_price,
               launch_date, status, is_current BOOLEAN, effective_date, end_date)
  ▸ SCD Type 2. Always add `WHERE pm.is_current = TRUE` to avoid duplicate SKUs.

regional_reference(region_id PK, region_name, country, timezone, population, income_level)
  ▸ No SCD / no is_current column — do NOT add WHERE is_current filters here.

store_reference(store_id PK, store_name, region_id FK→regional_reference, store_type)
  ▸ store_type IN ('online','physical')

seasonal_calendar(date PK, day_of_week, day_of_week_num, week_number, month,
                  quarter, year, is_holiday BOOLEAN, season, marketing_event)
  ▸ season IN ('Spring','Summer','Fall','Winter')
  ▸ Covers 2023-06-14 to 2026-06-14

weather_overlay(region_id FK, date, temperature_avg, temperature_min, temperature_max,
                precipitation, humidity, weather_condition, weather_alert)
  ▸ PK (region_id, date). LEFT JOIN it — not every date has weather data.

════════════════════════════════════════
FACT TABLE
════════════════════════════════════════
sales_transactions(transaction_id PK,
                   sku_id   FK→product_master,
                   store_id FK→store_reference,
                   transaction_date FK→seasonal_calendar,
                   transaction_time, quantity, unit_price,
                   total_amount, discount_amount,
                   net_amount  -- generated: total_amount - discount_amount)

════════════════════════════════════════
PRE-BUILT VIEWS — columns available in each view
════════════════════════════════════════
v_sales_by_product
  sku_id, product_name, category, brand,
  transaction_count, total_quantity,
  total_revenue, total_discount, net_revenue,
  avg_unit_price, first_sale_date, last_sale_date
  ▸ USE FOR: product/category ranking, revenue by brand/category (all-time)
  ▸ NO quarter/year/season filtering — aggregates the full date range
  ▸ NO net_amount column — use net_revenue directly (already aggregated)
  ▸ NO year/quarter/season columns — cannot filter by time period

v_sales_by_store_region
  store_id, store_name, store_type,
  region_id, region_name, country,
  transaction_count, total_quantity,
  total_revenue, net_revenue, unique_products, active_days
  ▸ USE FOR: store/region performance, geographic breakdowns
  ▸ NO net_amount column — use net_revenue directly (already aggregated)
  ▸ NO avg_transaction_value — compute as ROUND(net_revenue / transaction_count, 2)

v_daily_sales_summary
  transaction_date, day_of_week, week_number, month, quarter, year,
  season, is_holiday,
  transaction_count, active_stores, unique_products,
  total_quantity, total_revenue, net_revenue, avg_transaction_value
  ▸ USE FOR: daily/monthly/quarterly trends, holiday impact, seasonal patterns
  ▸ NO sku_id, product_name, category, or net_amount — it is a daily AGGREGATE view
  ▸ NO marketing_event column — JOIN to seasonal_calendar to get marketing_event
  ▸ DO NOT join this view to product_master

v_sales_weather_context
  transaction_date, region_id, region_name,
  temperature_avg, precipitation, weather_condition, weather_alert,
  total_quantity, total_revenue, transaction_count
  ▸ USE FOR: weather vs sales correlation, impact of rain/temperature

════════════════════════════════════════
QUERY PATTERNS — follow these exactly
════════════════════════════════════════
Q: Category/product revenue for a specific quarter/year?
→ Join sales_transactions with product_master AND seasonal_calendar:
   SELECT pm.category, ROUND(SUM(st.net_amount),2) AS net_revenue
   FROM sales_transactions st
   JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
   JOIN seasonal_calendar sc ON st.transaction_date = sc.date
   WHERE sc.quarter = 4 AND sc.year = 2025
   GROUP BY pm.category ORDER BY net_revenue DESC LIMIT 10

Q: Overall category ranking (no time filter)?
→ SELECT category, ROUND(net_revenue,2) AS net_revenue
   FROM v_sales_by_product
   GROUP BY category, net_revenue ORDER BY net_revenue DESC LIMIT 10

Q: Monthly or seasonal trends?
→ Use v_daily_sales_summary and GROUP BY month/season/quarter/year

Q: Store or region performance?
→ Use v_sales_by_store_region

Q: Top product/category per region (one winner per region)?
→ Use QUALIFY with ROW_NUMBER() — never ORDER BY inside a subquery IN(...):
   WITH cat_revenue AS (
       SELECT rr.region_name, pm.category,
              ROUND(SUM(st.net_amount),2) AS net_revenue
       FROM sales_transactions st
       JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
       JOIN store_reference sr ON st.store_id = sr.store_id
       JOIN regional_reference rr ON sr.region_id = rr.region_id
       GROUP BY rr.region_name, pm.category
   )
   SELECT region_name, category, net_revenue
   FROM cat_revenue
   QUALIFY ROW_NUMBER() OVER (PARTITION BY region_name ORDER BY net_revenue DESC) = 1
   ORDER BY net_revenue DESC LIMIT 20

Q: Weather effect on sales (by condition or region)?
→ ALWAYS use v_sales_weather_context — never re-join weather_overlay for this pattern:
   SELECT weather_condition,
          ROUND(SUM(total_revenue),2) AS total_revenue,
          SUM(transaction_count) AS transactions
   FROM v_sales_weather_context
   GROUP BY weather_condition
   ORDER BY total_revenue DESC LIMIT 20

Q: Weather effect filtered by region?
→ SELECT region_name, weather_condition,
          ROUND(SUM(total_revenue),2) AS total_revenue,
          SUM(transaction_count) AS transactions
   FROM v_sales_weather_context
   WHERE region_name ILIKE '%West%'
   GROUP BY region_name, weather_condition
   ORDER BY total_revenue DESC LIMIT 20

Q: Product + region combo (no weather)?
→ Join sales_transactions → product_master → store_reference → regional_reference:
   SELECT pm.category, rr.region_name, ROUND(SUM(st.net_amount),2) AS net_revenue
   FROM sales_transactions st
   JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
   JOIN store_reference sr ON st.store_id = sr.store_id
   JOIN regional_reference rr ON sr.region_id = rr.region_id
   WHERE rr.region_name ILIKE '%West%'
   GROUP BY pm.category, rr.region_name
   ORDER BY net_revenue DESC LIMIT 10

Q: Product + weather combo?
→ Join sales_transactions → product_master → store_reference → weather_overlay:
   SELECT pm.category, wo.weather_condition, ROUND(SUM(st.net_amount),2) AS net_revenue
   FROM sales_transactions st
   JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
   JOIN store_reference sr ON st.store_id = sr.store_id
   LEFT JOIN weather_overlay wo ON wo.region_id = sr.region_id AND wo.date = st.transaction_date
   GROUP BY pm.category, wo.weather_condition
   ORDER BY net_revenue DESC LIMIT 20

Q: Marketing event revenue (avg daily revenue per event)?
→ marketing_event is in seasonal_calendar, NOT in v_daily_sales_summary — JOIN them:
   SELECT sc.marketing_event, ROUND(AVG(dss.net_revenue),2) AS avg_daily_revenue, COUNT(*) AS days
   FROM v_daily_sales_summary dss
   JOIN seasonal_calendar sc ON dss.transaction_date = sc.date
   WHERE sc.marketing_event IS NOT NULL
   GROUP BY sc.marketing_event
   ORDER BY avg_daily_revenue DESC LIMIT 20

Q: Year-over-year or multi-year revenue comparison?
→ Use v_daily_sales_summary which has a year column:
   SELECT year, ROUND(SUM(net_revenue),2) AS total_net_revenue
   FROM v_daily_sales_summary
   WHERE year IN (2024, 2025)
   GROUP BY year
   ORDER BY year

Q: Top stores or store transaction count (joining sales_transactions + store_reference)?
→ Always qualify store_id with the table alias to avoid ambiguity:
   SELECT sr.store_id, sr.store_name, sr.store_type,
          COUNT(st.transaction_id) AS transaction_count,
          ROUND(SUM(st.net_amount),2) AS net_revenue
   FROM sales_transactions st
   JOIN store_reference sr ON st.store_id = sr.store_id
   GROUP BY sr.store_id, sr.store_name, sr.store_type
   ORDER BY net_revenue DESC LIMIT 10

Q: Average transaction value by store type?
→ Query sales_transactions joined to store_reference — do NOT use a view for this:
   SELECT sr.store_type,
          ROUND(SUM(st.net_amount) / COUNT(st.transaction_id), 2) AS avg_transaction_value
   FROM sales_transactions st
   JOIN store_reference sr ON st.store_id = sr.store_id
   GROUP BY sr.store_type
   ORDER BY avg_transaction_value DESC

════════════════════════════════════════
STRICT RULES
════════════════════════════════════════
1. Output ONLY one DuckDB SELECT (or WITH…SELECT). No prose, no markdown, no fences.
2. Never use INSERT/UPDATE/DELETE/DROP/CREATE/ALTER or any DDL/DML.
3. No trailing semicolon.
4. Include ORDER BY + LIMIT (≤100) whenever the result could be large.
5. Round monetary values: ROUND(x, 2).
6. Never reference a column that is not listed above for the table/view you are querying.
7. When filtering by category use exact values from data — do not guess; use ILIKE if uncertain.
8. `net_amount` ONLY exists in `sales_transactions`. Views (v_sales_by_product, v_daily_sales_summary, v_sales_by_store_region) have `net_revenue`, NOT `net_amount`. Never use `net_amount` when querying a view.
9. Only `product_master` has `is_current`. Never add `WHERE is_current` on `regional_reference`, `store_reference`, or any view.
10. When joining `sales_transactions` (alias st) with `store_reference` (alias sr), ALWAYS qualify `store_id` with its alias: `sr.store_id` in SELECT/GROUP BY, never bare `store_id`.
11. `marketing_event` is in `seasonal_calendar` only — never in views. To query it, JOIN `v_daily_sales_summary` to `seasonal_calendar` on `transaction_date = date`.
12. For year/quarter filtering, use `v_daily_sales_summary` (has year, quarter) or join `sales_transactions` to `seasonal_calendar`. Never filter by year/quarter on `v_sales_by_product`.
13. `year` is a reserved keyword in DuckDB. Always double-quote it when referencing the column: `"year"`. Example: `GROUP BY "year"`, `WHERE "year" IN (2024, 2025)`, `SELECT "year"`.
14. For weather condition aggregations across all regions, ALWAYS use `v_sales_weather_context` directly. Never join `weather_overlay` manually for this pattern — it causes JOIN order errors.
15. Never use ORDER BY inside a subquery used with IN(...). Use QUALIFY ROW_NUMBER() OVER (...) = 1 for "top N per group" queries.
"""


class InsightRequest(BaseModel):
    question: str
    max_rows: int = 100


def _query_db(sql: str) -> tuple[list[str], list[dict]]:
    db_path = os.environ.get("DUCKDB_PATH", "./data/velocityiq.duckdb")
    conn = duckdb.connect(db_path, read_only=True)
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    return columns, [dict(zip(columns, row)) for row in rows]


def _serialize(val):
    if isinstance(val, decimal.Decimal):
        return float(val)
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def _format_table(columns: list[str], records: list[dict]) -> str:
    header = " | ".join(columns)
    sep = " | ".join("-" * max(len(c), 4) for c in columns)
    rows = [" | ".join(str(_serialize(r.get(c, ""))) for c in columns) for r in records]
    return "\n".join([header, sep] + rows)


def _ollama_chat(system: str, user: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": 0.1},
    }
    try:
        with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
            r = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")
    content = data.get("message", {}).get("content", "")
    if not content:
        raise HTTPException(status_code=502, detail=f"Ollama returned no content: {data}")
    return content


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT_RE = re.compile(r"\b(WITH|SELECT)\b.*", re.DOTALL | re.IGNORECASE)


def _extract_sql(raw: str) -> str:
    fence = _FENCE_RE.search(raw)
    candidate = fence.group(1) if fence else raw
    match = _SELECT_RE.search(candidate)
    if not match:
        raise HTTPException(status_code=422, detail=f"LLM did not return a SELECT statement. Got: {raw!r}")
    sql = match.group(0).strip().rstrip(";").strip()
    return sql


def _validate_sql(sql: str) -> None:
    stripped = sql.lstrip().upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise HTTPException(status_code=422, detail=f"Only SELECT/WITH queries are allowed. Got: {sql!r}")
    if ";" in sql:
        raise HTTPException(status_code=422, detail="Multiple statements are not allowed.")
    upper = " " + sql.upper() + " "
    for token in FORBIDDEN_SQL_TOKENS:
        if re.search(rf"\b{token}\b", upper):
            raise HTTPException(status_code=422, detail=f"Forbidden SQL keyword: {token}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/create_model")
def create_model():
    """Retrain the XGBoost revenue-forecast model end-to-end.

    Reuses the standalone pipeline in ``scripts/retrain_model.py``: extracts
    current-month + historical samples from DuckDB, applies exponential-decay
    weights, trains the model, and persists it to ``./models`` with a run
    summary in ``./logs``. Runs synchronously in FastAPI's threadpool.
    """
    # Imported lazily so the heavy ML stack is only loaded when retraining is
    # requested (keeps the /insight path and import time light).
    try:
        import retrain_model
    except ImportError as e:  # pragma: no cover - misconfigured deployment
        raise HTTPException(status_code=500, detail=f"Retraining module unavailable: {e}")

    db_path = os.environ.get("DUCKDB_PATH", retrain_model.DEFAULT_DB_PATH)
    try:
        summary = retrain_model.retrain(db_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Database not found at {db_path}: {e}")
    except (duckdb.Error, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Retraining failed: {e}")

    return {
        "status": "ok",
        "model_path": summary["model_path"],
        "log_path": retrain_model.LOG_PATH,
        "summary": summary,
    }


@app.post("/train_seasonal_forecast")
def train_seasonal_forecast():
    """Retrain the SARIMA seasonal revenue forecast model end-to-end."""
    try:
        import forecast_model
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Forecast module unavailable: {e}")

    db_path = os.environ.get("DUCKDB_PATH", forecast_model.DEFAULT_DB_PATH)
    try:
        summary = forecast_model.train_and_save(db_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Database not found at {db_path}: {e}")
    except (duckdb.Error, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Training failed: {e}")

    return {
        "status": "ok",
        "model_path": summary["model_path"],
        "log_path": forecast_model.LOG_PATH,
        "summary": summary,
    }


@app.post("/train_demand_model")
def train_demand_model_endpoint():
    """Retrain the XGBoost demand (quantity) model end-to-end."""
    try:
        import demand_model
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Demand model module unavailable: {e}")

    db_path = os.environ.get("DUCKDB_PATH", demand_model.DEFAULT_DB_PATH)
    try:
        summary = demand_model.retrain(db_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=f"Database not found at {db_path}: {e}")
    except (duckdb.Error, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Demand model training failed: {e}")

    return {
        "status": "ok",
        "model_path": summary["model_path"],
        "features_path": summary["features_path"],
        "log_path": demand_model.LOG_PATH,
        "summary": summary,
    }


@app.post("/insight")
def insight(request: InsightRequest):
    """Answer a natural-language question by generating SQL, running it, and summarizing."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="`question` must not be empty.")

    raw_sql = _ollama_chat(SCHEMA_CONTEXT, question)
    sql = _extract_sql(raw_sql)
    _validate_sql(sql)

    capped_sql = f"SELECT * FROM (\n{sql}\n) AS _insight_q LIMIT {request.max_rows}"

    try:
        columns, records = _query_db(capped_sql)
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=f"Generated SQL failed: {e}\nSQL: {sql}")

    if not records:
        return {
            "question": question,
            "sql": sql,
            "summary": "The query ran successfully but returned no rows. Make sure sample data is loaded with `python scripts/load_sample_data.py`.",
            "data_points": [],
            "row_count": 0,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    safe_records = [{k: _serialize(v) for k, v in row.items()} for row in records]
    table = _format_table(columns, safe_records)

    summary_system = (
        "You are a sales intelligence analyst for VelocityIQ. "
        "Write a concise 3-5 sentence narrative answering the user's question. "
        "Cite the most important numbers from the data. Avoid bullet points."
    )
    summary_user = f"Question: {question}\n\nData:\n{table}"
    summary = _ollama_chat(summary_system, summary_user).strip()

    return {
        "question": question,
        "sql": sql,
        "summary": summary,
        "data_points": safe_records,
        "row_count": len(safe_records),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Demand model inference helpers (shared by Reports 2 and 5)
# ---------------------------------------------------------------------------

_CATEGORY_FEATURES_SQL = """
SELECT
    pm.category,
    MEDIAN(pm.list_price)                                                        AS median_list_price,
    MEDIAN(st.unit_price)                                                        AS median_unit_price,
    MEDIAN(COALESCE(st.discount_amount / NULLIF(st.total_amount, 0), 0.0))      AS actual_discount_rate,
    MODE() WITHIN GROUP (ORDER BY sr.store_type)                                 AS mode_store_type,
    MODE() WITHIN GROUP (ORDER BY pm.brand)                                      AS mode_brand,
    MODE() WITHIN GROUP (ORDER BY sc.season)                                     AS mode_season,
    MAX(sc.month::INT)                                                           AS latest_month,
    MAX(sc.quarter::INT)                                                         AS latest_quarter,
    MAX(sc.is_holiday::INT)                                                      AS mode_is_holiday,
    MODE() WITHIN GROUP (ORDER BY COALESCE(sc.marketing_event, 'none'))         AS mode_marketing_event
FROM sales_transactions st
JOIN product_master pm  ON st.sku_id   = pm.sku_id  AND pm.is_current = TRUE
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY pm.category
"""

_BAND_MIDPOINTS = {"0%": 0.0, "0-10%": 0.05, "10-20%": 0.15, "20%+": 0.25}
_BAND_ORDER = ["0%", "0-10%", "10-20%", "20%+"]


def _get_category_features() -> dict[str, dict]:
    """Return per-category median/mode feature values keyed by category name."""
    try:
        _, rows = _query_db(_CATEGORY_FEATURES_SQL)
        return {r["category"]: {k: _serialize(v) for k, v in r.items()} for r in rows}
    except Exception:
        return {}


def _run_demand_inference(cat_features: dict[str, dict], discount_rate: float, model, feature_cols: list[str]) -> float:
    """Predict quantity for a single category at a given discount_rate. Returns 0.0 on error."""
    try:
        import demand_model
        row = {
            "unit_price": cat_features.get("median_unit_price", 0.0),
            "list_price": cat_features.get("median_list_price", 0.0),
            "discount_rate": discount_rate,
            "month": int(cat_features.get("latest_month") or 1),
            "quarter": int(cat_features.get("latest_quarter") or 1),
            "is_holiday": int(cat_features.get("mode_is_holiday") or 0),
            "category": cat_features.get("category", "unknown"),
            "brand": cat_features.get("mode_brand", "unknown"),
            "store_type": cat_features.get("mode_store_type", "unknown"),
            "season": cat_features.get("mode_season", "unknown"),
            "marketing_event": cat_features.get("mode_marketing_event", "none"),
        }
        df = demand_model.build_inference_df([row], feature_cols)
        pred = float(model.predict(df)[0])
        return max(0.0, pred)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Report endpoints
# ---------------------------------------------------------------------------

@app.get("/reports/seasonal-forecast")
def report_seasonal_forecast(horizon: int = Query(default=3, ge=1, le=10)):
    """Report 1 extension — SARIMA forward forecast with confidence intervals."""
    try:
        import forecast_model
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Forecast module unavailable: {e}")

    horizon = max(1, min(horizon, 10))

    model_path = forecast_model.MODEL_PATH
    if not os.path.exists(model_path):
        raise HTTPException(
            status_code=503,
            detail=f"No trained model found at {model_path}. Run POST /train_seasonal_forecast first.",
        )

    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAXResults
        results = SARIMAXResults.load(model_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {e}")

    try:
        fc = forecast_model.forecast(results, horizon)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Forecast failed: {e}")

    model_trained_at = None
    log_path = forecast_model.LOG_PATH
    if os.path.exists(log_path):
        try:
            with open(log_path) as fh:
                import json as _json
                model_trained_at = _json.load(fh).get("retrain_timestamp")
        except Exception:
            pass

    return {
        "report": "seasonal-forecast",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "horizon": horizon,
        "forecast": fc,
        "model_trained_at": model_trained_at,
    }


@app.get("/reports/seasonal-trend")
def report_seasonal_trend(year: int | None = Query(default=None)):
    """Report 1 — monthly/seasonal revenue trend with optional year filter and YoY growth."""
    sql = 'SELECT * FROM v_monthly_sales_summary'
    if year is not None:
        sql += f' WHERE "year" = {int(year)}'
    sql += ' ORDER BY "year", month'
    try:
        _, records = _query_db(sql)
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=str(e))

    safe = [{k: _serialize(v) for k, v in row.items()} for row in records]

    # Season rollup across the returned rows
    season_rollup: dict[str, float] = {}
    for row in safe:
        s = row["season"]
        season_rollup[s] = round(season_rollup.get(s, 0.0) + float(row["net_revenue"] or 0), 2)

    # YoY growth % per (year, month) pair — enriches each row in-place
    by_ym: dict[tuple[int, int], float] = {
        (r["year"], r["month"]): float(r["net_revenue"] or 0) for r in safe
    }
    for row in safe:
        prev = by_ym.get((row["year"] - 1, row["month"]))
        if prev is not None and prev != 0:
            row["yoy_growth_pct"] = round((float(row["net_revenue"] or 0) - prev) / prev * 100, 2)
        else:
            row["yoy_growth_pct"] = None

    return {
        "report": "seasonal-trend",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_points": safe,
        "summary": {
            "season_rollup": season_rollup,
            "total_months": len(safe),
        },
    }


@app.get("/reports/category-leakage")
def report_category_leakage(
    category: str | None = Query(default=None),
    target_discount_rate: float = Query(default=0.0, ge=0.0, le=1.0),
):
    """Report 2 — gross vs net revenue by category; flags highest discount-eroded category.

    - ``category``: filter to a single category (case-insensitive).
    - ``target_discount_rate``: counterfactual rate to compare against actual (0.0–1.0, default 0).
    """
    sql = "SELECT * FROM v_category_leakage"
    if category is not None:
        safe_cat = category.replace("'", "''")
        sql += f" WHERE category ILIKE '{safe_cat}'"
    sql += " ORDER BY total_discount DESC"
    try:
        _, records = _query_db(sql)
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=str(e))

    safe = [{k: _serialize(v) for k, v in row.items()} for row in records]
    top = safe[0] if safe else None

    # Demand model: opportunity revenue at target_discount_rate vs actual per category.
    model_insights = None
    model_trained = False
    try:
        import demand_model
        model, feature_cols = demand_model.load_demand_model()
        if model is not None and feature_cols is not None:
            model_trained = True
            cat_feat_map = _get_category_features()
            insights = []
            for row in safe:
                cat = row["category"]
                cf = cat_feat_map.get(cat)
                if cf is None:
                    continue
                cf["category"] = cat
                actual_rate = float(cf.get("actual_discount_rate") or 0.0)
                pred_actual = _run_demand_inference(cf, actual_rate, model, feature_cols)
                pred_target = _run_demand_inference(cf, target_discount_rate, model, feature_cols)
                unit_price = float(cf.get("median_unit_price") or 0.0)
                opportunity = max(0.0, pred_target - pred_actual) * unit_price
                insights.append({
                    "category": cat,
                    "actual_discount_rate": round(actual_rate, 4),
                    "target_discount_rate": round(target_discount_rate, 4),
                    "pred_qty_at_actual_discount": round(pred_actual, 2),
                    "pred_qty_at_target_discount": round(pred_target, 2),
                    "opportunity_revenue_usd": round(opportunity, 2),
                })
            model_insights = insights
    except Exception:
        pass

    return {
        "report": "category-leakage",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_points": safe,
        "summary": {
            "highest_leakage_category": top["category"] if top else None,
            "highest_discount_rate": top["discount_rate"] if top else None,
        },
        "model_insights": model_insights,
        "model_trained": model_trained,
    }


@app.get("/reports/discount-effectiveness")
def report_discount_effectiveness(
    category: str | None = Query(default=None),
    target_discount_rate: float | None = Query(default=None, ge=0.0, le=1.0),
):
    """Report 5 — avg units per discount band; optional category and custom rate filters.

    - ``category``: filter to a single category (case-insensitive).
    - ``target_discount_rate``: predict demand at a custom rate (0.0–1.0) in addition to band midpoints.
    """
    sql = "SELECT * FROM v_discount_effectiveness"
    if category is not None:
        safe_cat = category.replace("'", "''")
        sql += f" WHERE category ILIKE '{safe_cat}'"
    sql += " ORDER BY category, discount_band"
    try:
        _, records = _query_db(sql)
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=str(e))

    safe = [{k: _serialize(v) for k, v in row.items()} for row in records]

    _band_order = {"0%": 0, "0-10%": 1, "10-20%": 2, "20%+": 3}
    band_agg: dict[str, dict] = {}
    for row in safe:
        b = row["discount_band"]
        if b not in band_agg:
            band_agg[b] = {"txn": 0, "qty_sum": 0.0}
        band_agg[b]["txn"] += row["transaction_count"]
        band_agg[b]["qty_sum"] += float(row["avg_quantity"] or 0) * row["transaction_count"]

    band_avg_qty = {
        b: round(v["qty_sum"] / v["txn"], 2)
        for b, v in band_agg.items()
        if v["txn"] > 0
    }
    sorted_bands = sorted(band_avg_qty.items(), key=lambda x: _band_order.get(x[0], 9))
    lifts = sorted_bands[-1][1] > sorted_bands[0][1] if len(sorted_bands) >= 2 else None

    # Demand model: predicted qty per (category, band) + inter-band elasticity.
    elasticity = None
    elasticity_custom = None
    model_trained = False
    try:
        import demand_model
        model, feature_cols = demand_model.load_demand_model()
        if model is not None and feature_cols is not None:
            model_trained = True
            cat_feat_map = _get_category_features()
            el_rows = []
            # Group rows by category to compute inter-band elasticity.
            from collections import defaultdict
            by_cat: dict[str, list] = defaultdict(list)
            for row in safe:
                by_cat[row["category"]].append(row)

            for cat, cat_rows in by_cat.items():
                cf = cat_feat_map.get(cat)
                if cf is None:
                    continue
                cf["category"] = cat
                # Sort by band order to enable sequential elasticity computation.
                ordered = sorted(
                    cat_rows,
                    key=lambda r: _BAND_ORDER.index(r["discount_band"]) if r["discount_band"] in _BAND_ORDER else 99,
                )
                prev_pred: float | None = None
                prev_rate: float | None = None
                for row in ordered:
                    band = row["discount_band"]
                    rate = _BAND_MIDPOINTS.get(band, 0.0)
                    pred = _run_demand_inference(cf, rate, model, feature_cols)
                    elas = None
                    if prev_pred is not None and prev_rate is not None:
                        delta_rate = rate - prev_rate
                        if delta_rate > 0:
                            elas = round((pred - prev_pred) / delta_rate, 2)
                    el_rows.append({
                        "category": cat,
                        "discount_band": band,
                        "pred_avg_qty": round(pred, 2),
                        "sql_avg_qty": round(float(row.get("avg_quantity") or 0), 2),
                        "elasticity": elas,
                    })
                    prev_pred = pred
                    prev_rate = rate
            elasticity = el_rows

            # Custom rate prediction: one row per category at the requested rate.
            if target_discount_rate is not None:
                custom_preds = []
                for cat, cf in cat_feat_map.items():
                    if category is not None and cat.lower() != category.lower():
                        continue
                    cf["category"] = cat
                    pred = _run_demand_inference(cf, target_discount_rate, model, feature_cols)
                    custom_preds.append({
                        "category": cat,
                        "target_discount_rate": round(target_discount_rate, 4),
                        "pred_qty": round(pred, 2),
                    })
                # attach as a separate key so it doesn't mix with band elasticity rows
                elasticity_custom = custom_preds
            else:
                elasticity_custom = None
    except Exception:
        elasticity_custom = None

    return {
        "report": "discount-effectiveness",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_points": safe,
        "summary": {
            "band_avg_quantity": {b: q for b, q in sorted_bands},
            "higher_discount_lifts_quantity": lifts,
        },
        "elasticity": elasticity,
        "elasticity_custom": elasticity_custom,
        "model_trained": model_trained,
    }


@app.get("/reports/concentration-risk")
def report_concentration_risk(top_n: int = Query(default=10, ge=1, le=500)):
    """Report 8 — SKU revenue Pareto; how many SKUs drive 80 % of revenue."""
    try:
        _, records = _query_db(
            f"SELECT * FROM v_sku_revenue_pareto ORDER BY revenue_rank LIMIT {top_n}"
        )
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=str(e))

    safe = [{k: _serialize(v) for k, v in row.items()} for row in records]

    try:
        _, pareto = _query_db(
            "SELECT MIN(revenue_rank) AS sku_count "
            "FROM v_sku_revenue_pareto WHERE cumulative_revenue_pct >= 0.80"
        )
        skus_at_80 = pareto[0]["sku_count"] if pareto else None
    except duckdb.Error:
        skus_at_80 = None

    top = safe[0] if safe else None

    return {
        "report": "concentration-risk",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_points": safe,
        "summary": {
            "top_sku": top["product_name"] if top else None,
            "top_sku_revenue_pct": top["revenue_pct"] if top else None,
            "skus_covering_80pct_revenue": skus_at_80,
            "top_n": top_n,
        },
    }


@app.get("/reports/concentration-risk/scenario")
def report_concentration_risk_scenario(
    top_n: int = Query(default=10, ge=1, le=500),
    shock_pct: float = Query(default=20.0, ge=0.0, le=100.0),
):
    """Report 8 scenario — revenue impact if top-N SKUs decline by shock_pct %."""
    try:
        _, records = _query_db(
            f"SELECT * FROM v_sku_revenue_pareto ORDER BY revenue_rank LIMIT {top_n}"
        )
        _, total_rows = _query_db("SELECT SUM(net_revenue) AS total FROM v_sales_by_product")
    except duckdb.Error as e:
        raise HTTPException(status_code=422, detail=str(e))

    safe = [{k: _serialize(v) for k, v in row.items()} for row in records]
    total_revenue = float((total_rows[0]["total"] or 0) if total_rows else 0)

    top_n_revenue = sum(float(r.get("net_revenue") or 0) for r in safe)
    rest_revenue = total_revenue - top_n_revenue
    shocked_top_n = top_n_revenue * (1.0 - shock_pct / 100.0)
    scenario_total = shocked_top_n + rest_revenue
    revenue_impact = scenario_total - total_revenue
    revenue_impact_pct = (revenue_impact / total_revenue * 100.0) if total_revenue else 0.0

    return {
        "report": "concentration-risk-scenario",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "top_n": top_n,
        "shock_pct": shock_pct,
        "baseline_total_revenue": round(total_revenue, 2),
        "top_n_revenue_baseline": round(top_n_revenue, 2),
        "top_n_revenue_shocked": round(shocked_top_n, 2),
        "scenario_total_revenue": round(scenario_total, 2),
        "revenue_impact_usd": round(revenue_impact, 2),
        "revenue_impact_pct": round(revenue_impact_pct, 2),
        "data_points": safe,
    }
