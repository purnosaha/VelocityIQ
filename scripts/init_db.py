#!/usr/bin/env python3
"""
VelocityIQ Database Initialization Script

This script initializes the DuckDB database with the VelocityIQ schema.
It reads the SQL initialization file and executes it against the database.

Usage:
    python scripts/init_db.py [--db-path /path/to/db]
"""

import os
import sys
import argparse
import logging
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Error: duckdb is not installed. Install it with: pip install duckdb")
    sys.exit(1)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_sql_file_path():
    """Get the path to the SQL initialization file."""
    # Determine the project root directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    sql_file = project_root / "sql" / "init.sql"

    if not sql_file.exists():
        raise FileNotFoundError(f"SQL initialization file not found at {sql_file}")

    return sql_file


def read_sql_file(sql_file_path):
    """Read the SQL initialization file."""
    logger.info(f"Reading SQL file: {sql_file_path}")
    with open(sql_file_path, 'r') as f:
        return f.read()


def initialize_database(db_path, sql_file_path):
    """
    Initialize the DuckDB database with the schema.

    Args:
        db_path: Path to the DuckDB database file
        sql_file_path: Path to the SQL initialization file
    """
    try:
        logger.info(f"Connecting to database: {db_path}")
        conn = duckdb.connect(str(db_path))

        # Read the SQL file
        sql_content = read_sql_file(sql_file_path)

        # Execute the SQL
        logger.info("Executing schema initialization SQL...")
        conn.execute(sql_content)
        conn.close()

        logger.info("✓ Database initialization completed successfully!")
        logger.info(f"✓ Database file: {db_path}")

        # List created tables
        conn = duckdb.connect(str(db_path), read_only=True)
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        conn.close()

        if tables:
            logger.info(f"\nCreated {len(tables)} tables:")
            for (table_name,) in sorted(tables):
                logger.info(f"  - {table_name}")

        return True

    except Exception as e:
        logger.error(f"✗ Database initialization failed: {str(e)}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Initialize VelocityIQ DuckDB database with schema"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to DuckDB database file (default: DUCKDB_PATH env var or ./data/velocityiq.duckdb)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-initialization (creates new tables even if they exist)"
    )

    args = parser.parse_args()

    # Determine database path
    db_path = args.db_path or os.environ.get('DUCKDB_PATH', './data/velocityiq.duckdb')
    db_path = Path(db_path)

    # Create data directory if it doesn't exist
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Database path: {db_path}")

    # Check if database already exists
    if db_path.exists() and not args.force:
        logger.warning(f"Database file already exists at {db_path}")
        response = input("Do you want to proceed with initialization? (y/n): ").strip().lower()
        if response != 'y':
            logger.info("Initialization cancelled.")
            return False

    # Get SQL file path
    sql_file_path = get_sql_file_path()

    # Initialize the database
    success = initialize_database(db_path, sql_file_path)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
