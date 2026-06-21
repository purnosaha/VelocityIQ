#!/usr/bin/env python3
"""Docker init entrypoint: initialise the Gold schema only.

The medallion ETL service (``etl/backfill.py`` + ``etl/main.py``) now owns all
data loading — dimensions and the fact table alike. This step therefore only
applies the schema (``sql/init.sql`` via ``init_db.py``); sample-data loading
has been retired in favour of the ETL. See md/etl_pipeline.md.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DUCKDB_PATH", "/data/velocityiq.duckdb"))
SCRIPTS_DIR = Path(__file__).parent


def main() -> None:
    logger.info("Initialising schema (init.sql uses CREATE TABLE IF NOT EXISTS)…")
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "init_db.py"), "--force"],
        check=True,
    )
    logger.info("Schema ready at %s — ETL service will load Bronze→Silver→Gold.", DB_PATH)


if __name__ == "__main__":
    main()
