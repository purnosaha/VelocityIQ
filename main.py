import decimal
import os
import re
from datetime import datetime

import duckdb
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="VelocityIQ", version="0.1.0")


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

v_sales_by_store_region
  store_id, store_name, store_type,
  region_id, region_name, country,
  transaction_count, total_quantity,
  total_revenue, net_revenue, unique_products, active_days
  ▸ USE FOR: store/region performance, geographic breakdowns

v_daily_sales_summary
  transaction_date, day_of_week, week_number, month, quarter, year,
  season, is_holiday,
  transaction_count, active_stores, unique_products,
  total_quantity, total_revenue, net_revenue, avg_transaction_value
  ▸ USE FOR: daily/monthly/quarterly trends, holiday impact, seasonal patterns
  ▸ NO sku_id, product_name, or category — it is a daily AGGREGATE view
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

Q: Weather effect on sales (by condition or region)?
→ Use v_sales_weather_context directly — do NOT re-join weather_overlay:
   SELECT region_name, weather_condition,
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
