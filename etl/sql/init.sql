-- VelocityIQ Sales Data Schema
-- Star Schema for OLAP Analytics
-- Created: 2026-06-14
-- Target: DuckDB

-- ============================================================================
-- DIMENSION TABLES
-- ============================================================================

-- Product Master Dimension (Slowly Changing Dimension - Type 2)
CREATE TABLE IF NOT EXISTS product_master (
    sku_id VARCHAR PRIMARY KEY,
    product_name VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    brand VARCHAR NOT NULL,
    package_size VARCHAR,
    list_price DECIMAL(10, 2) NOT NULL,
    launch_date DATE,
    status VARCHAR DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_date TIMESTAMP,
    is_current BOOLEAN DEFAULT TRUE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_price_positive CHECK (list_price > 0)
);

-- Regional Reference Dimension
CREATE TABLE IF NOT EXISTS regional_reference (
    region_id VARCHAR PRIMARY KEY,
    region_name VARCHAR NOT NULL,
    country VARCHAR NOT NULL,
    timezone VARCHAR,
    population INTEGER,
    income_level VARCHAR,
    created_date DATE NOT NULL,
    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Store Reference Dimension
CREATE TABLE IF NOT EXISTS store_reference (
    store_id VARCHAR PRIMARY KEY,
    store_name VARCHAR NOT NULL,
    region_id VARCHAR NOT NULL,
    store_type VARCHAR NOT NULL CHECK (store_type IN ('online', 'physical')),
    created_date DATE NOT NULL,
    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_store_region FOREIGN KEY (region_id)
        REFERENCES regional_reference(region_id)
);

-- Seasonal Calendar Dimension
CREATE TABLE IF NOT EXISTS seasonal_calendar (
    date DATE PRIMARY KEY,
    day_of_week VARCHAR NOT NULL,
    day_of_week_num TINYINT CHECK (day_of_week_num BETWEEN 1 AND 7),
    week_number TINYINT CHECK (week_number BETWEEN 1 AND 53),
    month TINYINT CHECK (month BETWEEN 1 AND 12),
    quarter TINYINT CHECK (quarter BETWEEN 1 AND 4),
    year SMALLINT NOT NULL,
    is_holiday BOOLEAN DEFAULT FALSE,
    season VARCHAR CHECK (season IN ('Spring', 'Summer', 'Fall', 'Winter')),
    marketing_event VARCHAR
);

-- Weather Overlay Dimension
CREATE TABLE IF NOT EXISTS weather_overlay (
    region_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    temperature_avg DECIMAL(5, 2),
    temperature_min DECIMAL(5, 2),
    temperature_max DECIMAL(5, 2),
    precipitation DECIMAL(8, 2),
    humidity TINYINT CHECK (humidity BETWEEN 0 AND 100),
    weather_condition VARCHAR,
    weather_alert VARCHAR,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (region_id, date),
    CONSTRAINT fk_weather_region FOREIGN KEY (region_id)
        REFERENCES regional_reference(region_id)
);

-- ============================================================================
-- FACT TABLE
-- ============================================================================

-- Sales Transactions (Fact Table)
CREATE TABLE IF NOT EXISTS sales_transactions (
    transaction_id VARCHAR PRIMARY KEY,
    sku_id VARCHAR NOT NULL,
    store_id VARCHAR NOT NULL,
    transaction_date DATE NOT NULL,
    transaction_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10, 2) NOT NULL CHECK (unit_price >= 0),
    total_amount DECIMAL(12, 2) NOT NULL CHECK (total_amount >= 0),
    discount_amount DECIMAL(10, 2) DEFAULT 0 CHECK (discount_amount >= 0),
    net_amount DECIMAL(12, 2) GENERATED ALWAYS AS (total_amount - discount_amount),
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_sales_product FOREIGN KEY (sku_id)
        REFERENCES product_master(sku_id),
    CONSTRAINT fk_sales_store FOREIGN KEY (store_id)
        REFERENCES store_reference(store_id),
    CONSTRAINT fk_sales_calendar FOREIGN KEY (transaction_date)
        REFERENCES seasonal_calendar(date)
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Fact table indexes
CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales_transactions(sku_id);
CREATE INDEX IF NOT EXISTS idx_sales_store ON sales_transactions(store_id);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_sales_composite ON sales_transactions(store_id, transaction_date, sku_id);

-- Dimension indexes
CREATE INDEX IF NOT EXISTS idx_product_category ON product_master(category);
CREATE INDEX IF NOT EXISTS idx_product_brand ON product_master(brand);
CREATE INDEX IF NOT EXISTS idx_product_status ON product_master(status);
CREATE INDEX IF NOT EXISTS idx_store_region ON store_reference(region_id);
CREATE INDEX IF NOT EXISTS idx_store_type ON store_reference(store_type);
CREATE INDEX IF NOT EXISTS idx_regional_country ON regional_reference(country);
CREATE INDEX IF NOT EXISTS idx_calendar_quarter ON seasonal_calendar(quarter, year);
CREATE INDEX IF NOT EXISTS idx_calendar_month ON seasonal_calendar(month, year);
CREATE INDEX IF NOT EXISTS idx_weather_region ON weather_overlay(region_id);

-- ============================================================================
-- VIEWS FOR COMMON ANALYTICS QUERIES
-- ============================================================================

-- Sales Summary by Product
CREATE VIEW IF NOT EXISTS v_sales_by_product AS
SELECT
    pm.sku_id,
    pm.product_name,
    pm.category,
    pm.brand,
    COUNT(st.transaction_id) as transaction_count,
    SUM(st.quantity) as total_quantity,
    SUM(st.total_amount) as total_revenue,
    SUM(st.discount_amount) as total_discount,
    SUM(st.net_amount) as net_revenue,
    AVG(st.unit_price) as avg_unit_price,
    MIN(st.transaction_date) as first_sale_date,
    MAX(st.transaction_date) as last_sale_date
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id
GROUP BY pm.sku_id, pm.product_name, pm.category, pm.brand;

-- Sales Summary by Store and Region
CREATE VIEW IF NOT EXISTS v_sales_by_store_region AS
SELECT
    sr.store_id,
    sr.store_name,
    sr.store_type,
    rr.region_id,
    rr.region_name,
    rr.country,
    COUNT(st.transaction_id) as transaction_count,
    SUM(st.quantity) as total_quantity,
    SUM(st.total_amount) as total_revenue,
    SUM(st.net_amount) as net_revenue,
    COUNT(DISTINCT st.sku_id) as unique_products,
    COUNT(DISTINCT st.transaction_date) as active_days
FROM sales_transactions st
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN regional_reference rr ON sr.region_id = rr.region_id
GROUP BY sr.store_id, sr.store_name, sr.store_type, rr.region_id, rr.region_name, rr.country;

-- Daily Sales Summary
CREATE VIEW IF NOT EXISTS v_daily_sales_summary AS
SELECT
    st.transaction_date,
    sc.day_of_week,
    sc.week_number,
    sc.month,
    sc.quarter,
    sc.season,
    sc.is_holiday,
    COUNT(st.transaction_id) as transaction_count,
    COUNT(DISTINCT st.store_id) as active_stores,
    COUNT(DISTINCT st.sku_id) as unique_products,
    SUM(st.quantity) as total_quantity,
    SUM(st.total_amount) as total_revenue,
    SUM(st.net_amount) as net_revenue,
    AVG(st.net_amount) as avg_transaction_value
FROM sales_transactions st
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY st.transaction_date, sc.day_of_week, sc.week_number, sc.month, sc.quarter, sc.season, sc.is_holiday;

-- Sales with Weather Context
CREATE VIEW IF NOT EXISTS v_sales_weather_context AS
SELECT
    st.transaction_date,
    sr.region_id,
    rr.region_name,
    wo.temperature_avg,
    wo.precipitation,
    wo.weather_condition,
    wo.weather_alert,
    SUM(st.quantity) as total_quantity,
    SUM(st.net_amount) as total_revenue,
    COUNT(st.transaction_id) as transaction_count
FROM sales_transactions st
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN regional_reference rr ON sr.region_id = rr.region_id
LEFT JOIN weather_overlay wo ON sr.region_id = wo.region_id AND st.transaction_date = wo.date
GROUP BY st.transaction_date, sr.region_id, rr.region_name, wo.temperature_avg,
         wo.precipitation, wo.weather_condition, wo.weather_alert;

-- ============================================================================
-- REPORT VIEWS
-- ============================================================================

-- Report 1: Monthly Sales Summary (seasonal trend / YoY)
CREATE VIEW IF NOT EXISTS v_monthly_sales_summary AS
SELECT
    sc."year",
    sc.month,
    sc.quarter,
    sc.season,
    COUNT(st.transaction_id)                                                         AS transaction_count,
    SUM(st.quantity)                                                                  AS total_quantity,
    SUM(st.total_amount)                                                              AS total_revenue,
    SUM(st.net_amount)                                                                AS net_revenue,
    ROUND(SUM(st.net_amount) / NULLIF(COUNT(st.transaction_id), 0), 2)               AS avg_transaction_value,
    SUM(CASE WHEN sc.is_holiday THEN st.net_amount ELSE 0 END)                       AS holiday_net_revenue
FROM sales_transactions st
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY sc."year", sc.month, sc.quarter, sc.season;

-- Report 1 (per-slice): Monthly net revenue by product category x region.
-- Feeds the per-(category, region) SARIMA forecaster (scripts/revenue_forecast_model.py).
CREATE VIEW IF NOT EXISTS v_monthly_sales_by_category_region AS
SELECT
    pm.category,
    rr.region_id,
    rr.region_name,
    sc."year",
    sc.month,
    SUM(st.net_amount)                                                                AS net_revenue
FROM sales_transactions st
JOIN product_master pm  ON st.sku_id   = pm.sku_id  AND pm.is_current = TRUE
JOIN store_reference sr ON st.store_id = sr.store_id
JOIN regional_reference rr ON sr.region_id = rr.region_id
JOIN seasonal_calendar sc ON st.transaction_date = sc.date
GROUP BY pm.category, rr.region_id, rr.region_name, sc."year", sc.month;

-- Report 2: Category Revenue Leakage (discount erosion by category)
CREATE VIEW IF NOT EXISTS v_category_leakage AS
SELECT
    pm.category,
    SUM(st.total_amount)                                                              AS gross_revenue,
    SUM(st.discount_amount)                                                           AS total_discount,
    SUM(st.net_amount)                                                                AS net_revenue,
    ROUND(SUM(st.discount_amount) / NULLIF(SUM(st.total_amount), 0), 4)              AS discount_rate,
    COUNT(st.transaction_id)                                                          AS transaction_count,
    SUM(st.quantity)                                                                  AS total_quantity,
    ROUND(SUM(st.net_amount) / NULLIF(COUNT(st.transaction_id), 0), 2)               AS revenue_per_transaction
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
GROUP BY pm.category;

-- Report 5: Discount Effectiveness (band analysis — do discounts lift units?)
CREATE VIEW IF NOT EXISTS v_discount_effectiveness AS
SELECT
    pm.category,
    CASE
        WHEN st.total_amount = 0 OR st.discount_amount = 0     THEN '0%'
        WHEN st.discount_amount / st.total_amount < 0.10        THEN '0-10%'
        WHEN st.discount_amount / st.total_amount < 0.20        THEN '10-20%'
        ELSE '20%+'
    END                                                                               AS discount_band,
    COUNT(st.transaction_id)                                                          AS transaction_count,
    ROUND(AVG(st.quantity), 2)                                                        AS avg_quantity,
    ROUND(AVG(st.net_amount), 2)                                                      AS avg_net_amount,
    SUM(st.net_amount)                                                                AS net_revenue,
    SUM(st.discount_amount)                                                           AS total_discount
FROM sales_transactions st
JOIN product_master pm ON st.sku_id = pm.sku_id AND pm.is_current = TRUE
GROUP BY pm.category, discount_band;

-- Report 8: SKU Revenue Concentration / Pareto
CREATE VIEW IF NOT EXISTS v_sku_revenue_pareto AS
SELECT
    sku_id,
    product_name,
    category,
    brand,
    net_revenue,
    RANK() OVER (ORDER BY net_revenue DESC)                                           AS revenue_rank,
    ROUND(net_revenue / NULLIF(SUM(net_revenue) OVER (), 0), 4)                      AS revenue_pct,
    ROUND(SUM(net_revenue) OVER (ORDER BY net_revenue DESC) /
          NULLIF(SUM(net_revenue) OVER (), 0), 4)                                     AS cumulative_revenue_pct
FROM v_sales_by_product;

-- ============================================================================
-- STORED PROCEDURES / FUNCTIONS
-- ============================================================================

-- Function to generate transaction IDs
CREATE MACRO IF NOT EXISTS generate_transaction_id(store_id, transaction_date) AS
    CONCAT(store_id, '-', STRFTIME(transaction_date, '%Y%m%d'), '-', LPAD(CAST(RANDOM() % 1000000 AS VARCHAR), 6, '0'));

-- Function to calculate fiscal quarter
CREATE MACRO IF NOT EXISTS get_fiscal_quarter(month_val) AS
    CASE
        WHEN month_val IN (1, 2, 3) THEN 1
        WHEN month_val IN (4, 5, 6) THEN 2
        WHEN month_val IN (7, 8, 9) THEN 3
        ELSE 4
    END;

-- ============================================================================
-- TABLE CONSTRAINTS & DOCUMENTATION
-- ============================================================================

-- COMMENT ON TABLES
-- product_master: Slowly Changing Dimension (Type 2) - tracks historical product changes
-- store_reference: Static dimension with foreign key to region
-- regional_reference: Base dimension for geographic hierarchy
-- seasonal_calendar: Time dimension for temporal analysis
-- weather_overlay: Optional dimension for environmental context
-- sales_transactions: Fact table with aggregated measures (quantity, price, amount)
