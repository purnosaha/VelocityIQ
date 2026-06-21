#!/usr/bin/env python3
"""
VelocityIQ Sample Data Loader

Generates and inserts realistic sample data into the VelocityIQ database.
Transactions are distributed equally across a 3-year period (June 14, 2023 - June 14, 2026)
to ensure temporal consistency and maintain referential integrity.

Key Features:
  • Evenly distributed transactions across all 1,096 days
  • All dates exist in seasonal_calendar (referential integrity)
  • All store/product/region references are valid
  • Configurable transaction volume
  • Progress logging for large datasets

Usage:
    python scripts/load_sample_data.py [--db-path /path/to/db] [--rows 10000]

Examples:
    # Load 10,000 transactions (evenly distributed across 3 years)
    python scripts/load_sample_data.py

    # Load 50,000 transactions
    python scripts/load_sample_data.py --rows 50000

    # Use custom database path
    python scripts/load_sample_data.py --db-path ./data/custom.duckdb --rows 100000
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
import random

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


# Sample data
REGIONS = [
    {"id": "REGION-US-WEST", "name": "US West", "country": "USA", "tz": "PST", "pop": 50000000, "income": "High"},
    {"id": "REGION-US-EAST", "name": "US East", "country": "USA", "tz": "EST", "pop": 80000000, "income": "High"},
    {"id": "REGION-US-MID", "name": "US Midwest", "country": "USA", "tz": "CST", "pop": 40000000, "income": "Medium"},
    {"id": "REGION-CANADA", "name": "Canada", "country": "Canada", "tz": "EST", "pop": 15000000, "income": "High"},
    {"id": "REGION-MEXICO", "name": "Mexico", "country": "Mexico", "tz": "CST", "pop": 60000000, "income": "Medium"},
]

STORES = [
    {"id": "STORE-001", "name": "Downtown Seattle", "region": "REGION-US-WEST", "type": "physical"},
    {"id": "STORE-002", "name": "Bay Area Mall", "region": "REGION-US-WEST", "type": "physical"},
    {"id": "STORE-003", "name": "West Coast Online", "region": "REGION-US-WEST", "type": "online"},
    {"id": "STORE-004", "name": "NYC Flagship", "region": "REGION-US-EAST", "type": "physical"},
    {"id": "STORE-005", "name": "Boston Metro", "region": "REGION-US-EAST", "type": "physical"},
    {"id": "STORE-006", "name": "East Coast Online", "region": "REGION-US-EAST", "type": "online"},
    {"id": "STORE-007", "name": "Chicago Center", "region": "REGION-US-MID", "type": "physical"},
    {"id": "STORE-008", "name": "Midwest Online", "region": "REGION-US-MID", "type": "online"},
    {"id": "STORE-009", "name": "Toronto Downtown", "region": "REGION-CANADA", "type": "physical"},
    {"id": "STORE-010", "name": "Mexico City", "region": "REGION-MEXICO", "type": "physical"},
]

PRODUCTS = [
    {"sku": "SKU-TECH-001", "name": "Wireless Headphones", "category": "Electronics", "brand": "AudioTech", "size": "Standard", "price": 89.99},
    {"sku": "SKU-TECH-002", "name": "USB-C Cable", "category": "Electronics", "brand": "TechLink", "size": "2M", "price": 12.99},
    {"sku": "SKU-TECH-003", "name": "Power Bank", "category": "Electronics", "brand": "ChargeMax", "size": "20000mAh", "price": 49.99},
    {"sku": "SKU-APPAREL-001", "name": "Classic T-Shirt", "category": "Apparel", "brand": "ComfortWear", "size": "L", "price": 24.99},
    {"sku": "SKU-APPAREL-002", "name": "Denim Jeans", "category": "Apparel", "brand": "StyleFit", "size": "32", "price": 79.99},
    {"sku": "SKU-APPAREL-003", "name": "Running Shoes", "category": "Apparel", "brand": "SportMax", "size": "10", "price": 129.99},
    {"sku": "SKU-HOME-001", "name": "Coffee Maker", "category": "Home", "brand": "BrewMaster", "size": "12-Cup", "price": 59.99},
    {"sku": "SKU-HOME-002", "name": "Desk Lamp", "category": "Home", "brand": "BrightLite", "size": "LED", "price": 34.99},
    {"sku": "SKU-HOME-003", "name": "Throw Pillow", "category": "Home", "brand": "ComfyHome", "size": "18x18", "price": 19.99},
    {"sku": "SKU-BEAUTY-001", "name": "Face Cream", "category": "Beauty", "brand": "SkinCare+", "size": "50ml", "price": 44.99},
]

SEASONS_DATES = {
    "Spring": [(3, 20), (6, 20)],
    "Summer": [(6, 21), (9, 22)],
    "Fall": [(9, 23), (12, 20)],
    "Winter": [(12, 21), (3, 19)],
}

MARKETING_EVENTS = [
    "Spring Sale", "Summer Clearance", "Back to School", "Black Friday",
    "Cyber Monday", "New Year Sale", "Mother's Day", "Father's Day",
]
# Most days have no marketing event; ~17% chance of an event
MARKETING_EVENT_PROBABILITY = 0.17

WEATHER_CONDITIONS = ["Clear", "Cloudy", "Rainy", "Snowy", "Overcast", "Sunny"]


def generate_product_data(conn):
    """Generate and insert product master data."""
    logger.info("Loading product master data...")
    for product in PRODUCTS:
        conn.execute(
            """
            INSERT INTO product_master
            (sku_id, product_name, category, brand, package_size, list_price,
             launch_date, status, effective_date, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product["sku"],
                product["name"],
                product["category"],
                product["brand"],
                product["size"],
                product["price"],
                datetime(2023, 6, 14).date(),  # Launch date matches start of data period
                "active",
                datetime.now(),
                True,
            ),
        )
    logger.info(f"✓ Inserted {len(PRODUCTS)} products")


def generate_region_data(conn):
    """Generate and insert regional reference data."""
    logger.info("Loading regional reference data...")
    for region in REGIONS:
        conn.execute(
            """
            INSERT INTO regional_reference
            (region_id, region_name, country, timezone, population, income_level, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                region["id"],
                region["name"],
                region["country"],
                region["tz"],
                region["pop"],
                region["income"],
                datetime.now().date(),
            ),
        )
    logger.info(f"✓ Inserted {len(REGIONS)} regions")


def generate_store_data(conn):
    """Generate and insert store reference data."""
    logger.info("Loading store reference data...")
    for store in STORES:
        conn.execute(
            """
            INSERT INTO store_reference
            (store_id, store_name, region_id, store_type, created_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                store["id"],
                store["name"],
                store["region"],
                store["type"],
                datetime.now().date(),
            ),
        )
    logger.info(f"✓ Inserted {len(STORES)} stores")


def generate_calendar_data(conn):
    """Generate and insert seasonal calendar data."""
    logger.info("Loading seasonal calendar data...")

    # 3 years of data: June 14, 2023 to June 14, 2026
    start_date = datetime(2023, 6, 14).date()
    end_date = datetime(2026, 6, 14).date()
    current = start_date
    count = 0

    while current <= end_date:
        day_of_week = current.strftime("%A")
        week_num = current.isocalendar()[1]
        month = current.month
        quarter = (month - 1) // 3 + 1
        year = current.year
        day_num = current.weekday() + 1  # 1-7 (Monday-Sunday)

        # Determine season (handles wrap-around for Winter which spans year boundary)
        season = None
        for s, date_range in SEASONS_DATES.items():
            start_month, start_day = date_range[0]
            end_month, end_day = date_range[1]
            if start_month <= end_month:
                # Normal range (e.g., Spring: Mar 20 – Jun 20)
                in_season = (
                    (month == start_month and current.day >= start_day) or
                    (month == end_month and current.day <= end_day) or
                    (start_month < month < end_month)
                )
            else:
                # Wrap-around range (e.g., Winter: Dec 21 – Mar 19)
                in_season = (
                    (month == start_month and current.day >= start_day) or
                    (month == end_month and current.day <= end_day) or
                    (month > start_month) or
                    (month < end_month)
                )
            if in_season:
                season = s
                break

        # Check if holiday (simplified: weekends + major holidays)
        is_holiday = current.weekday() >= 5  # Weekend
        if (month == 12 and current.day == 25) or \
           (month == 1 and current.day == 1) or \
           (month == 11 and current.day == 26):  # Thanksgiving
            is_holiday = True

        marketing_event = (
            random.choice(MARKETING_EVENTS)
            if random.random() < MARKETING_EVENT_PROBABILITY
            else None
        )

        conn.execute(
            """
            INSERT INTO seasonal_calendar
            (date, day_of_week, day_of_week_num, week_number, month, quarter, year,
             is_holiday, season, marketing_event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current,
                day_of_week,
                day_num,
                week_num,
                month,
                quarter,
                year,
                is_holiday,
                season,
                marketing_event,
            ),
        )

        current += timedelta(days=1)
        count += 1

    logger.info(f"✓ Inserted {count} calendar days")


def generate_weather_data(conn):
    """Generate and insert weather overlay data."""
    logger.info("Loading weather overlay data...")

    # 3 years of data: June 14, 2023 to June 14, 2026
    start_date = datetime(2023, 6, 14).date()
    end_date = datetime(2026, 6, 14).date()
    current = start_date
    count = 0

    while current <= end_date:
        for region in REGIONS:
            # Generate realistic temperature based on region and season
            month = current.month
            base_temp = 15 + (month % 12) * 3  # Simplified seasonal variation
            temp_avg = base_temp + random.randint(-5, 5)
            temp_min = temp_avg - random.randint(3, 7)
            temp_max = temp_avg + random.randint(3, 7)

            precipitation = random.choices(
                [0, random.uniform(5, 30)],
                weights=[0.7, 0.3]
            )[0]

            humidity = random.randint(40, 80)
            condition = random.choice(WEATHER_CONDITIONS)
            alert = random.choice([None, "Heat Wave", "Frost Warning"] if random.random() > 0.95 else [None])

            conn.execute(
                """
                INSERT INTO weather_overlay
                (region_id, date, temperature_avg, temperature_min, temperature_max,
                 precipitation, humidity, weather_condition, weather_alert)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region["id"],
                    current,
                    round(temp_avg, 2),
                    round(temp_min, 2),
                    round(temp_max, 2),
                    round(precipitation, 2),
                    humidity,
                    condition,
                    alert,
                ),
            )
            count += 1

        current += timedelta(days=1)

    logger.info(f"✓ Inserted {count} weather records")


def generate_sales_data(conn, num_rows=10000):
    """
    Generate and insert sample sales transaction data.

    Transactions are distributed equally across all dates in the 3-year period
    to ensure even distribution and maintain referential integrity.
    """
    logger.info(f"Loading {num_rows} sample sales transactions...")

    # 3 years of data: June 14, 2023 to June 14, 2026
    start_date = datetime(2023, 6, 14).date()
    end_date = datetime(2026, 6, 14).date()

    # Calculate number of days in the period
    total_days = (end_date - start_date).days + 1  # Include end date

    # Calculate transactions per day and remainder
    transactions_per_day = num_rows // total_days
    remainder = num_rows % total_days

    logger.info(f"Distributing {num_rows} transactions across {total_days} days")
    logger.info(f"  {transactions_per_day} transactions/day + {remainder} additional transactions")

    transaction_count = 0
    current_date = start_date
    day_index = 0

    # Iterate through each date in the period
    while current_date <= end_date:
        # Calculate how many transactions for this day
        txns_for_day = transactions_per_day
        if day_index < remainder:
            txns_for_day += 1  # Distribute remainder evenly at the start

        # Generate transactions for this day
        for txn_index in range(txns_for_day):
            store = random.choice(STORES)
            product = random.choice(PRODUCTS)

            quantity = random.randint(1, 5)
            unit_price = product["price"]
            total_amount = quantity * unit_price
            discount_amount = round(total_amount * random.choice([0, 0, 0, 0.05, 0.10, 0.15]), 2)

            # Generate unique transaction ID
            # Format: STORE-YYYYMMDD-INDEX (index zero-padded to 4 digits per day)
            transaction_id = (
                f"{store['id']}-{current_date.strftime('%Y%m%d')}-"
                f"{txn_index:04d}"
            )

            try:
                conn.execute(
                    """
                    INSERT INTO sales_transactions
                    (transaction_id, sku_id, store_id, transaction_date, quantity,
                     unit_price, total_amount, discount_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transaction_id,
                        product["sku"],
                        store["id"],
                        current_date,
                        quantity,
                        unit_price,
                        total_amount,
                        discount_amount,
                    ),
                )
                transaction_count += 1
            except Exception as e:
                err_str = str(e)
                if "UNIQUE constraint" in err_str or "PRIMARY KEY" in err_str:
                    # Duplicate from a previous run — skip silently for idempotency
                    continue
                # Anything else (FK, NOT NULL, CHECK) is a real problem; fail fast
                logger.error(f"Failed to insert transaction {transaction_id}: {err_str}")
                raise

        # Move to next day
        current_date += timedelta(days=1)
        day_index += 1

        # Log progress every 500 days
        if day_index % 500 == 0:
            days_pct = (day_index / total_days) * 100
            logger.info(f"  Processed {day_index}/{total_days} days ({days_pct:.1f}%) - "
                       f"{transaction_count:,} transactions inserted")

    logger.info(f"✓ Inserted {transaction_count} sales transactions across {total_days} days")


def load_sample_data(db_path, num_rows=10000):
    """Load all sample data into the database."""
    conn = None
    try:
        logger.info(f"Connecting to database: {db_path}")
        conn = duckdb.connect(str(db_path))

        # Insert dimension data
        generate_region_data(conn)
        generate_store_data(conn)
        generate_product_data(conn)
        generate_calendar_data(conn)
        generate_weather_data(conn)

        # Insert fact data
        generate_sales_data(conn, num_rows)

    except Exception as e:
        logger.error(f"✗ Sample data loading failed: {str(e)}")
        return False
    finally:
        if conn is not None:
            conn.close()

    logger.info("✓ Sample data loading completed successfully!")

    # Verify
    verify_conn = None
    try:
        verify_conn = duckdb.connect(str(db_path), read_only=True)
        stats = {}
        for table in ["product_master", "store_reference", "regional_reference",
                      "seasonal_calendar", "weather_overlay", "sales_transactions"]:
            count = verify_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = count

        logger.info("\nData Summary:")
        for table, count in stats.items():
            logger.info(f"  {table}: {count:,} rows")
    except Exception as e:
        logger.error(f"✗ Verification query failed: {str(e)}")
        return False
    finally:
        if verify_conn is not None:
            verify_conn.close()

    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Load sample data into VelocityIQ database"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to DuckDB database file"
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=10000,
        help="Number of sample sales transactions to generate (default: 10000)"
    )

    args = parser.parse_args()

    db_path = args.db_path or os.environ.get('DUCKDB_PATH', './data/velocityiq.duckdb')
    db_path = Path(db_path)

    if not db_path.exists():
        logger.error(f"Database file not found at {db_path}")
        logger.error("Run 'python scripts/init_db.py' first to create the database.")
        return 1

    success = load_sample_data(db_path, args.rows)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
