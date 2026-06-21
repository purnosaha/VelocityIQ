# VelocityIQ Sales Data Schema

## Overview

This document describes the data schema for the VelocityIQ sales intelligence platform. The schema uses a **star schema** design optimized for OLAP (Online Analytical Processing) analytics queries.

**Database**: DuckDB  
**Schema Type**: Star Schema (Fact + Dimensions)  
**Created**: 2026-06-14

---

## Schema Architecture

```
                    DIMENSIONS
                        |
    +-------------------+-------------------+
    |                   |                   |
product_master      store_reference    seasonal_calendar
    |                   |                   |
    +----------+--------+---------+---------+
             |         |         |
         FACT TABLE: sales_transactions
         |         |         |
    +----+---------+--------+------+
    |                              |
regional_reference         weather_overlay
```

---

## Dimension Tables

### 1. **product_master** (Slowly Changing Dimension - Type 2)
Tracks product metadata with historical changes over time.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| sku_id | VARCHAR | PK | Unique stock keeping unit identifier |
| product_name | VARCHAR | NOT NULL | Product display name |
| category | VARCHAR | NOT NULL | Product category (e.g., Electronics, Apparel) |
| brand | VARCHAR | NOT NULL | Brand name |
| package_size | VARCHAR | NULLABLE | Package size (e.g., 500ml, Large) |
| list_price | DECIMAL(10,2) | NOT NULL, CHECK > 0 | Official list price |
| launch_date | DATE | NULLABLE | Product launch date |
| status | VARCHAR | CHECK IN ('active', 'inactive') | Current product status |
| effective_date | TIMESTAMP | DEFAULT NOW | SCD Type 2 effective date |
| end_date | TIMESTAMP | NULLABLE | SCD Type 2 end date (NULL = current) |
| is_current | BOOLEAN | DEFAULT TRUE | Flag for current record |
| last_updated | TIMESTAMP | DEFAULT NOW | Last modification timestamp |

**Indexes**:
- PK: sku_id
- idx_product_category
- idx_product_brand
- idx_product_status

**Use Case**: Filter products by category/brand, track pricing changes over time

---

### 2. **store_reference**
Maps stores to regions and tracks store metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| store_id | VARCHAR | PK | Unique store identifier |
| store_name | VARCHAR | NOT NULL | Store display name |
| region_id | VARCHAR | FK → regional_reference | Region where store is located |
| store_type | VARCHAR | CHECK IN ('online', 'physical') | Store channel type |
| created_date | DATE | NOT NULL | Store creation/opening date |
| updated_date | TIMESTAMP | DEFAULT NOW | Last modification timestamp |

**Indexes**:
- PK: store_id
- FK: region_id
- idx_store_type

**Use Case**: Segment sales by store, analyze online vs physical performance

---

### 3. **regional_reference**
Base geographic dimension with demographic context.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| region_id | VARCHAR | PK | Unique region identifier |
| region_name | VARCHAR | NOT NULL | Region display name |
| country | VARCHAR | NOT NULL | Country code or name |
| timezone | VARCHAR | NULLABLE | Timezone (e.g., UTC-5, EST) |
| population | INTEGER | NULLABLE | Region population estimate |
| income_level | VARCHAR | NULLABLE | Income classification (e.g., High, Medium, Low) |
| created_date | DATE | NOT NULL | Record creation date |
| updated_date | TIMESTAMP | DEFAULT NOW | Last modification timestamp |

**Indexes**:
- PK: region_id
- idx_regional_country

**Use Case**: Geographic hierarchies, regional market analysis, demographic context

---

### 4. **seasonal_calendar**
Time dimension with seasonal and promotional context.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| date | DATE | PK | Calendar date |
| day_of_week | VARCHAR | NOT NULL | Day name (Monday, Tuesday, etc.) |
| day_of_week_num | TINYINT | CHECK 1-7 | Day number (1=Sunday, 7=Saturday) |
| week_number | TINYINT | CHECK 1-53 | ISO week number |
| month | TINYINT | CHECK 1-12 | Month number |
| quarter | TINYINT | CHECK 1-4 | Quarter number |
| year | SMALLINT | NOT NULL | Year |
| is_holiday | BOOLEAN | DEFAULT FALSE | Holiday flag |
| season | VARCHAR | CHECK IN ('Spring', 'Summer', 'Fall', 'Winter') | Season name |
| marketing_event | VARCHAR | NULLABLE | Marketing event name (e.g., "Black Friday", "Spring Sale") |

**Indexes**:
- PK: date
- idx_calendar_quarter
- idx_calendar_month

**Use Case**: Temporal analysis, seasonal trends, holiday impact on sales

---

### 5. **weather_overlay**
Regional weather conditions as optional context for sales analysis.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| region_id | VARCHAR | PK, FK | Region identifier |
| date | DATE | PK, FK | Date of weather record |
| temperature_avg | DECIMAL(5,2) | NULLABLE | Average temperature (°C or °F) |
| temperature_min | DECIMAL(5,2) | NULLABLE | Minimum daily temperature |
| temperature_max | DECIMAL(5,2) | NULLABLE | Maximum daily temperature |
| precipitation | DECIMAL(8,2) | NULLABLE | Rainfall amount (mm) |
| humidity | TINYINT | CHECK 0-100 | Relative humidity percentage |
| weather_condition | VARCHAR | NULLABLE | Condition (e.g., "Clear", "Rainy", "Cloudy") |
| weather_alert | VARCHAR | NULLABLE | Active alerts (e.g., "Heat Wave", "Frost Warning") |
| created_date | TIMESTAMP | DEFAULT NOW | Record creation date |

**Indexes**:
- PK: (region_id, date)
- FK: region_id
- idx_weather_region

**Use Case**: Correlate weather patterns with sales performance, seasonal demand analysis

**Note**: This is an optional/reference dimension. Sales facts may not always have matching weather records.

---

## Fact Table

### **sales_transactions**
Core transactional fact table capturing every sales event.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| transaction_id | VARCHAR | PK | Unique transaction identifier |
| sku_id | VARCHAR | FK → product_master | Product sold |
| store_id | VARCHAR | FK → store_reference | Store where sale occurred |
| transaction_date | DATE | FK → seasonal_calendar | Date of transaction |
| transaction_time | TIMESTAMP | DEFAULT NOW | Exact time of transaction |
| quantity | INTEGER | NOT NULL, CHECK > 0 | Units sold |
| unit_price | DECIMAL(10,2) | NOT NULL, CHECK ≥ 0 | Price per unit |
| total_amount | DECIMAL(12,2) | NOT NULL, CHECK ≥ 0 | Gross revenue (qty × unit_price) |
| discount_amount | DECIMAL(10,2) | DEFAULT 0, CHECK ≥ 0 | Discount applied |
| net_amount | DECIMAL(12,2) | GENERATED | Net revenue (total_amount - discount_amount) |
| created_date | TIMESTAMP | DEFAULT NOW | Record creation date |

**Indexes** (for query optimization):
- PK: transaction_id
- idx_sales_sku
- idx_sales_store
- idx_sales_date
- idx_sales_composite(store_id, transaction_date, sku_id)

**Granularity**: One row per transaction  
**Volume**: Expected millions of rows  
**Use Case**: Foundation for all analytical queries

---

## Slowly Changing Dimensions (SCD)

### **Type 2: product_master**

The `product_master` table uses SCD Type 2 to maintain product history:

- **effective_date**: When the record became active
- **end_date**: When the record stopped being active (NULL = current)
- **is_current**: Boolean flag for quick identification of current records

**Example**: If a product's price changes:

| sku_id | product_name | list_price | is_current | effective_date | end_date |
|--------|--------------|-----------|-----------|---|---|
| SKU-001 | Widget A | 9.99 | FALSE | 2026-01-01 | 2026-05-31 |
| SKU-001 | Widget A | 11.99 | TRUE | 2026-06-01 | NULL |

This allows historical analysis: "What was the product price on any given date?"

---

## Views for Analytics

### 1. **v_sales_by_product**
Aggregates sales metrics by product.

```sql
SELECT * FROM v_sales_by_product
WHERE category = 'Electronics'
ORDER BY net_revenue DESC;
```

Metrics: transaction_count, total_quantity, total_revenue, net_revenue, avg_unit_price

---

### 2. **v_sales_by_store_region**
Aggregates sales by store and geographic region.

```sql
SELECT * FROM v_sales_by_store_region
WHERE country = 'USA'
ORDER BY net_revenue DESC;
```

Metrics: transaction_count, total_revenue, unique_products, active_days

---

### 3. **v_daily_sales_summary**
Daily aggregations with temporal context (week, month, season, etc.).

```sql
SELECT * FROM v_daily_sales_summary
WHERE is_holiday = TRUE;
```

Metrics: transaction_count, active_stores, total_revenue, avg_transaction_value

---

### 4. **v_sales_weather_context**
Links sales to regional weather conditions.

```sql
SELECT * FROM v_sales_weather_context
WHERE temperature_avg > 25 AND precipitation > 10;
```

Useful for: Seasonal demand analysis, weather impact modeling

---

## Relationships & Foreign Keys

```
sales_transactions
├─── FK: sku_id → product_master.sku_id
├─── FK: store_id → store_reference.store_id
└─── FK: transaction_date → seasonal_calendar.date

store_reference
└─── FK: region_id → regional_reference.region_id

weather_overlay
├─── FK: region_id → regional_reference.region_id
└─── FK: date → seasonal_calendar.date
```

**Cardinality**:
- Fact:Dimension = Many:One
- 1 product can have many sales
- 1 store can have many sales
- 1 date can have many sales
- 1 region can have many stores

---

## Data Types & Precision

| Type | Usage | Range/Notes |
|------|-------|------------|
| VARCHAR | IDs, names, categories | Variable length strings |
| DATE | Calendar dates | YYYY-MM-DD |
| TIMESTAMP | Transaction times | Includes time component |
| INTEGER | Quantities | Whole numbers only |
| DECIMAL(10,2) | Currency amounts | 8 digits + 2 decimals, precise for money |
| DECIMAL(12,2) | Revenue aggregations | 10 digits + 2 decimals |
| TINYINT | Small integers | -128 to 127 |
| SMALLINT | Years | -32768 to 32767 |
| BOOLEAN | True/False flags | 0 = FALSE, 1 = TRUE |

---

## Query Examples

### Top 10 Products by Revenue (Last 30 Days)

```sql
SELECT 
    pm.sku_id,
    pm.product_name,
    pm.category,
    SUM(st.net_amount) as revenue,
    SUM(st.quantity) as units_sold,
    COUNT(*) as transactions
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id
WHERE st.transaction_date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY pm.sku_id, pm.product_name, pm.category
ORDER BY revenue DESC
LIMIT 10;
```

### Sales by Region with Weather Context

```sql
SELECT 
    rr.region_name,
    st.transaction_date,
    wo.weather_condition,
    SUM(st.net_amount) as revenue,
    AVG(wo.temperature_avg) as avg_temp
FROM sales_transactions st
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN regional_reference rr ON sr.region_id = rr.region_id
LEFT JOIN weather_overlay wo ON rr.region_id = wo.region_id AND st.transaction_date = wo.date
WHERE st.transaction_date >= '2026-01-01'
GROUP BY rr.region_name, st.transaction_date, wo.weather_condition;
```

### Holiday vs Non-Holiday Sales Comparison

```sql
SELECT 
    sc.is_holiday,
    COUNT(*) as transactions,
    SUM(st.quantity) as units,
    SUM(st.net_amount) as revenue,
    AVG(st.net_amount) as avg_transaction_value
FROM sales_transactions st
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY sc.is_holiday;
```

### Product Price Changes (SCD Type 2)

```sql
SELECT 
    sku_id,
    product_name,
    list_price,
    effective_date,
    end_date,
    is_current
FROM product_master
WHERE sku_id = 'SKU-001'
ORDER BY effective_date;
```

---

## Initialization & Setup

### Initialize Database

```bash
python scripts/init_db.py --db-path ./data/velocityiq.duckdb
```

### Connect from Python

```python
import duckdb

conn = duckdb.connect('./data/velocityiq.duckdb')
result = conn.execute("SELECT * FROM product_master LIMIT 5").fetchall()
```

### Connect from SQL Tools

DuckDB is compatible with standard SQL clients. Use:
- **URL**: `duckdb:./data/velocityiq.duckdb`
- **Driver**: DuckDB ODBC (optional)

---

## Performance Considerations

### Indexes
- Composite index on fact table (store_id, transaction_date, sku_id) for common GROUP BY patterns
- Foreign key columns indexed for join performance
- Category/brand columns indexed for filtering

### Partitioning (Future)
For large datasets, consider partitioning sales_transactions by year/month:
```sql
CREATE TABLE sales_transactions_2026 AS 
SELECT * FROM sales_transactions 
WHERE YEAR(transaction_date) = 2026;
```

### Aggregations
Pre-materialized views (`v_daily_sales_summary`, etc.) available for fast reporting.

---

## Constraints & Data Quality

1. **Positive Prices**: list_price, unit_price must be > 0
2. **Valid Quantities**: quantity must be > 0
3. **Amount Consistency**: total_amount = quantity × unit_price
4. **Discount Logic**: discount_amount ≤ total_amount
5. **Status Values**: Only 'active' or 'inactive'
6. **Store Type**: Only 'online' or 'physical'
7. **Referential Integrity**: Foreign keys enforced on all dimension references

---

## Future Enhancements

- [ ] Customer dimension (customer_master)
- [ ] Supplier/vendor dimension
- [ ] Promotion dimension (marketing campaigns)
- [ ] Inventory snapshot dimension
- [ ] Time-based facts (stock levels, prices at specific times)
- [ ] Geography hierarchy (country → state → city)
- [ ] Product hierarchy (category → subcategory → line)

---

## Support & Documentation

For questions or schema modifications, refer to:
- `sql/init.sql` - Complete DDL statements
- `schema_erd.drawio` - Visual entity-relationship diagram
- `scripts/init_db.py` - Database initialization script
