"""Run the 3 previously-failed scenarios + 3 random passing ones."""

import httpx

BASE_URL = "http://localhost:8000"

SCENARIOS = [
    # ── 3 previously failed ───────────────────────────────────────────────
    (16, "Weather condition vs revenue [PREV FAIL]",
         "How does weather condition affect total revenue across all regions?"),
    (20, "Top product in each region [PREV FAIL]",
         "What is the best-selling product category in each region by net revenue?"),
    (25, "Year over year comparison [PREV FAIL]",
         "Compare total net revenue between 2024 and 2025."),
    # ── 3 random passing ones (3, 11, 13) ────────────────────────────────
    (3,  "Brand performance [sanity]",
         "Which brands generate the most total revenue?"),
    (11, "Monthly revenue trend 2025 [sanity]",
         "Show total net revenue by month for the year 2025."),
    (13, "Holiday vs non-holiday revenue [sanity]",
         "Compare total revenue on holiday vs non-holiday days."),
]


def test(n, label, question):
    try:
        r = httpx.post(f"{BASE_URL}/insight",
                       json={"question": question, "max_rows": 10},
                       timeout=180.0)
        d = r.json()
    except Exception as e:
        return {"n": n, "label": label, "status": "REQUEST_FAILED", "error": str(e)}
    if "detail" in d:
        return {"n": n, "label": label, "status": "API_ERROR",
                "error": d["detail"], "sql": d.get("sql", "")}
    return {"n": n, "label": label, "status": "OK",
            "row_count": d.get("row_count", 0),
            "sql": d.get("sql", "")[:200],
            "summary": d.get("summary", "")[:300]}


passed = failed = 0
for n, label, question in SCENARIOS:
    res = test(n, label, question)
    mark = "✓" if res["status"] == "OK" else "✗"
    print(f"{mark} #{n} {label}  →  {res['status']}  (rows: {res.get('row_count','-')})")
    if res["status"] == "OK":
        passed += 1
        print(f"   SQL: {res['sql'][:110]}")
        print(f"   SUM: {res['summary'][:180]}")
    else:
        failed += 1
        print(f"   ERR: {res.get('error','')[:150]}")
        if res.get("sql"):
            print(f"   SQL: {res['sql'][:110]}")
    print()

print(f"RESULT: {passed} passed / {failed} failed")
