import decimal
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
from fastapi import FastAPI, HTTPException
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


class PredictRequest(BaseModel):
    quantity: float
    unit_price: float
    discount_amount: float
    category: str
    store_type: str
    is_holiday: bool


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


@app.post("/predict")
def predict(request: PredictRequest):
    """Forecast net_amount for a single transaction using the trained XGBoost model.

    Applies the same preprocessing as the training pipeline: numeric coercion,
    is_holiday cast to int, one-hot encoding of category/store_type, and column
    alignment to the model's exact feature set (unseen categories → 0).
    """
    try:
        import pandas as pd
        import retrain_model
        from xgboost import XGBRegressor
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"ML stack unavailable: {e}")

    model_path = retrain_model.MODEL_PATH
    if not os.path.exists(model_path):
        raise HTTPException(
            status_code=503,
            detail=f"No trained model found at {model_path}. Run POST /create_model first.",
        )

    model = XGBRegressor()
    model.load_model(model_path)

    row = pd.DataFrame([{
        "quantity": float(request.quantity),
        "unit_price": float(request.unit_price),
        "discount_amount": float(request.discount_amount),
        "category": request.category,
        "store_type": request.store_type,
        "is_holiday": int(request.is_holiday),
    }])

    row = pd.get_dummies(row, columns=["category", "store_type"], prefix=["category", "store_type"])
    bool_cols = row.select_dtypes(include="bool").columns
    if len(bool_cols):
        row[bool_cols] = row[bool_cols].astype(int)

    expected_features = model.get_booster().feature_names
    row = row.reindex(columns=expected_features, fill_value=0)

    predicted = round(float(model.predict(row)[0]), 2)

    return {
        "predicted_net_amount": predicted,
        "inputs": {
            "quantity": request.quantity,
            "unit_price": request.unit_price,
            "discount_amount": request.discount_amount,
            "category": request.category,
            "store_type": request.store_type,
            "is_holiday": request.is_holiday,
        },
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
