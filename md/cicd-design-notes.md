# CI/CD Design Notes — VelocityIQ Production Pipeline

**Audience:** Receiving engineering team  
**Date:** 2026-06-22  
**Status:** Recommended handoff baseline

---

## Environments

| Environment | Branch / Trigger | Purpose |
|---|---|---|
| **Local** | Any branch, `docker compose up` | Developer iteration — full stack on laptop |
| **Staging** | Merge to `dev` | Integration smoke-test; safe to break |
| **Production** | Semver tag on `main` (e.g. `v1.2.0`) | Live; guarded by manual approval gate |

Each environment is a Docker Compose stack deployed to a dedicated host (VM, ECS task, or Kubernetes namespace). The only difference between staging and production is the data volume mount and the `OLLAMA_MODEL` env var (staging may run the smaller `qwen2.5:3b` to save memory; production runs `qwen2.5:7b`).

---

## Branch and Promotion Strategy

```
feature/* ──PR──► dev ──PR──► main ──tag──► production
                  │                          ▲
                  └──── auto-deploy ──► staging
```

1. **Feature branches** open PRs against `dev`. The existing `PR Review` workflow runs Ruff and Bandit inline comments automatically on every push.
2. Merge to `dev` triggers a staging deploy (new workflow to add — see below).
3. A PR from `dev` → `main` requires all gates to pass **plus** one reviewer approval.
4. A semver tag (`git tag v1.x.y && git push --tags`) on `main` triggers the production deploy job, which requires a manual approval step in GitHub Actions Environments.

---

## CI Gates — What Must Pass Before Promotion

### On every PR (already live)
| Check | Tool | Failure mode |
|---|---|---|
| Lint | `ruff check` via reviewdog | Inline PR comments; non-blocking today (`-fail-on-error=false`) — **flip to `true` before go-live** |
| Security scan | `bandit -r` via reviewdog | Same — flip to blocking |

### Add before shipping to staging
| Check | Tool | Notes |
|---|---|---|
| Unit + integration tests | `uv run pytest` | Must include `api_tests/test_revenue_forecast.py`; target ≥ 80% coverage on `main.py` and `etl/` |
| Docker build | `docker compose build` (CI matrix) | Catches dependency pin drift between app, ETL, and model-init images |
| Schema migration dry-run | `python scripts/init_db.py --force --dry-run` | Add `--dry-run` flag to init script; ensures SQL migrations apply cleanly against a blank DB |

### Production gate (in addition to all above)
- All staging checks green on the same commit SHA.
- Manual approval from one team lead via **GitHub Actions Environments** (`Settings → Environments → production → Required reviewers`).

---

## Deployment

### Staging (auto, on merge to `dev`)
```yaml
# .github/workflows/deploy-staging.yml (to create)
on:
  push:
    branches: [dev]
jobs:
  deploy:
    environment: staging
    steps:
      - uses: actions/checkout@v4
      - name: Build and push image
        run: docker compose build && docker compose push   # or push to ECR/GHCR
      - name: SSH deploy
        run: |
          ssh $STAGING_HOST "docker compose pull && docker compose up -d --remove-orphans"
```

### Production (manual tag trigger)
```yaml
# .github/workflows/deploy-production.yml (to create)
on:
  push:
    tags: ['v*']
jobs:
  deploy:
    environment: production          # blocks until reviewer approves in GitHub UI
    steps:
      - name: Deploy tagged image
        run: ssh $PROD_HOST "IMAGE_TAG=${{ github.ref_name }} docker compose up -d"
```

**Image tagging convention:** `ghcr.io/<org>/velocityiq:<semver>` — tag `latest` only on confirmed stable prod deploys.

---

## Rollback

### Fast rollback (< 2 min)
Re-deploy the previous image tag — no rebuild required:

```bash
# On the production host
IMAGE_TAG=v1.1.3 docker compose up -d
```

The DuckDB file (`./data/velocityiq.duckdb`) is a named volume. It is **not rolled back** with the application — schema migrations must be backward-compatible or explicitly reversed before triggering application rollback. Keep migration scripts in `scripts/migrations/` numbered sequentially; each must have a paired `down_*.sql`.

### Model rollback
SARIMA `.pkl` files are written to `./models/`. Pin the previous model directory in a versioned path (`./models/v1.1.3/`) so a model rollback is a symlink swap, not a retrain.

### Smoke test after any deploy
```bash
curl -f http://<host>:8000/health
curl -f http://<host>:8000/reports/sales-overview
```
Both must return `200`. Wire these as a post-deploy step in the deploy workflow.

---

## Secrets and Configuration

| Secret | Where to store | Notes |
|---|---|---|
| `DUCKDB_PATH` | Docker Compose env / host `.env` file | Never commit the actual `.duckdb` file |
| `OLLAMA_MODEL` | Docker Compose env | Swap here to change model without code change |
| `STAGING_HOST` / `PROD_HOST` | GitHub Actions Secrets | SSH target for deploy step |
| `GITHUB_TOKEN` | Auto-injected by Actions | Already used by Ruff/Bandit reviewdog jobs |

No external API keys are required — Ollama is self-hosted.

---

## Open Production Readiness Items

| Item | Priority | Owner suggestion |
|---|---|---|
| Flip Ruff + Bandit to blocking (`-fail-on-error=true`) | High | DevOps / lead |
| Add `pytest` job to PR workflow | High | Backend engineer |
| Add `--dry-run` to `init_db.py` and wire to CI | Medium | Backend engineer |
| Replace hardcoded `CAD_USD_RATE=0.73` with FX API call | Medium | ETL engineer |
| Add `down_*.sql` migration scripts | Medium | Backend engineer |
| Replace `model-init` one-shot container with scheduled cron (Airflow or GitHub Actions scheduled workflow) | Low | ML engineer |
| Pin `ollama/ollama:latest` to a digest tag for reproducibility | Low | DevOps / lead |
