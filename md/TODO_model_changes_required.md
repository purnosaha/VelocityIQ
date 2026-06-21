# TODO — Forecasting Model Changes Required

Scope: support Reports **1 (Seasonal Trend & Forecast)**, **2 (Category Leakage & Opportunity)**, **5 (Discount Effectiveness)**, and **8 (Concentration Risk / Pareto)**.

## Current state

The model in `scripts/retrain_model.py` is a **per-transaction `net_amount` regressor**:
- Granularity: one row = one sale.
- Features: `quantity`, `unit_price`, `discount_amount`, `category`, `store_type`, `is_holiday`.
- Target: `net_amount`.
- Temporal logic: none in features — date is used only for exponential-decay sample weighting. `season`, `month`, `quarter`, `marketing_event` are not features.
- Served via `/predict`; retrained via `/create_model`.

## Critical blocker — target tautology

`net_amount` is a **generated column**:

```
net_amount = total_amount − discount_amount = (quantity × unit_price) − discount_amount
```

All three right-hand terms are already model inputs, so the model is **fitting an algebraic identity** — it predicts `net_amount` from the numbers that define it. R² looks excellent but the model has learned arithmetic, not behavior. This makes it useless for any lever-based decision (Reports 2 and 5 especially).

**Action:** for behavioral reports, re-target away from `net_amount` to **`quantity` (units / demand)**, with discount as a real driver. Derive revenue as `predicted_qty × unit_price − discount_amount`.

## Required changes per report

### Report 1 — Seasonal Revenue Trend & Forecast
- [ ] Build a **new time-series model** (separate from the per-transaction regressor).
- [ ] Aggregate to monthly/quarterly `net_revenue`.
- [ ] Add temporal features: `month`, `quarter`, `season`, `marketing_event`, `is_holiday`, plus lag and rolling-mean features and YoY deltas.
- [ ] Output a forward forecast for the next 1–3 months/quarters.

### Report 2 — Category Revenue Leakage & Opportunity
- [ ] Re-target from `net_amount` to **demand/quantity** to escape the tautology.
- [ ] Add features: `sku_id`, `brand`, `list_price` (from `product_master`) so category-level and discount-leakage projections are meaningful.
- [ ] Derive net revenue from predicted demand and project discount-leakage recovery scenarios.

### Report 5 — Discount Effectiveness & Margin Impact
- [ ] **Most important change.** Re-target to **`quantity`** with `discount_amount` (or discount %) as the explanatory driver, so the model learns elasticity ("does a 10% discount actually lift units?").
- [ ] Engineer a `discount_rate` feature (`discount_amount / total_amount`) rather than the raw amount.
- [ ] Revenue then computed downstream: `predicted_qty × unit_price − discount`.

### Report 8 — Revenue Concentration Risk (Pareto)
- [ ] Add **entity dimensions** to the model (`sku_id`, `store_id`, `region_id`) or build per-entity forecasts.
- [ ] Add a **scenario/simulation layer** (e.g., "top contributor drops 20–30%") on top of the forecasts — arithmetic, not ML.

## Recommended consolidated approach

Build **two models** instead of four bespoke ones:

1. **Demand model** — re-targeted to `quantity`, with discount as a real driver plus entity dimensions (`sku_id`/`store_id`/`region_id`). Serves Reports 2, 5, and 8. Replaces the current arithmetic-identity model.
2. **Time-series revenue model** — monthly aggregates with seasonality and lag features. Serves Report 1.

Revenue is a **derived quantity** (`qty × price − discount`) on top of the demand model, plus a thin **simulation layer** for discount scenarios (Report 5) and concentration stress tests (Report 8).

## Files affected
- `scripts/retrain_model.py` — re-target, feature changes, add temporal/entity features.
- `main.py` — update `/predict` request schema and feature alignment; likely add a second model's train/predict endpoints.
- New artifacts under `./models/` for the second (time-series) model.
