# VelocityIQ — Working Assumptions

These clarifying questions were posed to the evaluators. As answers were not available, each is resolved below as a stated working assumption that the build reflects. Technology choices behind these assumptions are documented separately in the ADRs (`md/adr-001` … `md/adr-004`).

## 1 · Data Ingestion

- **Real vs synthetic data:** No real dataset is provided. **Synthetic POS data is generated** with deliberately injected defects (~5% nulls, ~2% duplicates, late arrivals, malformed dates, negative quantities, orphan SKUs) to exercise the quality pipeline, not just the happy path.
- **Data volume:** POC scale — **~10K rows by default, configurable to ~100K**. The analytical store would be revisited above ~100M rows.
- **Batch vs real-time:** **Batch ingestion is sufficient.** Late-arriving records are handled as a *data-quality case within batch* (audit columns + keeping the latest record per key), **not** as a streaming/real-time requirement.
- **Source data format:** **No single mandated format.** Heterogeneous sources are assumed (differing date formats, price encodings, currencies), normalized through a declarative mapping layer; CSV/JSON/Parquet are all acceptable inputs.
- **Failed-validation handling:** **Quarantine, never silent-drop.** Validation-failed rows are retained with a reject reason; deduplicated rows are tracked in an audit table; a per-batch reconciliation identity (`rows_in == loaded + rejected + deduped`) must hold.
- **"Multiple POS systems":** Represented as **3 distinct sources with different native schemas** → separate raw-landing tables, each mapped to one canonical contract. Adding a source = one new mapping spec, no code change.

## 2 · Forecasting

- **Granularity & level:** **Monthly** forecasts at the **(category × region)** grain, plus an aggregate seasonal model. SKU- and store-level forecasting are out of scope.
- **Forecast horizon:** **Configurable, default short-horizon (3–6 months)**, trained on ~36 months of history (≥2 seasonal cycles).
- **"Region":** Interpreted as a **store cluster / geographic region** (demographics, timezone, income level) — not country/state/city specifically.
- **Model versioning / MLOps:** **Out of scope.** Models are persisted with a manifest (trained slices, accuracy metrics); no experiment tracking, registry, or automated retraining.

## 3 · AI / LLM Layer

- **Runtime vs persisted insights:** **Runtime exposure only.** Insights are generated on demand; historical insights are not persisted or managed.
- **Implementation shape:** **Text-to-SQL over the analytical store** — a natural-language question is translated to a query, executed, and returned with a prose summary.
- **Latency model:** **Synchronous response is acceptable** (configurable timeout); no asynchronous queue/job processing.
- **Data-access constraints:** **Read-only, SELECT-only safety gate** — any mutating statement is rejected. The model is grounded by injected schema context and may read transaction-level data but cannot mutate it. Inference is **local with zero data egress**.

## 4 · API & User Interface

- **Key KPIs & interactions:** Net revenue trends, seasonal/revenue forecasts with confidence intervals, category leakage/opportunity, discount effectiveness, and concentration (Pareto) risk. Primary interactions: **natural-language Q&A** and **pre-built report charts**.
- **Data freshness:** **On-demand / batch-refresh**, reflecting the last ETL run; no real-time auto-refresh.
- **Downstream consumption:** **Primarily REST APIs** for downstream dashboards; the analytical store is also directly queryable. No file-drop/message-bus export channel at POC stage.
