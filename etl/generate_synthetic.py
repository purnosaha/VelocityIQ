"""Synthetic data generator for the VelocityIQ ETL.

Produces two kinds of output:

1. **POS transaction batches** (``generate_pos_batch``) — one calendar month of
   transactions for each of the three sources, each in its own *native* schema
   (different column names, date formats, currency, price encoding) so the
   adapter layer has real schema drift to absorb. Defects are injected on
   purpose: nulls, duplicate re-sends, late arrivals, malformed dates, negative
   quantities, and orphan SKUs.

2. **Dimension seeds** (``generate_dimensions``) — the non-POS reference data:
   product master, region/store reference, and the deterministic
   seasonal_calendar + synthetic weather_overlay covering the full window. These
   feed the Gold dimensions directly (calendar is pure date math; weather is
   synthetic) so the fact FKs resolve.

The canonical product/region/store catalogues are reused from
``scripts/load_sample_data.py`` so generated keys line up with the Gold dims.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import pandas as pd

from etl import config

# Reuse the canonical catalogues so generated keys match the Gold dimensions.
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
MARKETING_EVENT_PROBABILITY = 0.17
WEATHER_CONDITIONS = ["Clear", "Cloudy", "Rainy", "Snowy", "Overcast", "Sunny"]

# Orphan SKUs deliberately NOT present in PRODUCTS / product_master.
ORPHAN_SKUS = ["SKU-GHOST-999", "SKU-UNKNOWN-000"]

# Stores routed to each POS source. In-store terminals (A/B) feed physical
# stores; the e-commerce platform feeds the online-type stores. The online
# channel reports in CAD (a channel/platform attribute) to exercise currency
# normalization in the adapter.
_PHYSICAL_STORES = [s["id"] for s in STORES if s["type"] == "physical"]
_ONLINE_STORES = [s["id"] for s in STORES if s["type"] == "online"]

# Monthly seasonal demand multiplier (mild, not uniform-random).
_MONTH_DEMAND = {
    1: 0.85, 2: 0.80, 3: 0.95, 4: 1.00, 5: 1.05, 6: 1.10,
    7: 1.05, 8: 1.00, 9: 1.05, 10: 1.10, 11: 1.35, 12: 1.45,
}
# Category-level base demand weights so the mix isn't uniform.
_CATEGORY_WEIGHT = {"Electronics": 1.3, "Apparel": 1.1, "Home": 1.0, "Beauty": 0.7}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _random_datetime_in_month(month_start: date, max_date: date | None = None) -> datetime:
    start, end = config.month_bounds(month_start)
    # For the current (partial) month, don't fabricate transactions in the
    # future — cap the upper bound at ``max_date`` (today).
    if max_date is not None and max_date < end:
        end = max(start, max_date)
    span_days = (end - start).days
    day = start + timedelta(days=random.randint(0, span_days))
    return datetime.combine(day, time(random.randint(7, 21), random.randint(0, 59)))


def _weighted_product() -> dict:
    weights = [_CATEGORY_WEIGHT[p["category"]] for p in PRODUCTS]
    return random.choices(PRODUCTS, weights=weights, k=1)[0]


def _discount_for(total: float) -> float:
    return round(total * random.choice([0, 0, 0, 0, 0.05, 0.10, 0.15]), 2)


def _maybe_null(value, rate: float = config.NULL_RATE):
    """Return None ~``rate`` of the time (used for non-key fields)."""
    return None if random.random() < rate else value


def _canonical_transactions(
    month_start: date, n: int, store_pool: list[str], max_date: date | None = None
) -> list[dict]:
    """Build ``n`` clean canonical transactions for the month, pre-native-mapping."""
    demand = _MONTH_DEMAND.get(month_start.month, 1.0)
    rows = []
    for idx in range(n):
        product = _weighted_product()
        qty = max(1, int(round(random.triangular(1, 6, 2) * demand)))
        # mild per-row price jitter around list price
        unit_price = round(product["price"] * random.uniform(0.97, 1.06), 2)
        total = round(qty * unit_price, 2)
        rows.append(
            {
                # ``seq`` is the stable native-id sequence. Duplicates copy it so
                # the re-sent transaction collides on the dedup key.
                "seq": idx,
                "ts": _random_datetime_in_month(month_start, max_date),
                "sku": product["sku"],
                "qty": qty,
                "unit_price": unit_price,
                "discount": _discount_for(total),
                "store_id": random.choice(store_pool),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Defect injection
# --------------------------------------------------------------------------- #
def _inject_defects(rows: list[dict], month_start: date) -> list[dict]:
    """Mutate/extend a clean row list with the realistic defect mix."""
    if not rows:
        return rows

    # ~2% duplicates (same txn re-sent). Append copies; native id assigned later
    # is shared via a marker so the dedup key collides.
    dup_count = max(1, int(len(rows) * config.DUPLICATE_RATE))
    for src in random.sample(rows, min(dup_count, len(rows))):
        clone = dict(src)
        clone["_dup_of"] = id(src)
        rows.append(clone)

    # Handful of late arrivals: timestamp pulled back into a prior month.
    for src in random.sample(rows, min(config.LATE_ARRIVAL_COUNT, len(rows))):
        late_month = config.add_months(month_start, -random.randint(1, 3))
        src["ts"] = _random_datetime_in_month(late_month)

    # Malformed / unparseable dates.
    for src in random.sample(rows, min(config.MALFORMED_DATE_COUNT, len(rows))):
        src["ts"] = "not-a-date"

    # Negative quantities.
    for src in random.sample(rows, min(config.NEGATIVE_QTY_COUNT, len(rows))):
        src["qty"] = -abs(src["qty"]) if isinstance(src["qty"], int) else -1

    # Orphan SKUs (not in product_master).
    for src in random.sample(rows, min(config.ORPHAN_SKU_COUNT, len(rows))):
        src["sku"] = random.choice(ORPHAN_SKUS)

    # ~5% nulls in non-key fields (unit_price / discount / store_id).
    for src in rows:
        if random.random() < config.NULL_RATE:
            field = random.choice(["unit_price", "discount", "store_id"])
            src[field] = None

    return rows


# --------------------------------------------------------------------------- #
# Native-schema emitters (one per source — this is the schema-drift surface)
# --------------------------------------------------------------------------- #
def _emit_pos_system_a(rows: list[dict], month_start: date) -> pd.DataFrame:
    """POS A: US-style date string, '$'-prefixed price string."""
    out = []
    for r in rows:
        ts = r["ts"]
        ts_str = ts.strftime("%m/%d/%Y %H:%M") if isinstance(ts, datetime) else ts
        price = r["unit_price"]
        out.append(
            {
                "txn_id": f"A-{month_start:%Y%m}-{r['seq']:05d}",
                "ts": ts_str,
                "sku": r["sku"],
                "qty": r["qty"],
                "unit_price": None if price is None else f"${price:.2f}",
                "disc": r["discount"],
                "store_id": r["store_id"],
            }
        )
    return pd.DataFrame(out)


def _emit_pos_system_b(rows: list[dict], month_start: date) -> pd.DataFrame:
    """POS B: ISO datetime, integer cents for money, 'location' for store."""
    out = []
    for r in rows:
        ts = r["ts"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else ts
        price = r["unit_price"]
        disc = r["discount"]
        out.append(
            {
                "transaction_ref": f"B-{month_start:%Y%m}-{r['seq']:05d}",
                "sale_dt": ts_str,
                "item_code": r["sku"],
                "units": r["qty"],
                "price_cents": None if price is None else int(round(price * 100)),
                "discount_cents": None if disc is None else int(round(disc * 100)),
                "location": r["store_id"],
            }
        )
    return pd.DataFrame(out)


def _emit_online_channel(rows: list[dict], month_start: date) -> pd.DataFrame:
    """Online: ISO-8601, CAD currency, line_total instead of unit price."""
    out = []
    for r in rows:
        ts = r["ts"]
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(ts, datetime) else ts
        price = r["unit_price"]
        qty = r["qty"]
        # line_total is in CAD; unit price is back-calculated downstream.
        line_total = None
        if price is not None and isinstance(qty, int) and qty != 0:
            line_total = round(price * qty / config.CAD_USD_RATE, 2)  # USD→CAD for realism
        out.append(
            {
                "order_id": f"ONL-{month_start:%Y%m}-{r['seq']:05d}",
                "created_at": ts_str,
                "product_sku": r["sku"],
                "qty_ordered": qty,
                "line_total": line_total,
                "discount": r["discount"],
                "currency": "CAD",
                "store_id": r["store_id"],
            }
        )
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Public: POS batch generation
# --------------------------------------------------------------------------- #
def generate_pos_batch(
    month_start: date, seed: int | None = None, today: date | None = None
) -> dict[str, pd.DataFrame]:
    """Generate one month of POS data for all three sources (native schemas).

    Returns a mapping ``{source_system: native_dataframe}``. Defects are baked
    in. Row volume is split ~40/35/25 across A/B/online. For the current
    (partial) month, dates are capped at ``today`` so no future-dated rows are
    fabricated.
    """
    if seed is not None:
        random.seed(seed)

    max_date = today or date.today()
    total = config.ROWS_PER_MONTH
    counts = {
        "pos_system_a": int(total * 0.40),
        "pos_system_b": int(total * 0.35),
        "online_channel": total - int(total * 0.40) - int(total * 0.35),
    }

    a_rows = _inject_defects(_canonical_transactions(month_start, counts["pos_system_a"], _PHYSICAL_STORES, max_date), month_start)
    b_rows = _inject_defects(_canonical_transactions(month_start, counts["pos_system_b"], _PHYSICAL_STORES, max_date), month_start)
    o_rows = _inject_defects(_canonical_transactions(month_start, counts["online_channel"], _ONLINE_STORES, max_date), month_start)

    return {
        "pos_system_a": _emit_pos_system_a(a_rows, month_start),
        "pos_system_b": _emit_pos_system_b(b_rows, month_start),
        "online_channel": _emit_online_channel(o_rows, month_start),
    }


# --------------------------------------------------------------------------- #
# Public: dimension seeds
# --------------------------------------------------------------------------- #
def _season_for(d: date) -> str | None:
    month, day = d.month, d.day
    for season, (lo, hi) in SEASONS_DATES.items():
        lo_m, lo_d = lo
        hi_m, hi_d = hi
        if lo_m <= hi_m:
            in_season = (
                (month == lo_m and day >= lo_d)
                or (month == hi_m and day <= hi_d)
                or (lo_m < month < hi_m)
            )
        else:  # wrap-around (Winter)
            in_season = (
                (month == lo_m and day >= lo_d)
                or (month == hi_m and day <= hi_d)
                or (month > lo_m)
                or (month < hi_m)
            )
        if in_season:
            return season
    return None


def generate_calendar(start: date, end: date) -> pd.DataFrame:
    """Deterministic seasonal_calendar rows for the inclusive [start, end] span."""
    rows = []
    cur = start
    while cur <= end:
        rows.append(
            {
                "date": cur,
                "day_of_week": cur.strftime("%A"),
                "day_of_week_num": cur.weekday() + 1,
                "week_number": cur.isocalendar()[1],
                "month": cur.month,
                "quarter": (cur.month - 1) // 3 + 1,
                "year": cur.year,
                "is_holiday": cur.weekday() >= 5
                or (cur.month == 12 and cur.day == 25)
                or (cur.month == 1 and cur.day == 1)
                or (cur.month == 11 and cur.day == 26),
                "season": _season_for(cur),
                "marketing_event": (
                    random.choice(MARKETING_EVENTS)
                    if random.random() < MARKETING_EVENT_PROBABILITY
                    else None
                ),
            }
        )
        cur += timedelta(days=1)
    return pd.DataFrame(rows)


def generate_weather(start: date, end: date) -> pd.DataFrame:
    """Synthetic weather_overlay rows for every (region, date) in the span."""
    rows = []
    cur = start
    while cur <= end:
        base_temp = 15 + (cur.month % 12) * 3
        for region in REGIONS:
            temp_avg = base_temp + random.randint(-5, 5)
            precipitation = random.choices([0.0, round(random.uniform(5, 30), 2)], weights=[0.7, 0.3])[0]
            alert = (
                random.choice(["Heat Wave", "Frost Warning"])
                if random.random() > 0.97
                else None
            )
            rows.append(
                {
                    "region_id": region["id"],
                    "date": cur,
                    "temperature_avg": round(temp_avg, 2),
                    "temperature_min": round(temp_avg - random.randint(3, 7), 2),
                    "temperature_max": round(temp_avg + random.randint(3, 7), 2),
                    "precipitation": precipitation,
                    "humidity": random.randint(40, 80),
                    "weather_condition": random.choice(WEATHER_CONDITIONS),
                    "weather_alert": alert,
                }
            )
        cur += timedelta(days=1)
    return pd.DataFrame(rows)


def generate_product_master() -> pd.DataFrame:
    """Native product reference rows (land in bronze_product_master)."""
    return pd.DataFrame(
        [
            {
                "sku": p["sku"],
                "product_name": p["name"],
                "category": p["category"],
                "brand": p["brand"],
                "package_size": p["size"],
                "list_price": p["price"],
                "launch_date": "2023-06-14",
                "status": "active",
            }
            for p in PRODUCTS
        ]
    )


def generate_region_reference() -> pd.DataFrame:
    """Combined region/store reference (land in bronze_region_reference).

    One row per store, carrying its region attributes — gold.py splits this into
    regional_reference + store_reference.
    """
    by_region = {r["id"]: r for r in REGIONS}
    rows = []
    for s in STORES:
        r = by_region[s["region"]]
        rows.append(
            {
                "store_id": s["id"],
                "store_name": s["name"],
                "store_type": s["type"],
                "region_id": r["id"],
                "region_name": r["name"],
                "country": r["country"],
                "timezone": r["tz"],
                "population": r["pop"],
                "income_level": r["income"],
            }
        )
    return pd.DataFrame(rows)


def generate_dimensions(today: date | None = None) -> dict[str, pd.DataFrame]:
    """All non-POS reference data needed before the fact load."""
    start, end = config.full_window(today)
    return {
        "product_master": generate_product_master(),
        "region_reference": generate_region_reference(),
        "calendar": generate_calendar(start, end),
        "weather": generate_weather(start, end),
    }
