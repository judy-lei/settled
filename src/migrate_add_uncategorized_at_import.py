"""
Add the transactions.uncategorized_at_import column (durable "rules left this
blank at import" marker).

Run once on an existing database to pick up the column:
    .venv/bin/python src/migrate_add_uncategorized_at_import.py

Idempotent: skips if the column already exists. Backfills existing rows from
category_source — rows still carrying 'none' were left blank by the rules and
get 1; everything else gets 0. Rows that were blank at import but have since
been filled in (category_source now 'user_manual') cannot be recovered — that
evidence is exactly what this column exists to stop losing going forward, and
those rows keep the DEFAULT 0. A fresh DB gets the column from init_db()
directly with the importer stamping it per row.
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

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "uncategorized_at_import" in cols:
        print("uncategorized_at_import: already present — no-op")
        return

    conn.execute("BEGIN")
    try:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN uncategorized_at_import "
            "INTEGER NOT NULL DEFAULT 0 CHECK (uncategorized_at_import IN (0, 1))")
        backfilled = conn.execute(
            "UPDATE transactions SET uncategorized_at_import = 1 "
            "WHERE category_source = 'none'").rowcount

        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        ic_result = conn.execute("PRAGMA integrity_check").fetchone()[0]

        if fk_violations:
            conn.execute("ROLLBACK")
            sys.exit(f"FK check: FAIL\n{[dict(r) for r in fk_violations]}")
        if ic_result != "ok":
            conn.execute("ROLLBACK")
            sys.exit(f"Integrity check: FAIL — {ic_result}")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    print(f"uncategorized_at_import: added (backfilled {backfilled} still-blank row(s) to 1)")
    print("FK check:        PASS")
    print(f"Integrity check: {ic_result.upper()}")
    print("Done.")


if __name__ == "__main__":
    run()
