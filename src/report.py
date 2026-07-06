"""
Household spend report — across all imported accounts.

Run: .venv/bin/python src/report.py            (all years)
     .venv/bin/python src/report.py --year 2025
"""

import argparse
from schema import get_conn

SIGNED_AMOUNT = "CASE WHEN t.direction = 'credit' THEN -t.amount ELSE t.amount END"

# Spend transactions: real charges and refunds, no payments or transfers,
# no confirmed duplicates. The categories type='spend' filter adds a second
# layer once categories are typed; the transaction_type filter handles it
# correctly even while all categories are still the default type='spend'.
SPEND_FILTER = """
    JOIN categories c ON c.id = t.category_id
    WHERE t.transaction_type NOT IN ('payment', 'transfer')
      AND t.duplicate_status != 'confirmed_duplicate'
"""


def report(year: int = None):
    conn = get_conn()
    year_filter = "AND substr(t.transaction_date, 1, 4) = :year" if year else ""
    params = {"year": str(year)} if year else {}

    label = f"HOUSEHOLD SPEND REPORT — {year}" if year else "HOUSEHOLD SPEND REPORT — all years"
    print("=" * 60)
    print(label)
    print("=" * 60)

    # By category
    print("\n--- Spend by category (net of refunds; excludes payments/transfers) ---")
    rows = conn.execute(f"""
        SELECT c.name AS category, COUNT(*) AS txns,
               ROUND(SUM({SIGNED_AMOUNT}), 2) AS total
        FROM transactions t
        {SPEND_FILTER} {year_filter}
        GROUP BY c.name
        ORDER BY total DESC
    """, params).fetchall()
    for r in rows:
        print(f"  {r['category']:<20} {r['txns']:>4} txns   ${r['total']:>10.2f}")

    total = conn.execute(f"""
        SELECT ROUND(SUM({SIGNED_AMOUNT}), 2)
        FROM transactions t
        {SPEND_FILTER} {year_filter}
    """, params).fetchone()[0]
    print(f"\n  {'TOTAL':<20}          ${(total or 0):>10.2f}")

    # By payer
    print("\n--- Spend by payer ---")
    rows = conn.execute(f"""
        SELECT u.display_name, COUNT(*) AS txns,
               ROUND(SUM({SIGNED_AMOUNT}), 2) AS total
        FROM transactions t
        JOIN users u ON t.owner_id = u.id
        {SPEND_FILTER} {year_filter}
        GROUP BY u.display_name
        ORDER BY total DESC
    """, params).fetchall()
    for r in rows:
        print(f"  {r['display_name']:<20} {r['txns']:>4} txns   ${r['total']:>10.2f}")

    # Excluded: payments between own accounts and confirmed duplicates
    print("\n--- Excluded from spend (payments, confirmed duplicates) ---")
    rows = conn.execute(f"""
        SELECT COALESCE(c.name, 'Uncategorized') AS category,
               COUNT(*) AS txns,
               ROUND(SUM({SIGNED_AMOUNT}), 2) AS total
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE (t.transaction_type IN ('payment', 'transfer')
               OR t.duplicate_status = 'confirmed_duplicate')
          {year_filter.replace('t.transaction_date', 't.transaction_date')}
        GROUP BY category
        ORDER BY total DESC
    """, params).fetchall()
    for r in rows:
        print(f"  {r['category']:<20} {r['txns']:>4} txns   ${r['total']:>10.2f}")

    # Uncategorized — needs review
    print("\n--- Uncategorized (needs review) ---")
    rows = conn.execute(f"""
        SELECT t.merchant_normalized, t.amount, t.transaction_date
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE c.name = 'Uncategorized' {year_filter}
        ORDER BY t.amount DESC
        LIMIT 30
    """, params).fetchall()
    if rows:
        for r in rows:
            print(f"  ${r['amount']:>8.2f}  {r['transaction_date']}  {r['merchant_normalized']}")
        n_total = conn.execute(f"""
            SELECT COUNT(*) FROM transactions t
            JOIN categories c ON c.id = t.category_id
            WHERE c.name = 'Uncategorized' {year_filter}
        """, params).fetchone()[0]
        if n_total > 30:
            print(f"  ... and {n_total - 30} more")
    else:
        print("  None.")

    # Imported files summary
    print("\n--- Imported files (all years) ---")
    rows = conn.execute("""
        SELECT f.source_filename, f.row_count, a.institution, a.account_name
        FROM import_files f JOIN accounts a ON f.account_id = a.id
        ORDER BY f.imported_at
    """).fetchall()
    for r in rows:
        print(f"  {r['source_filename']:<35} {r['row_count']:>4} rows   {r['institution']} {r['account_name']}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Household spend report")
    parser.add_argument("--year", type=int, default=None, help="Scope report to a single year, e.g. 2025")
    args = parser.parse_args()
    report(year=args.year)
