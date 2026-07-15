"""
Drop the 'Uncategorized' category row.

Run once after deploying the fix/unify-uncategorized-on-null changes.
Idempotent: no-op if the row is already gone.

What this does:
  1. Moves any transactions on the 'Uncategorized' category to category_id=NULL
     (they are uncategorized — this is correct, not data loss).
  2. Deletes the category_splits rows for that category.
  3. Deletes the category row itself.
  4. Runs PRAGMA foreign_key_check + integrity_check and prints PASS/FAIL.
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "spend.db"


def run() -> None:
    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    row = conn.execute(
        "SELECT id FROM categories WHERE name = 'Uncategorized'"
    ).fetchone()

    if row is None:
        print("'Uncategorized' category not found — already removed. Nothing to do.")
        return

    cat_id = row["id"]

    txn_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id = ?", (cat_id,)
    ).fetchone()[0]

    split_count = conn.execute(
        "SELECT COUNT(*) FROM category_splits WHERE category_id = ?", (cat_id,)
    ).fetchone()[0]

    print(f"Found 'Uncategorized' (id={cat_id}): "
          f"{txn_count} transaction(s), {split_count} split row(s)")

    conn.execute("BEGIN")
    try:
        if txn_count:
            conn.execute(
                "UPDATE transactions SET category_id = NULL, category_source = 'none' "
                "WHERE category_id = ?",
                (cat_id,),
            )
            print(f"  Moved {txn_count} transaction(s) to category_id=NULL")

        conn.execute("DELETE FROM category_splits WHERE category_id = ?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))

        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        ic_result = conn.execute("PRAGMA integrity_check").fetchone()[0]

        if fk_violations:
            conn.execute("ROLLBACK")
            sys.exit(f"FK check: FAIL\n{[dict(r) for r in fk_violations]}")
        if ic_result != "ok":
            conn.execute("ROLLBACK")
            sys.exit(f"Integrity check: FAIL — {ic_result}")

        conn.execute("COMMIT")

        print(f"FK check:        PASS")
        print(f"Integrity check: {ic_result.upper()}")
        print("Done.")

    except Exception:
        conn.execute("ROLLBACK")
        raise


if __name__ == "__main__":
    run()
