# TODO — Report API Endpoints (Reports 1, 2, 5, 8)

## Context

Implement four sales/revenue reports as **separate API endpoints**, each backed by SQL views where a
view simplifies the query. This pass is **analytics-only** — no forecasting is wired in (the existing
XGBoost `/predict` model has a documented `net_amount` tautology; forecasting is deferred to the
separate effort in `md/TODO_model_changes_required.md`). No Streamlit changes this pass.

The four reports:
- **Report 1** — Seasonal Revenue Trend (monthly/seasonal/YoY)
- **Report 2** — Category Revenue Leakage & Opportunity (discount erosion by category)
- **Report 5** — Discount Effectiveness (do discounts lift units? band analysis)
- **Report 8** — Revenue Concentration Risk / Pareto (top-SKU dependence)

## Constraints found during exploration

- `_query_db(sql)` (main.py ~line 255) opens DuckDB with `read_only=True` → **endpoints cannot create
  views at runtime**. New views must live in `etl/sql/init.sql` and be applied by re-running
  `python scripts/init_db.py --db-path ./data/velocityiq.duckdb --force`. `CREATE VIEW IF NOT EXISTS`
  is idempotent and does not touch existing tables/data.
- Views expose `net_revenue` / `total_revenue` (aggregates of `net_amount` / `total_amount`); the fact
  table uses `net_amount`. Do not mix.
- `year` is a reserved word → must be double-quoted (`sc."year"`) in SQL.
- `v_daily_sales_summary` is missing `year` and `marketing_event` — so Report 1 needs a new
  monthly view rather than reusing it.
- `product_master` is SCD Type 2 → always join with `AND pm.is_current = TRUE`.
- Match existing style: add endpoints directly to `main.py` (monolithic), reuse `_query_db` and
  `_serialize`. Endpoints must `_serialize` each cell (Decimal→float, date→isoformat) like `/insight`.

## New SQL views (add to `etl/sql/init.sql`)

1. **`v_monthly_sales_summary`** (Report 1) — group `sales_transactions ⋈ seasonal_calendar` by
   `"year", month, quarter, season`; expose `transaction_count, total_quantity, total_revenue,
   net_revenue, avg_transaction_value`, plus a `holiday_net_revenue` (CASE on `is_holiday`).

2. **`v_category_leakage`** (Report 2) — group `sales_transactions ⋈ product_master(is_current)` by
   `category`; expose `gross_revenue (SUM total_amount)`, `total_discount`, `net_revenue`,
   `discount_rate = SUM(discount_amount)/NULLIF(SUM(total_amount),0)`, `transaction_count`,
   `total_quantity`, `revenue_per_transaction`.

3. **`v_discount_effectiveness`** (Report 5) — per-transaction `discount_rate = discount_amount/total_amount`
   bucketed into `'0%' | '0-10%' | '10-20%' | '20%+'` (a `discount_band` CASE column), grouped by
   `category, discount_band`; expose `transaction_count, avg_quantity, avg_net_amount, net_revenue,
   total_discount`. Lets the report compare avg units sold across discount bands (elasticity signal).

4. **`v_sku_revenue_pareto`** (Report 8) — built on top of `v_sales_by_product` with window functions:
   `RANK() OVER (ORDER BY net_revenue DESC) AS revenue_rank`,
   `revenue_pct = net_revenue / SUM(net_revenue) OVER ()`,
   `cumulative_revenue_pct = SUM(net_revenue) OVER (ORDER BY net_revenue DESC) / SUM(net_revenue) OVER ()`.

## New API endpoints (add to `main.py`)

All `GET`, returning JSON `{ report, generated_at, data_points: [...], summary: {...} }`, values run
through `_serialize`. Each is one focused query against its view plus light Python shaping.

1. **`GET /reports/seasonal-trend`** — query `v_monthly_sales_summary` ordered by `"year", month`.
   Returns the monthly series, a season-rollup, and YoY growth % per month (computed in Python from
   the series). Optional `?year=` filter.

2. **`GET /reports/category-leakage`** — query `v_category_leakage` ordered by `total_discount DESC`.
   Returns per-category gross vs net, discount_rate, revenue_per_transaction. Summary flags the
   highest-leakage category.

3. **`GET /reports/discount-effectiveness`** — query `v_discount_effectiveness`. Optional `?category=`
   filter. Returns rows per `discount_band` (overall and/or per category) so avg_quantity can be
   compared across bands. Summary notes whether higher discount bands actually show higher avg_quantity.

4. **`GET /reports/concentration-risk`** — query `v_sku_revenue_pareto` ordered by `revenue_rank`.
   Optional `?top_n=` (default 10). Summary computes how many SKUs account for ≥80% of net revenue
   (Pareto threshold) and the top SKU's revenue share.

## Files to modify
- `etl/sql/init.sql` — add the 4 views described above.
- `main.py` — add the 4 `GET /reports/*` endpoints; reuse `_query_db` + `_serialize`. Use FastAPI
  `Query` params for the optional filters.

## Verification
1. Apply views: `python scripts/init_db.py --db-path ./data/velocityiq.duckdb --force`
   (load data first if empty: `python scripts/load_sample_data.py --rows 10000`).
2. Start API: `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000`.
3. Hit each endpoint and confirm non-empty, well-typed JSON:
   - `curl localhost:8000/reports/seasonal-trend`
   - `curl localhost:8000/reports/category-leakage`
   - `curl "localhost:8000/reports/discount-effectiveness?category=<one>"`
   - `curl "localhost:8000/reports/concentration-risk?top_n=10"`
4. Spot-check sanity: seasonal-trend months sum to total net_revenue; pareto `cumulative_revenue_pct`
   ends at ~1.0; category discount_rate ∈ [0,1].
