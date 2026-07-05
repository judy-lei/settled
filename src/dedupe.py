"""
Conservative duplicate detection.

Flags likely duplicates for review — never auto-deletes or auto-merges.
Match criteria (all must agree): same date, same amount, same normalized
merchant, same owner. Different accounts/sources are allowed to match
(e.g. a purchase appearing on both a chequing export and a credit card
export would be a real duplicate worth catching).

Run: .venv/bin/python src/dedupe.py
"""

from schema import get_conn


def find_duplicate_groups(conn) -> list:
    """
    Returns groups of transaction ids that share date + amount + merchant + owner.
    Only groups with 2+ members are real candidates.
    """
    rows = conn.execute("""
        SELECT id, transaction_date, amount, merchant_normalized, owner_id, direction
        FROM transactions
        WHERE duplicate_status NOT IN ('confirmed_duplicate', 'dismissed')
        ORDER BY transaction_date, amount, merchant_normalized
    """).fetchall()

    groups = {}
    for r in rows:
        key = (r["transaction_date"], r["amount"], r["merchant_normalized"], r["owner_id"], r["direction"])
        groups.setdefault(key, []).append(r["id"])

    return [ids for ids in groups.values() if len(ids) > 1]


def flag_duplicates(conn) -> int:
    """Marks the 2nd+ transaction in each duplicate group as suspected_duplicate."""
    groups = find_duplicate_groups(conn)
    flagged = 0
    for ids in groups:
        primary, *rest = sorted(ids)
        for dup_id in rest:
            conn.execute("""
                UPDATE transactions
                SET duplicate_status = 'suspected_duplicate', duplicate_of_id = ?
                WHERE id = ? AND duplicate_status = 'unique'
            """, (primary, dup_id))
            flagged += 1
    conn.commit()
    return flagged


def report_suspected(conn) -> None:
    rows = conn.execute("""
        SELECT t.id, t.transaction_date, t.amount, t.merchant_normalized,
               t.duplicate_of_id, a.institution, a.account_name
        FROM transactions t JOIN accounts a ON t.account_id = a.id
        WHERE t.duplicate_status = 'suspected_duplicate'
        ORDER BY t.transaction_date
    """).fetchall()

    if not rows:
        print("No suspected duplicates.")
        return

    print(f"\n{len(rows)} suspected duplicate(s) — review before confirming:\n")
    for r in rows:
        orig = conn.execute(
            "SELECT account_id FROM transactions WHERE id = ?", (r["duplicate_of_id"],)
        ).fetchone()
        print(f"  id={r['id']:<5} ${r['amount']:>8.2f}  {r['transaction_date']}  "
              f"{r['merchant_normalized']:<30} ({r['institution']} {r['account_name']})"
              f"  duplicate_of=id {r['duplicate_of_id']}")


if __name__ == "__main__":
    conn = get_conn()
    n = flag_duplicates(conn)
    print(f"Flagged {n} suspected duplicate(s).")
    report_suspected(conn)
    conn.close()
