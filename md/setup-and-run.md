# VelocityIQ — Setup and Run Instructions

**Audience:** Receiving engineering team  
**Date:** 2026-06-22

---

## Prerequisites

- Docker and Docker Compose installed on every host (local, staging, prod)
- GitHub repository with Actions enabled
- SSH access to staging and production hosts (key-based, no password)
- `uv` installed locally (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

---

## 1. Clone and run locally

```bash
git clone https://github.com/purnosaha/VelocityIQ.git
cd VelocityIQ
docker compose up --build
```

Wait for all services to report healthy, then seed data:

```bash
# Backfill 36 months of history (run once) — also retrains ML models on completion
docker compose exec etl uv run python etl/backfill.py

# Load current month — also retrains ML models on completion
docker compose exec etl uv run python etl/main.py
```

Verify the stack is live:
```bash
curl http://localhost:8000/health          # → {"status":"ok"}
open http://localhost:8501                 # Streamlit dashboard
```

> **To wire up staging/production deploy:** complete the one-time infra setup described in the [Secrets and Configuration](cicd-design-notes.md#secrets-and-configuration) and [Deployment](cicd-design-notes.md#deployment) sections of the CI/CD design notes before pushing to `dev` or tagging a release.

---

## 2. Trigger a staging deploy

```bash
git checkout dev
git merge feature/my-feature
git push origin dev
# GitHub Actions picks up the push → builds image → deploys to staging automatically
```

---

## 3. Trigger a production release

```bash
git checkout main
git merge dev
git push origin main
git tag v1.0.0
git push origin v1.0.0
# GitHub Actions prompts the required reviewer in the UI → approve → deploys to production
```
