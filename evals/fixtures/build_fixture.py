"""
Build the eval fixture database from fixture_transactions.json.
Creates evals/fixtures/eval.db fresh every run — deterministic, same DB every time.

Run: .venv/bin/python evals/fixtures/build_fixture.py
"""

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent
FIXTURE_JSON = FIXTURE_DIR / "fixture_transactions.json"
EVAL_DB = FIXTURE_DIR / "eval.db"

sys.path.insert(0, str(FIXTURE_DIR.parent.parent / "src"))
from categories import blanked_at_import
from schema import init_db


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def build(conn: sqlite3.Connection, data: dict) -> None:
    init_db(conn)

    # Users
    user_ids: dict[str, int] = {}
    for u in data["users"]:
        cur = conn.execute(
            "INSERT INTO users (display_name) VALUES (?) RETURNING id", (u["display_name"],)
        )
        user_ids[u["display_name"]] = cur.fetchone()[0]

    # Accounts — keyed as "Owner/Institution/AccountName"
    account_ids: dict[str, int] = {}
    for a in data["accounts"]:
        cur = conn.execute(
            "INSERT INTO accounts (owner_id, institution, account_name, account_type) VALUES (?, ?, ?, ?) RETURNING id",
            (user_ids[a["owner"]], a["institution"], a["account_name"], a["account_type"]),
        )
        key = f"{a['owner']}/{a['institution']}/{a['account_name']}"
        account_ids[key] = cur.fetchone()[0]

    # Categories
    category_ids: dict[str, int] = {}
    for c in data["categories"]:
        cur = conn.execute(
            "INSERT INTO categories (name, type) VALUES (?, ?) RETURNING id",
            (c["name"], c["type"]),
        )
        category_ids[c["name"]] = cur.fetchone()[0]

    # Category splits (spend categories only; transfer/income/investment have no splits)
    spend_cats = {
        name: cid
        for name, cid in category_ids.items()
        if any(c["name"] == name and c["type"] == "spend" for c in data["categories"])
    }
    for s in data["category_splits"]:
        if s["category"] in spend_cats:
            conn.execute(
                "INSERT INTO category_splits (category_id, user_id, pct) VALUES (?, ?, ?)",
                (spend_cats[s["category"]], user_ids[s["user"]], s["pct"]),
            )

    # Import files
    now = datetime.now(timezone.utc).isoformat()
    import_file_ids: dict[str, int] = {}
    for f in data["import_files"]:
        account_id = account_ids[f["account"]]
        cur = conn.execute(
            """INSERT INTO import_files
               (account_id, source_filename, source_format, row_count, source_hash, imported_at)
               VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
            (
                account_id,
                f["source_filename"],
                f["source_format"],
                f["row_count"],
                _hash(f["label"]),
                now,
            ),
        )
        import_file_ids[f["label"]] = cur.fetchone()[0]

    # Transactions (two passes: first insert without duplicate_of_id, then update it)
    label_to_id: dict[str, int] = {}
    for t in data["transactions"]:
        account_id = account_ids[t["account"]]
        owner_id = user_ids[t["owner"]]
        category_id = category_ids[t["category"]] if t["category"] else None
        import_file_id = import_file_ids[t["import_file"]]

        # Stamp uncategorized_at_import via the SAME shared rule the importer
        # uses, so the fixture reflects the real import-time marker and cannot
        # drift from it.
        uncategorized_at_import = blanked_at_import(t["category_source"])
        cur = conn.execute(
            """INSERT INTO transactions
               (import_file_id, account_id, owner_id,
                merchant_raw, merchant_normalized,
                transaction_date, amount, direction, transaction_type,
                source_category_raw, category_id, category_source,
                uncategorized_at_import, review_status, duplicate_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', ?)
               RETURNING id""",
            (
                import_file_id,
                account_id,
                owner_id,
                t["merchant_raw"],
                t["merchant_normalized"],
                t["transaction_date"],
                t["amount"],
                t["direction"],
                t["transaction_type"],
                t.get("source_category_raw"),
                category_id,
                t["category_source"],
                uncategorized_at_import,
                t["duplicate_status"],
            ),
        )
        label_to_id[t["label"]] = cur.fetchone()[0]

    # Second pass: set duplicate_of_id where referenced by label
    for t in data["transactions"]:
        if "duplicate_of_label" in t:
            conn.execute(
                "UPDATE transactions SET duplicate_of_id = ? WHERE id = ?",
                (label_to_id[t["duplicate_of_label"]], label_to_id[t["label"]]),
            )

    conn.commit()
    print(f"Built {EVAL_DB}")
    print(f"  Users: {len(user_ids)}")
    print(f"  Accounts: {len(account_ids)}")
    print(f"  Categories: {len(category_ids)}")
    print(f"  Transactions: {len(label_to_id)}")


if __name__ == "__main__":
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    conn = sqlite3.connect(EVAL_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    data = json.loads(FIXTURE_JSON.read_text())
    build(conn, data)
    conn.close()
