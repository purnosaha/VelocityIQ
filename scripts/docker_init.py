#!/usr/bin/env python3
"""Docker init entrypoint: initialise schema then load sample data if the DB is empty."""
import logging
import os
import subprocess
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DUCKDB_PATH", "/data/velocityiq.duckdb"))
SAMPLE_ROWS = int(os.environ.get("SAMPLE_DATA_ROWS", "10000"))
SCRIPTS_DIR = Path(__file__).parent


def main() -> None:
    # Step 1 — schema init (always runs; init.sql uses CREATE TABLE IF NOT EXISTS)
    logger.info("Step 1/2: initialising schema…")
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "init_db.py"), "--force"],
        check=True,
    )

    # Step 2 — check whether data is already present
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    row_count = conn.execute("SELECT COUNT(*) FROM sales_transactions").fetchone()[0]
    conn.close()

    if row_count > 0:
        logger.info(
            "Step 2/2: %s transactions already loaded — skipping data load.",
            f"{row_count:,}",
        )
        return

    # Step 3 — load sample data
    logger.info("Step 2/2: loading %s sample transactions…", f"{SAMPLE_ROWS:,}")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "load_sample_data.py"),
            "--rows",
            str(SAMPLE_ROWS),
        ],
        check=True,
    )
    logger.info("Initialisation complete.")


if __name__ == "__main__":
    main()
