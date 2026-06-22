# ADR-003: SARIMA for Revenue Forecasting

**Status:** Accepted  
**Date:** 2026-06-22

## Context

The business requires forward revenue forecasts broken down by product category and region, at monthly granularity, with visible uncertainty bounds. The historical dataset covers approximately 36 months. The solution must be trainable without a GPU, interpretable by a business analyst, and produce per-slice models granular enough to support category- and region-level planning.

## Options Considered

- **Naive / moving average:** Simple to implement and explain, but captures no seasonality or trend — produces flat or lagged forecasts that are not useful for CPG planning cycles.

- **Facebook Prophet:** Automated seasonality detection and trend decomposition; handles missing data gracefully. However, it is a large dependency, treats the model as a black box, and its default configurations frequently overfit on short monthly series.

- **LSTM / Transformer (deep learning):** State-of-the-art on long, high-frequency series. At 36 monthly observations per slice, these models are severely data-starved and will overfit without extensive regularisation — the wrong trade-off for a skeleton that must demonstrate correctness, not sophistication.

- **SARIMA (Seasonal ARIMA):** Principled statistical time-series model with native support for monthly seasonality. Interpretable order parameters; produces calibrated confidence intervals via the statsmodels covariance matrix. Fits reliably on 24+ observations (two full seasonal cycles).

## Decision

SARIMA via `statsmodels.tsa.statespace.SARIMAX`, with the following implementation choices:

**Per-slice models** — a separate SARIMA model is trained for each `(category, region)` pair. This produces granular forecasts without assuming that all slices share the same seasonal pattern. Models are persisted to `./models/revenue_forecast_slices/{key}.pkl` with a manifest JSON listing trained slices, RMSE, and MAE.

**AIC-based order selection** — grid search over 64 combinations of `(p, d, q)(P, D, Q)` with each order ∈ {0, 1} and seasonal period = 12. The combination minimising AIC (Akaike Information Criterion) is selected. Fallback to `(1,1,1)(0,1,1,12)` if grid search produces no valid fit.

**Minimum observations gate** — slices with fewer than 24 monthly observations are skipped to avoid fitting on fewer than two seasonal cycles. Skipped and failed slices are logged; the pipeline continues.

**Aggregate seasonal model** — a second, single SARIMA model is trained on the full `v_monthly_sales_summary` view to power the `/reports/seasonal-forecast` endpoint with a 95% confidence interval band.

**XGBoost complement** — a separate XGBoost model (`scripts/demand_model.py`) is trained for quantity (unit demand) prediction and discount elasticity analysis. SARIMA handles the time-series revenue trajectory; XGBoost handles the feature-driven quantity regression. The two models address different business questions.

## Consequences

**Accepted trade-offs:**
- Grid search over 64 orders per slice adds approximately 5 minutes of training time for a full dataset. Acceptable for a one-time or scheduled training run; not suitable for real-time retraining.
- SARIMA assumes stationarity. Production would add unit-root tests (ADF/KPSS) and automatic differencing selection rather than fixing `d ∈ {0, 1}`.
- Slice models are independent — they do not share information across categories or regions. A hierarchical model (e.g., Prophet with regressors) would improve accuracy for thin slices but adds significant complexity.

**Benefits realised:**
- Interpretable coefficients and explicit seasonal structure — a business analyst can reason about the model parameters.
- Calibrated 95% confidence intervals included in all forecast API responses.
- No GPU dependency — trains on CPU in minutes.
- AIC selection is data-driven; the model complexity adjusts to the available history per slice rather than being fixed by the developer.
