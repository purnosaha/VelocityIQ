"""Test /insight API across 20+ diverse scenarios."""

import json
import httpx

BASE_URL = "http://localhost:8000"

SCENARIOS = [
    # ── Product / Revenue ──────────────────────────────────────────────────
    (1,  "Top products by revenue",
         "What are the top 10 products by net revenue?"),
    (2,  "Category revenue ranking",
         "Rank all product categories by total net revenue."),
    (3,  "Brand performance",
         "Which brands generate the most total revenue?"),
    (4,  "Low-performing products",
         "Show the 10 products with the lowest net revenue."),
    (5,  "Product revenue in Q4 2025",
         "What is the net revenue by product category in Q4 2025?"),

    # ── Store / Region ─────────────────────────────────────────────────────
    (6,  "Top stores by revenue",
         "Which 10 stores have the highest net revenue?"),
    (7,  "Online vs physical sales",
         "Compare total net revenue between online and physical stores."),
    (8,  "Region performance",
         "What is the net revenue for each region?"),
    (9,  "Store transaction count",
         "Which stores processed the most transactions?"),
    (10, "Country-level revenue",
         "Show total revenue broken down by country."),

    # ── Time / Seasonal ────────────────────────────────────────────────────
    (11, "Monthly revenue trend 2025",
         "Show total net revenue by month for the year 2025."),
    (12, "Seasonal sales comparison",
         "What is the total revenue for each season (Spring, Summer, Fall, Winter)?"),
    (13, "Holiday vs non-holiday revenue",
         "Compare total revenue on holiday vs non-holiday days."),
    (14, "Quarterly trend",
         "Show net revenue by quarter across all years."),
    (15, "Best sales day of week",
         "Which day of the week has the highest average daily revenue?"),

    # ── Weather Correlation ────────────────────────────────────────────────
    (16, "Weather condition vs revenue",
         "How does weather condition affect total revenue across all regions?"),
    (17, "Rain impact on sales",
         "What is the total revenue on rainy days compared to sunny days?"),
    (18, "Temperature and sales",
         "Show average revenue grouped by temperature ranges (cold <10°C, mild 10-25°C, hot >25°C)?"),

    # ── Cross-dimensional ─────────────────────────────────────────────────
    (19, "Category + region combo",
         "What is the net revenue by product category for each region?"),
    (20, "Top product in each region",
         "What is the best-selling product category in each region by net revenue?"),
    (21, "Marketing event revenue",
         "Which marketing events drive the highest average daily revenue?"),
    (22, "Average transaction value by store type",
         "What is the average transaction value for online stores vs physical stores?"),

    # ── Edge cases ─────────────────────────────────────────────────────────
    (23, "Empty/ambiguous question",
         "revenue"),
    (24, "Specific product lookup",
         "How many transactions have been made for SKU products in the Electronics category?"),
    (25, "Year over year comparison",
         "Compare total net revenue between 2024 and 2025."),
]


def test_scenario(n: int, label: str, question: str) -> dict:
    try:
        r = httpx.post(
            f"{BASE_URL}/insight",
            json={"question": question, "max_rows": 10},
            timeout=180.0,
        )
        d = r.json()
    except Exception as e:
        return {"n": n, "label": label, "status": "REQUEST_FAILED", "error": str(e)}

    if "detail" in d:
        return {
            "n": n, "label": label,
            "status": "API_ERROR",
            "error": d["detail"],
            "sql": d.get("sql", ""),
        }

    return {
        "n": n, "label": label,
        "status": "OK",
        "row_count": d.get("row_count", 0),
        "sql": d.get("sql", "")[:200],
        "summary": d.get("summary", "")[:300],
    }


def main():
    passed = failed = 0
    results = []

    print(f"{'#':<4} {'Label':<35} {'Status':<14} {'Rows':<6}")
    print("-" * 90)

    for n, label, question in SCENARIOS:
        res = test_scenario(n, label, question)
        results.append(res)

        status = res["status"]
        rows = res.get("row_count", "-")
        mark = "✓" if status == "OK" else "✗"
        print(f"{mark}{n:<3} {label:<35} {status:<14} {rows}")

        if status == "OK":
            passed += 1
            print(f"     SQL: {res['sql'][:110]}")
            print(f"     SUM: {res['summary'][:180]}")
        else:
            failed += 1
            print(f"     ERR: {res.get('error','')[:140]}")
            if res.get("sql"):
                print(f"     SQL: {res['sql'][:110]}")
        print()

    print("=" * 90)
    print(f"TOTAL: {len(SCENARIOS)}  PASSED: {passed}  FAILED: {failed}")

    with open("md/insight_api_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Full results saved → md/insight_api_test_results.json")


if __name__ == "__main__":
    main()
