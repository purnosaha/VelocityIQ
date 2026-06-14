# VelocityIQ Database Schema Setup - Complete Guide

## ✅ What Was Created

Your VelocityIQ project now has a complete, production-ready database schema based on a **star schema** design optimized for sales analytics. Here's what has been set up:

---

## 📁 Files Created

### 1. **SQL Schema Definition** (`sql/init.sql`)
- Complete DDL (Data Definition Language) statements
- 1 Fact Table (sales_transactions)
- 5 Dimension Tables (product_master, store_reference, regional_reference, seasonal_calendar, weather_overlay)
- 10 Performance indexes
- 4 Analytical views
- 2 SQL functions/macros
- **Size**: 9.4 KB | **Lines**: 300+

### 2. **Schema Documentation** (`sql/SCHEMA.md`)
- Detailed description of each table and column
- Data types and constraints explained
- Slowly Changing Dimension (SCD Type 2) implementation
- Sample queries and use cases
- **Size**: 13 KB | **Sections**: 20+ comprehensive sections

### 3. **Setup README** (`sql/README.md`)
- Quick start guide
- Common tasks and troubleshooting
- Docker integration instructions
- Performance tuning recommendations
- **Size**: 9.4 KB | **Coverage**: Complete setup reference

### 4. **Database Initialization Script** (`scripts/init_db.py`)
- Python script to create database and schema
- Creates DuckDB database file
- Validates table creation
- **Usage**: `python scripts/init_db.py --db-path ./data/velocityiq.duckdb`

### 5. **Sample Data Loader** (`scripts/load_sample_data.py`)
- Generates realistic sample data
- Creates 10+ products, 5 regions, 10 stores
- Generates 2-year calendar (2025-2026)
- Creates configurable number of transactions (default: 10,000)
- **Usage**: `python scripts/load_sample_data.py --rows 10000`

### 6. **Entity-Relationship Diagram** (`schema_erd.drawio`)
- Visual representation of all tables and relationships
- Crow's foot notation for cardinality
- Star schema layout
- Can be opened/edited in [draw.io](https://app.diagrams.net/)

---

## 📊 Schema Architecture

### Star Schema Layout

```
                  DIMENSIONS
                      |
    +------------------+------------------+
    |                  |                  |
product_master   store_reference   seasonal_calendar
    |                  |                  |
    +--------+--------+--------+---------+
            |         |        |
        FACT TABLE: sales_transactions
        |         |        |
    +---+--------+--------+---+
    |                        |
regional_reference   weather_overlay
```

### Core Components

**1 Fact Table**:
- `sales_transactions` — Transaction-level sales data
  - Measures: quantity, unit_price, total_amount, discount_amount, net_amount
  - Dimensions: sku_id, store_id, transaction_date
  - **Expected volume**: Millions of rows

**5 Dimension Tables**:
| Name | Purpose | Rows |
|------|---------|------|
| `product_master` | Product metadata with price history (SCD Type 2) | ~10 |
| `store_reference` | Store locations and attributes | ~10 |
| `regional_reference` | Geographic regions and demographics | ~5 |
| `seasonal_calendar` | Time dimension with holidays/events (2025-2026) | ~730 |
| `weather_overlay` | Daily regional weather data | ~3,650 |

---

## 🚀 How to Use

### Step 1: Install Dependencies

Add DuckDB to your project:

```bash
# Option A: Add to pyproject.toml
pip install duckdb

# Option B: In Docker (already configured)
# See docker-compose.yml for DuckDB service
```

### Step 2: Initialize Database

```bash
# Create database with all tables, indexes, and views
python scripts/init_db.py --db-path ./data/velocityiq.duckdb

# Output:
# ✓ Database initialization completed successfully!
# ✓ Database file: ./data/velocityiq.duckdb
# 
# Created 6 tables:
#   - product_master
#   - regional_reference
#   - sales_transactions
#   - seasonal_calendar
#   - store_reference
#   - weather_overlay
```

### Step 3: Load Sample Data (Optional)

```bash
# Generate and insert sample data
python scripts/load_sample_data.py --rows 10000

# Or generate more data
python scripts/load_sample_data.py --rows 50000

# Output:
# ✓ Sample data loading completed successfully!
# 
# Data Summary:
#   product_master: 10 rows
#   regional_reference: 5 rows
#   store_reference: 10 rows
#   seasonal_calendar: 730 rows
#   weather_overlay: 3,650 rows
#   sales_transactions: 10,000 rows
```

### Step 4: Query the Database

```python
import duckdb

# Connect to database
conn = duckdb.connect('./data/velocityiq.duckdb')

# Example: Top 5 products by revenue
results = conn.execute("""
    SELECT 
        pm.sku_id,
        pm.product_name,
        pm.category,
        SUM(st.net_amount) as revenue,
        COUNT(*) as transactions
    FROM sales_transactions st
    JOIN product_master pm ON st.sku_id = pm.sku_id
    GROUP BY pm.sku_id, pm.product_name, pm.category
    ORDER BY revenue DESC
    LIMIT 5
""").fetchall()

for row in results:
    print(row)
```

---

## 🎯 Key Features

### ✅ Star Schema Design
- Optimized for analytical queries (OLAP)
- Fast aggregations and GROUP BY operations
- Clean separation of facts and dimensions

### ✅ Data Quality
- Foreign key constraints enforced
- Check constraints for valid data (prices > 0, quantities > 0, etc.)
- Referential integrity on all relationships
- Generated columns for computed fields (net_amount)

### ✅ Performance
- Composite indexes on fact table (store_id, transaction_date, sku_id)
- Separate indexes on dimension key columns
- View materializations for common queries

### ✅ Slowly Changing Dimensions
- SCD Type 2 implementation in product_master
- Tracks historical product prices and metadata
- Supports "as-of" date analysis

### ✅ Time Intelligence
- Complete date dimension (2025-2026)
- Day of week, week number, month, quarter, year
- Holiday flags and marketing event tags
- Season classification (Spring, Summer, Fall, Winter)

### ✅ Optional Context Dimensions
- Weather data for environmental correlations
- Demographics for market analysis
- Geographic hierarchy (region → country)

---

## 📈 Pre-Built Views

Ready-to-use analytical views:

1. **v_sales_by_product** — Product revenue analysis
2. **v_sales_by_store_region** — Geographic performance
3. **v_daily_sales_summary** — Time-series data with context
4. **v_sales_weather_context** — Weather correlation analysis

**Example**: Query top performing regions:
```sql
SELECT * FROM v_sales_by_store_region
ORDER BY net_revenue DESC
LIMIT 10;
```

---

## 🔧 Configuration

### DuckDB Setup
Database is configured to use:
- **Path**: `./data/velocityiq.duckdb` (default, configurable)
- **Type**: File-based (persistent)
- **Format**: Native DuckDB format

### Environment Variables
```bash
# Set custom database path
export DUCKDB_PATH=/custom/path/velocityiq.duckdb

# Then scripts will use it automatically
python scripts/init_db.py
python scripts/load_sample_data.py
```

### Docker Integration
```bash
# Use docker-compose for containerized setup
docker-compose up -d

# Initialize in container
docker-compose exec app python scripts/init_db.py
```

---

## 📚 Documentation Files

| File | Purpose | Read For |
|------|---------|----------|
| `sql/SCHEMA.md` | Complete technical reference | Table definitions, constraints, examples |
| `sql/README.md` | Setup and usage guide | Quick start, common tasks, troubleshooting |
| `sql/init.sql` | Raw SQL definitions | Direct database migration, understanding structure |
| `schema_erd.drawio` | Visual diagram | Understanding relationships, sharing with team |
| `scripts/init_db.py` | Database creation script | Custom initialization, automation |
| `scripts/load_sample_data.py` | Data generation | Understanding data structure, populating test data |

---

## 🚨 Important Notes

### Setup Requirements
- **Python 3.12+** (from pyproject.toml)
- **DuckDB** (pip install duckdb)
- **~50 MB** disk space for sample data with 10K transactions

### Data Considerations
- Sample data is randomly generated and synthetic
- Real data should go through validation before loading
- Consider data quality checks before production use

### Production Deployment
For production:
1. Add duckdb to dependencies in pyproject.toml
2. Run init_db.py during application startup
3. Set DUCKDB_PATH to persistent storage location
4. Configure backups of database file
5. Monitor database size as transactions grow

---

## 🔍 Sample Data Included

The loader generates:

**Products**: 10 products across 4 categories
- Electronics (headphones, cables, power banks)
- Apparel (t-shirts, jeans, shoes)
- Home (coffee makers, lamps, pillows)
- Beauty (face cream)

**Stores**: 10 stores across 5 regions
- 5 US regions (West, East, Midwest)
- Canada region
- Mexico region

**Time Range**: 2025-01-01 to 2026-12-31
- Full calendar dimension
- Holiday flags for major holidays
- Marketing events (Black Friday, etc.)
- Weather data for every day and region

**Transactions**: 10,000+ sample transactions
- Dates within 2026
- Random store/product combinations
- Quantity: 1-5 units
- Discounts: 0%, 5%, 10%, 15% random

---

## ✨ Next Steps

1. **Install DuckDB**: `pip install duckdb`
2. **Initialize Database**: `python scripts/init_db.py`
3. **Load Sample Data**: `python scripts/load_sample_data.py`
4. **Explore Data**: See queries in [sql/SCHEMA.md](sql/SCHEMA.md)
5. **Integrate with App**: Import duckdb in your Python code
6. **Customize**: Modify scripts for your actual data

---

## 📖 Learning Resources

To understand more about the schema:

1. **Star Schema Basics**: https://en.wikipedia.org/wiki/Star_schema
2. **DuckDB Documentation**: https://duckdb.org/docs/
3. **SQL Window Functions**: Useful for advanced analytics
4. **Data Warehousing Concepts**: Look up "dimensional modeling"

---

## 📞 Troubleshooting

**Q: "DuckDB module not found"**  
A: Install with `pip install duckdb`

**Q: "Database file not found"**  
A: Run `python scripts/init_db.py` first

**Q: "Foreign key constraint failed"**  
A: Ensure dimensions exist before fact data; reload from scratch

**Q: "Database is locked"**  
A: Close any other connections; DuckDB allows concurrent reads

---

## 📝 Summary

✅ **Complete schema** with 1 fact table + 5 dimensions  
✅ **10 indexes** for query performance  
✅ **4 views** for common analytics  
✅ **2 automation scripts** for setup and data loading  
✅ **Comprehensive documentation** with 20+ examples  
✅ **Visual ERD diagram** for reference  
✅ **SCD Type 2** support for historical data  
✅ **Docker ready** for containerized deployment  

You're ready to start building analytics on top of this schema!

---

**Created**: 2026-06-14  
**Schema Version**: 1.0  
**Database**: DuckDB  
**Pattern**: Star Schema (OLAP Optimized)
