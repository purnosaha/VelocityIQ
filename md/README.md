# VelocityIQ Database Setup & Management

This directory contains all database schema, initialization scripts, and documentation for VelocityIQ.

## 📁 File Structure

```
sql/
├── init.sql                 # Complete SQL schema definition
├── SCHEMA.md               # Detailed schema documentation
└── README.md               # This file

scripts/
├── init_db.py              # Database initialization script
└── load_sample_data.py     # Sample data generator
```

## 🚀 Quick Start

### 1. Initialize the Database

```bash
# Create database with schema
python scripts/init_db.py --db-path ./data/velocityiq.duckdb
```

**Output**:
```
✓ Database initialization completed successfully!
✓ Database file: ./data/velocityiq.duckdb

Created 11 tables:
  - product_master
  - regional_reference
  - sales_transactions
  - seasonal_calendar
  - store_reference
  - weather_overlay
```

### 2. Load Sample Data (Optional)

```bash
# Generate 10,000 sample transactions
python scripts/load_sample_data.py --rows 10000

# Or use environment variable for database path
DUCKDB_PATH=./data/velocityiq.duckdb python scripts/load_sample_data.py --rows 50000
```

**Output**:
```
✓ Sample data loading completed successfully!

Data Summary:
  product_master: 10 rows
  regional_reference: 5 rows
  store_reference: 10 rows
  seasonal_calendar: 730 rows
  weather_overlay: 3,650 rows
  sales_transactions: 10,000 rows
```

### 3. Query the Database

```python
import duckdb

conn = duckdb.connect('./data/velocityiq.duckdb')

# Example: Top 5 products by revenue
result = conn.execute("""
    SELECT 
        sku_id,
        product_name,
        category,
        SUM(net_amount) as revenue
    FROM sales_transactions
    JOIN product_master USING(sku_id)
    GROUP BY sku_id, product_name, category
    ORDER BY revenue DESC
    LIMIT 5
""").fetchall()

for row in result:
    print(row)
```

---

## 📊 Schema Overview

The VelocityIQ schema uses a **star schema** pattern optimized for OLAP analytics:

### Fact Table
- **sales_transactions** - Core transaction data (quantity, price, amount)

### Dimension Tables
- **product_master** - Product metadata (SKU, category, brand, price)
- **store_reference** - Store locations and types
- **regional_reference** - Geographic regions and demographics
- **seasonal_calendar** - Time dimension with holidays, seasons, events
- **weather_overlay** - Regional weather data (temperature, precipitation, conditions)

**Relationships**: All dimensions connect to the fact table via foreign keys. See [SCHEMA.md](SCHEMA.md) for complete details.

---

## 🔧 Common Tasks

### Check Database Status

```bash
# Connect and inspect tables
python -c "
import duckdb
conn = duckdb.connect('./data/velocityiq.duckdb')
tables = conn.execute(\"SELECT * FROM information_schema.tables WHERE table_schema='main'\").fetchall()
for (name,) in sorted(tables):
    count = conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
    print(f'{name}: {count:,} rows')
"
```

### Query with DuckDB CLI

```bash
# If duckdb CLI is installed
duckdb ./data/velocityiq.duckdb

# Inside duckdb CLI:
.tables                      # List all tables
SELECT * FROM product_master LIMIT 5;
.quit                        # Exit
```

### Reset Database (Dangerous!)

```bash
# Delete the database file completely
rm ./data/velocityiq.duckdb

# Then reinitialize
python scripts/init_db.py
```

### Backup Database

```bash
# Copy database file to backup location
cp ./data/velocityiq.duckdb ./data/velocityiq.duckdb.backup

# Or compress for archival
tar -czf velocityiq-backup-$(date +%Y%m%d).tar.gz ./data/velocityiq.duckdb
```

### Generate More Data

```bash
# Add 50,000 more transactions to existing database
python scripts/load_sample_data.py --rows 50000
```

---

## 📋 Schema Details

### Tables

| Table | Type | Rows | Description |
|-------|------|------|-------------|
| product_master | Dimension | ~10 | Product metadata with SCD Type 2 |
| store_reference | Dimension | ~10 | Store locations and types |
| regional_reference | Dimension | ~5 | Geographic regions |
| seasonal_calendar | Dimension | ~730 | Time dimension (2025-2026) |
| weather_overlay | Dimension | ~3,650 | Regional weather (daily) |
| sales_transactions | Fact | ~10,000+ | Transaction-level sales data |

### Indexes

- **Fact table**: Composite index on (store_id, transaction_date, sku_id)
- **Foreign keys**: Indexed for join performance
- **Dimensions**: Category, brand, status, region, etc. indexed for filtering

### Views

Pre-built analytical views available:
- `v_sales_by_product` - Product revenue aggregations
- `v_sales_by_store_region` - Geographic sales analysis
- `v_daily_sales_summary` - Time-series sales data
- `v_sales_weather_context` - Sales with weather correlation

---

## 🎯 Sample Queries

### Revenue by Category

```sql
SELECT 
    pm.category,
    COUNT(*) as transactions,
    SUM(st.quantity) as units,
    SUM(st.net_amount) as revenue
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id
GROUP BY pm.category
ORDER BY revenue DESC;
```

### Online vs Physical Store Performance

```sql
SELECT
    sr.store_type,
    SUM(st.net_amount) as revenue,
    COUNT(*) as transactions,
    AVG(st.net_amount) as avg_transaction_value
FROM sales_transactions st
JOIN store_reference sr ON st.store_id = sr.store_id
GROUP BY sr.store_type;
```

### Holiday Impact

```sql
SELECT
    sc.is_holiday,
    COUNT(*) as transactions,
    SUM(st.net_amount) as revenue,
    AVG(st.net_amount) as avg_value
FROM sales_transactions st
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY sc.is_holiday;
```

### Product Price History (SCD Type 2)

```sql
SELECT
    sku_id,
    product_name,
    list_price,
    effective_date,
    end_date
FROM product_master
WHERE sku_id = 'SKU-TECH-001'
ORDER BY effective_date;
```

See [SCHEMA.md](SCHEMA.md) for more examples.

---

## 🔐 Data Quality

### Constraints Enforced

- ✅ Positive prices (list_price, unit_price > 0)
- ✅ Positive quantities (quantity > 0)
- ✅ Valid amounts (total_amount >= 0, discount_amount >= 0)
- ✅ Amount consistency (net_amount = total_amount - discount_amount)
- ✅ Valid enumerations (store_type, status, season, etc.)
- ✅ Referential integrity (all foreign keys enforced)

### Data Validation

Run validation queries:

```sql
-- Check for orphaned transactions
SELECT COUNT(*) as orphaned_products
FROM sales_transactions
WHERE sku_id NOT IN (SELECT sku_id FROM product_master);

SELECT COUNT(*) as orphaned_stores
FROM sales_transactions
WHERE store_id NOT IN (SELECT store_id FROM store_reference);

-- Check for invalid amounts
SELECT COUNT(*) as invalid_amounts
FROM sales_transactions
WHERE net_amount > total_amount OR discount_amount > total_amount;
```

---

## 🐳 Docker Setup

If using Docker with the provided docker-compose.yml:

```bash
# Build and start containers
docker-compose up -d

# Initialize database in container
docker-compose exec app python scripts/init_db.py

# Load sample data
docker-compose exec app python scripts/load_sample_data.py --rows 50000

# Query from container
docker-compose exec app python -c "
import duckdb
conn = duckdb.connect('/app/data/velocityiq.duckdb')
print(conn.execute('SELECT * FROM v_sales_by_product LIMIT 5').fetchall())
"

# View logs
docker-compose logs -f app

# Stop containers
docker-compose down
```

---

## 📈 Performance Tuning

### For Large Datasets

If working with millions of rows:

1. **Disable constraints during bulk insert**:
   ```sql
   -- Check if supported in DuckDB version
   -- INSERT INTO sales_transactions SELECT ... FROM import_staging
   ```

2. **Build indexes after load**:
   ```bash
   # Delete and recreate indexes post-load for faster initial inserts
   ```

3. **Use columnar storage** (DuckDB native):
   ```sql
   PRAGMA memory_limit='8GB';
   PRAGMA threads=4;
   ```

4. **Partition large fact tables** (future):
   ```sql
   -- Consider partitioning by year/month
   CREATE TABLE sales_2026 PARTITION OF sales_transactions ...
   ```

---

## 🚨 Troubleshooting

### "Database file not found"
```bash
# Ensure data directory exists
mkdir -p ./data

# Then reinitialize
python scripts/init_db.py
```

### "DuckDB not installed"
```bash
# Install DuckDB
pip install duckdb
```

### "Foreign key constraint violation"
The sample data loader generates random data. If you see FK violations:
```bash
# Reset and reload
rm ./data/velocityiq.duckdb
python scripts/init_db.py
python scripts/load_sample_data.py
```

### Query timeout
For large datasets, increase memory:
```python
import duckdb
conn = duckdb.connect('./data/velocityiq.duckdb')
conn.execute("PRAGMA memory_limit='4GB'")
```

---

## 📚 Documentation

- **[SCHEMA.md](SCHEMA.md)** - Complete schema documentation with examples
- **[schema_erd.drawio](../schema_erd.drawio)** - Visual Entity-Relationship Diagram
- **[init.sql](init.sql)** - Raw SQL DDL statements

---

## 🤝 Contributing

To modify the schema:

1. Edit `sql/init.sql` directly
2. Recreate the database: `rm ./data/velocityiq.duckdb && python scripts/init_db.py`
3. Update `SCHEMA.md` with changes
4. Regenerate the ERD if needed

---

## 📞 Support

For questions or issues:
1. Check [SCHEMA.md](SCHEMA.md) for detailed table/column documentation
2. Review sample queries in this README
3. Check [schema_erd.drawio](../schema_erd.drawio) for visual reference
4. Inspect `sql/init.sql` for raw SQL definitions

---

**Last Updated**: 2026-06-14  
**Schema Version**: 1.0  
**Database**: DuckDB
