"""Shared fixtures for report API tests.

Tests run against FastAPI's in-process TestClient so no server needs to be
running. They require the real DuckDB file (with data loaded). If the file is
absent, tests are skipped automatically.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable (main.py lives there).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.environ.get("DUCKDB_PATH", str(PROJECT_ROOT / "data" / "velocityiq.duckdb"))


@pytest.fixture(scope="module")
def client():
    """Return a TestClient backed by the real database. Skip if DB is absent."""
    if not Path(DB_PATH).exists():
        pytest.skip(f"Database not found at {DB_PATH}. Run load_sample_data.py first.")

    os.environ["DUCKDB_PATH"] = DB_PATH

    from starlette.testclient import TestClient
    import main  # noqa: PLC0415 — imported here so env var is set first

    return TestClient(main.app)
