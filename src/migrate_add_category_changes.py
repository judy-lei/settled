"""
Add the category_changes table (append-only correction trail).

Run once on an existing database to pick up the review-flow correction trail:
    .venv/bin/python src/migrate_add_category_changes.py

Idempotent: CREATE TABLE / CREATE INDEX IF NOT EXISTS, no backfill. A fresh DB
gets this table from init_db() directly; the importer's main() runs init_db on
every run, so the live DB also picks it up regardless — this script honours the
"no schema change without a migration" convention and lets the table be added
without a full reimport.
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

    existed = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_changes'"
    ).fetchone() is not None

    # Separate execute() calls, not executescript(): executescript() issues an
    # implicit COMMIT that would close this transaction out from under us.
    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS category_changes (
                id                  INTEGER PRIMARY KEY,
                transaction_id      INTEGER NOT NULL REFERENCES transactions(id),
                old_category_id     INTEGER NOT NULL REFERENCES categories(id),
                new_category_id     INTEGER NOT NULL REFERENCES categories(id),
                old_category_source TEXT NOT NULL CHECK (old_category_source IN (
                                        'merchant_rule', 'source_mapped', 'user_manual'
                                    )),
                changed_at          TEXT NOT NULL,
                CHECK (old_category_id != new_category_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catchg_txn ON category_changes(transaction_id)")

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

    print("category_changes:", "already present — no-op" if existed else "created")
    print("FK check:        PASS")
    print(f"Integrity check: {ic_result.upper()}")
    print("Done.")


if __name__ == "__main__":
    run()
