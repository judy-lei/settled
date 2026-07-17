"""
Household spend report — across all imported accounts.

Run: .venv/bin/python src/report.py            (all years)
     .venv/bin/python src/report.py --year 2025
"""

import argparse
from schema import get_conn

SIGNED_AMOUNT = "CASE WHEN t.direction = 'credit' THEN -t.amount ELSE t.amount END"

# Spend conditions: real charges and refunds, no payments or transfers,
# no confirmed duplicates. No JOIN to categories — NULL-category (uncategorized)
# rows are spend and belong in totals. Use LEFT JOIN + COALESCE in by-category
# queries so they appear as 'Uncategorized' rather than being silently dropped.
_SPEND_WHERE = """
    WHERE t.transaction_type NOT IN ('payment', 'transfer')
      AND t.duplicate_status != 'confirmed_duplicate'
"""


# extra_where contract (all three functions below): a TRUSTED LITERAL SQL
# fragment only — never a caller- or user-supplied string. Bind every value
# through `params` (e.g. ":period"), never by interpolating it into extra_where.
# Today's callers pass hardcoded year/month filters; this note keeps a future
# caller (agent tools, a category filter) from turning it into injected SQL.


def spend_by_category(conn, extra_where: str = "", params: dict = None) -> list:
    """Net spend per category, NULL-category rows included as 'Uncategorized'.

    Shared by report() and evals/check_tools.py's regression check — extra_where
    lets each caller scope the period (year vs. year-month) without duplicating
    the SPEND_WHERE / join / COALESCE logic that CR-1 broke once already.
    extra_where must be a trusted literal (see contract note above).
    """
    params = params or {}
    return conn.execute(f"""
        SELECT COALESCE(c.name, 'Uncategorized') AS category,
               COUNT(*) AS txns,
               ROUND(SUM({SIGNED_AMOUNT}), 2) AS total
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        {_SPEND_WHERE} {extra_where}
        GROUP BY COALESCE(c.name, 'Uncategorized')
        ORDER BY total DESC
    """, params).fetchall()


def spend_by_payer(conn, extra_where: str = "", params: dict = None) -> list:
    """Net spend per payer (transaction owner), all qualifying rows included.

    Same spend surface as spend_by_category(), grouped by owner instead of
    category, so the two must reconcile to the same total. Extracted and shared
    with the eval so this read site is locked the same way by-category is —
    it was the un-asserted sibling query CR-1's fix left behind.
    extra_where must be a trusted literal (see contract note above).
    """
    params = params or {}
    return conn.execute(f"""
        SELECT u.display_name AS payer,
               COUNT(*) AS txns,
               ROUND(SUM({SIGNED_AMOUNT}), 2) AS total
        FROM transactions t
        JOIN users u ON t.owner_id = u.id
        {_SPEND_WHERE} {extra_where}
        GROUP BY u.display_name
        ORDER BY total DESC
    """, params).fetchall()


def spend_total(conn, extra_where: str = "", params: dict = None) -> float:
    """Net spend total, NULL-category rows included. See spend_by_category().
    extra_where must be a trusted literal (see contract note above)."""
    params = params or {}
    return conn.execute(f"""
        SELECT ROUND(SUM({SIGNED_AMOUNT}), 2)
        FROM transactions t
        {_SPEND_WHERE} {extra_where}
    """, params).fetchone()[0]


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
    rows = spend_by_category(conn, year_filter, params)
    for r in rows:
        print(f"  {r['category']:<20} {r['txns']:>4} txns   ${r['total']:>10.2f}")

    total = spend_total(conn, year_filter, params)
    print(f"\n  {'TOTAL':<20}          ${(total or 0):>10.2f}")

    # By payer
    print("\n--- Spend by payer ---")
    rows = spend_by_payer(conn, year_filter, params)
    for r in rows:
        print(f"  {r['payer']:<20} {r['txns']:>4} txns   ${r['total']:>10.2f}")

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
          {year_filter}
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
        WHERE t.category_id IS NULL {year_filter}
        ORDER BY t.amount DESC
        LIMIT 30
    """, params).fetchall()
    if rows:
        for r in rows:
            print(f"  ${r['amount']:>8.2f}  {r['transaction_date']}  {r['merchant_normalized']}")
        n_total = conn.execute(f"""
            SELECT COUNT(*) FROM transactions t
            WHERE t.category_id IS NULL {year_filter}
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
