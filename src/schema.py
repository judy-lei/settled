"""
Database schema — 5 tables for v1.

Run directly to (re)initialize: .venv/bin/python src/schema.py
Safe to call multiple times (CREATE IF NOT EXISTS / INSERT OR IGNORE).
To start fresh: delete data/spend.db and re-run.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "spend.db"
SEED_CONFIG_PATH = Path(__file__).parent.parent / "data" / "seed_config.json"
SEED_CONFIG_EXAMPLE = Path(__file__).parent.parent / "seed_config.example.json"


def load_seed_config() -> dict:
    """Person and account data is local-only (data/ is git-ignored), never
    hardcoded in source. Copy seed_config.example.json to data/seed_config.json
    and fill in your household's details."""
    if not SEED_CONFIG_PATH.exists():
        raise SystemExit(
            f"Missing {SEED_CONFIG_PATH}.\n"
            f"Copy {SEED_CONFIG_EXAMPLE.name} there and edit it with your "
            "household's persons and accounts."
        )
    with open(SEED_CONFIG_PATH) as f:
        return json.load(f)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persons (
            id           INTEGER PRIMARY KEY,
            initials     TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id           INTEGER PRIMARY KEY,
            owner_id     INTEGER NOT NULL REFERENCES persons(id),
            institution  TEXT NOT NULL,
            account_name TEXT NOT NULL,
            account_type TEXT NOT NULL,  -- credit_card | chequing | savings
            is_active    INTEGER DEFAULT 1,
            UNIQUE(owner_id, institution, account_name)
        );

        CREATE TABLE IF NOT EXISTS import_files (
            id              INTEGER PRIMARY KEY,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            source_filename TEXT NOT NULL,
            source_format   TEXT NOT NULL,  -- amex_monthly | amex_annual | ws_visa | ws_chequing_clean
            row_count       INTEGER NOT NULL,
            imported_at     TEXT NOT NULL,
            UNIQUE(source_filename, row_count)  -- re-importing same file is a no-op
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id                   INTEGER PRIMARY KEY,
            import_file_id       INTEGER NOT NULL REFERENCES import_files(id),
            account_id           INTEGER NOT NULL REFERENCES accounts(id),
            owner_id             INTEGER NOT NULL REFERENCES persons(id),
            merchant_raw         TEXT,
            merchant_normalized  TEXT,
            transaction_date     TEXT NOT NULL,
            posted_date          TEXT,
            amount               REAL NOT NULL,   -- always positive; direction carries sign meaning
            currency             TEXT DEFAULT 'CAD',
            direction            TEXT NOT NULL,   -- debit | credit
            transaction_type     TEXT,            -- purchase | payment | refund | transfer | fee
            source_category_raw  TEXT,             -- category as given by the source, if any
            category             TEXT,
            category_source      TEXT,             -- merchant_rule | source_mapped | none
            include_in_household INTEGER DEFAULT 1,
            review_status        TEXT DEFAULT 'unreviewed',
            duplicate_status     TEXT DEFAULT 'unique',  -- unique | suspected_duplicate | confirmed_duplicate | dismissed
            duplicate_of_id      INTEGER REFERENCES transactions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_txn_owner    ON transactions(owner_id);
        CREATE INDEX IF NOT EXISTS idx_txn_import_file ON transactions(import_file_id);

        CREATE TABLE IF NOT EXISTS merchant_rules (
            id         INTEGER PRIMARY KEY,
            pattern    TEXT NOT NULL,
            category   TEXT NOT NULL,
            source     TEXT DEFAULT 'user_correction',  -- seed | user_correction
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()


def seed_merchant_rules(conn: sqlite3.Connection, seed_rules: list) -> None:
    """One-time seed from the original hardcoded rules list. No-ops if already seeded."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM merchant_rules WHERE source = 'seed'"
    ).fetchone()[0]
    if existing:
        return
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany("""
        INSERT INTO merchant_rules (pattern, category, source, created_at)
        VALUES (?, ?, 'seed', ?)
    """, [(p, c, now) for p, c in seed_rules])
    conn.commit()


def get_merchant_rules(conn: sqlite3.Connection) -> list:
    """Returns rules newest-first, so user corrections override older seed rules
    when patterns overlap on the same merchant."""
    rows = conn.execute(
        "SELECT pattern, category FROM merchant_rules ORDER BY id DESC"
    ).fetchall()
    return [(r["pattern"], r["category"]) for r in rows]


def add_merchant_rule(conn: sqlite3.Connection, pattern: str, category: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO merchant_rules (pattern, category, source, created_at)
        VALUES (?, ?, 'user_correction', ?)
    """, (pattern, category, now))
    conn.commit()


def seed_persons(conn: sqlite3.Connection) -> dict:
    config = load_seed_config()
    conn.executemany(
        "INSERT OR IGNORE INTO persons (initials, display_name) VALUES (?, ?)",
        [(p["initials"], p["display_name"]) for p in config["persons"]],
    )
    conn.commit()
    return {r["initials"]: r["id"] for r in conn.execute("SELECT id, initials FROM persons")}


def seed_accounts(conn: sqlite3.Connection, persons: dict) -> dict:
    config = load_seed_config()
    accounts = [
        (persons[a["owner_initials"]], a["institution"], a["account_name"], a["account_type"])
        for a in config["accounts"]
    ]
    conn.executemany("""
        INSERT OR IGNORE INTO accounts (owner_id, institution, account_name, account_type)
        VALUES (?, ?, ?, ?)
    """, accounts)
    conn.commit()
    rows = conn.execute("SELECT id, institution, account_name FROM accounts")
    return {f"{r['institution']}:{r['account_name']}": r["id"] for r in rows}


if __name__ == "__main__":
    conn = get_conn()
    init_db(conn)
    persons = seed_persons(conn)
    accounts = seed_accounts(conn, persons)
    print("DB initialized:", DB_PATH)
    print("Persons:", persons)
    print("Accounts:", accounts)
    conn.close()
