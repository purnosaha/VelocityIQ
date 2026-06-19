# Revenue-Forecast Model — Retraining & Scheduling

How the XGBoost revenue-forecast model (`scripts/retrain_model.py`) gets
(re)trained, and the plan for automating it.

## Pipeline entrypoints

| Trigger | Mechanism | Status |
| --- | --- | --- |
| On demand (API) | `POST /create_model` ([main.py](../main.py)) | ✅ live |
| Initial training on stack spawn | `model-init` service in [docker-compose.yml](../docker-compose.yml) | ✅ live |
| Recurring / scheduled retrain | cron or Airflow | ⏳ **TODO** |

The script is the single source of truth — every trigger above just invokes
`scripts/retrain_model.py` (directly or via the `/create_model` endpoint, which
reuses the same module). It is intentionally side-effect-free apart from writing
`./models/revenue_forecast_latest.json` and `./logs/retrain_log.json`, so it is
safe to call from any scheduler with no manual steps.

Standalone invocation:

```bash
python scripts/retrain_model.py            # uses $DUCKDB_PATH or ./data/velocityiq.duckdb
# or inside the running stack:
docker compose run --rm model-init
```

## Initial training on `docker compose up`

The `model-init` one-shot service trains the model once, after the `duckdb`
service has seeded the database (`depends_on … service_completed_successfully`).
It reuses the **app image** (which installs `libgomp1`, required by xgboost) and
mounts `./data`, `./models`, and `./logs` so artifacts land on the host.

It does **not** block the `app` / `streamlit` services from starting — the API
is available immediately, and `/create_model` can retrain on demand while the
initial training runs in parallel.

## TODO — proper scheduled pipeline

Once we stand up the real scheduling layer, drive retraining from there and
remove the stop-gaps above as appropriate.

- [ ] Decide cadence (e.g. nightly) and orchestrator (Airflow vs. a managed
      cron / k8s CronJob).
- [ ] Wrap `retrain_model.retrain()` as a task/DAG step; read `DUCKDB_PATH` and
      output paths from config, not hardcoded defaults.
- [ ] Add run alerting on failure and on metric regression (watch `r2` / `rmse`
      in `logs/retrain_log.json` against the previous run).
- [ ] Version saved models (timestamped filenames + a `latest` pointer) instead
      of overwriting `revenue_forecast_latest.json`.
- [ ] Decide whether to keep the `model-init` bootstrap once the scheduler runs
      on its own cadence.

> A host crontab entry was considered as a temporary measure but intentionally
> skipped — use `docker compose run --rm model-init` (or the scheduler, once
> built) instead.
