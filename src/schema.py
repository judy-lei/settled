"""
Database schema — v2.

Run directly to (re)initialize a fresh database:
    .venv/bin/python src/schema.py

Safe to run on a fresh DB only. Existing databases must use migrate_v2.py.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "spend.db"
SEED_CONFIG_PATH = Path(__file__).parent.parent / "data" / "seed_config.json"
SEED_CONFIG_EXAMPLE = Path(__file__).parent.parent / "seed_config.example.json"

SETTLEMENT_EXCLUDED_TYPES = ("payment", "transfer")
_SETTLEMENT_EXCLUDED_SQL = ", ".join(f"'{t}'" for t in SETTLEMENT_EXCLUDED_TYPES)

DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("Auto", "spend"),
    ("Bills", "spend"),
    ("Coffee & Tea", "spend"),
    ("Donations", "spend"),
    ("Eating Out", "spend"),
    ("Education", "spend"),
    ("Entertainment", "spend"),
    ("Family", "spend"),
    ("Gifts", "spend"),
    ("Groceries", "spend"),
    ("Health", "spend"),
    ("Home", "spend"),
    ("Payment", "transfer"),
    ("Personal Care", "spend"),
    ("Pet", "spend"),
    ("Recreation", "spend"),
    ("Rental Property", "spend"),
    ("Services", "spend"),
    ("Shopping", "spend"),
    ("Subscriptions", "spend"),
    ("Transport", "spend"),
    ("Travel", "spend"),
]


def load_seed_config() -> dict:
    """Household config is local-only (data/ is git-ignored). Copy
    seed_config.example.json to data/seed_config.json and fill in your details."""
    if not SEED_CONFIG_PATH.exists():
        raise SystemExit(
            f"Missing {SEED_CONFIG_PATH}.\n"
            f"Copy {SEED_CONFIG_EXAMPLE.name} there and edit it with your "
            "household's users and accounts."
        )
    with open(SEED_CONFIG_PATH) as f:
        return json.load(f)


def _save_seed_config(config: dict) -> None:
    """Atomically overwrite seed_config.json (temp file + os.replace).
    Leaves no orphaned temp file if the write or replace fails."""
    dir_ = SEED_CONFIG_PATH.parent
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, SEED_CONFIG_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id           INTEGER PRIMARY KEY,
            owner_id     INTEGER NOT NULL REFERENCES users(id),
            institution  TEXT NOT NULL,
            account_name TEXT NOT NULL,
            account_type TEXT NOT NULL CHECK (account_type IN ('credit_card', 'chequing', 'savings')),
            is_active    INTEGER DEFAULT 1,
            UNIQUE(owner_id, institution, account_name)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'spend'
                 CHECK (type IN ('spend', 'income', 'transfer', 'investment'))
        );

        CREATE TABLE IF NOT EXISTS category_splits (
            id          INTEGER PRIMARY KEY,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            user_id     INTEGER NOT NULL REFERENCES users(id),
            pct         REAL NOT NULL CHECK (pct >= 0 AND pct <= 100),
            UNIQUE(category_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS settlements (
            id         INTEGER PRIMARY KEY,
            period     TEXT NOT NULL UNIQUE,
            settled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS import_files (
            id              INTEGER PRIMARY KEY,
            account_id      INTEGER NOT NULL REFERENCES accounts(id),
            source_filename TEXT NOT NULL,
            source_format   TEXT NOT NULL CHECK (source_format IN (
                                'amex_monthly', 'amex_annual', 'ws_visa', 'ws_chequing_clean'
                            )),
            row_count       INTEGER NOT NULL,
            source_hash     TEXT NOT NULL UNIQUE,
            imported_at     TEXT NOT NULL,
            UNIQUE(source_filename, row_count)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id                   INTEGER PRIMARY KEY,
            import_file_id       INTEGER NOT NULL REFERENCES import_files(id),
            account_id           INTEGER NOT NULL REFERENCES accounts(id),
            owner_id             INTEGER NOT NULL REFERENCES users(id),
            merchant_raw         TEXT NOT NULL,
            merchant_normalized  TEXT NOT NULL,
            transaction_date     TEXT NOT NULL,
            posted_date          TEXT,
            amount               REAL NOT NULL CHECK (amount >= 0),
            currency             TEXT DEFAULT 'CAD',
            direction            TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
            -- Adding a type here? Review these filters in the same commit:
            --   src/schema.py SETTLEMENT_EXCLUDED_TYPES (should the new type be excluded from spend?)
            --   src/agent/tools_read.py query_spend (uncategorized_count_in_period WHERE clause)
            --   src/agent/tools_read.py list_uncategorized (WHERE clause on what needs categorization)
            transaction_type     TEXT NOT NULL CHECK (transaction_type IN
                                     ('purchase', 'payment', 'refund', 'transfer', 'fee')),
            source_category_raw  TEXT,
            category_id          INTEGER REFERENCES categories(id),
            category_source      TEXT NOT NULL CHECK (category_source IN (
                                     'merchant_rule', 'source_mapped', 'transaction_type',
                                     'user_manual', 'none'
                                 )),
            review_status        TEXT NOT NULL DEFAULT 'unreviewed'
                                 CHECK (review_status IN ('unreviewed', 'reviewed')),
            duplicate_status     TEXT NOT NULL DEFAULT 'unique'
                                 CHECK (duplicate_status IN (
                                     'unique', 'suspected_duplicate',
                                     'confirmed_duplicate', 'dismissed'
                                 )),
            duplicate_of_id      INTEGER REFERENCES transactions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_txn_date        ON transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_txn_category    ON transactions(category_id);
        CREATE INDEX IF NOT EXISTS idx_txn_owner       ON transactions(owner_id);
        CREATE INDEX IF NOT EXISTS idx_txn_import_file ON transactions(import_file_id);

        CREATE TABLE IF NOT EXISTS merchant_rules (
            id          INTEGER PRIMARY KEY,
            pattern     TEXT NOT NULL UNIQUE,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            source      TEXT NOT NULL DEFAULT 'user_correction'
                        CHECK (source IN ('seed', 'user_correction')),
            created_at  TEXT NOT NULL
        );
    """)
    conn.commit()


def seed_users(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert users from seed_config. Returns {display_name: id}."""
    config = load_seed_config()
    conn.executemany(
        "INSERT OR IGNORE INTO users (display_name) VALUES (?)",
        [(u["display_name"],) for u in config["users"]],
    )
    conn.commit()
    return {r["display_name"]: r["id"] for r in conn.execute("SELECT id, display_name FROM users")}


def seed_accounts(conn: sqlite3.Connection, users: dict[str, int]) -> dict[str, int]:
    """Insert accounts from seed_config. Returns {institution:account_name: id}."""
    config = load_seed_config()
    conn.executemany("""
        INSERT OR IGNORE INTO accounts (owner_id, institution, account_name, account_type)
        VALUES (?, ?, ?, ?)
    """, [
        (users[a["owner_name"]], a["institution"], a["account_name"], a["account_type"])
        for a in config["accounts"]
    ])
    conn.commit()
    return {
        f"{r['institution']}:{r['account_name']}": r["id"]
        for r in conn.execute("SELECT id, institution, account_name FROM accounts")
    }


def seed_categories(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert default categories. Returns {name: id}."""
    conn.executemany(
        "INSERT OR IGNORE INTO categories (name, type) VALUES (?, ?)",
        DEFAULT_CATEGORIES,
    )
    conn.commit()
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}


def seed_category_splits(conn: sqlite3.Connection) -> None:
    """Seed 50/50 default splits for all spend categories. No-op for existing rows."""
    spend_cats = conn.execute("SELECT id FROM categories WHERE type = 'spend'").fetchall()
    users = conn.execute("SELECT id FROM users").fetchall()
    conn.executemany(
        "INSERT OR IGNORE INTO category_splits (category_id, user_id, pct) VALUES (?, ?, ?)",
        [(c["id"], u["id"], 50.0) for c in spend_cats for u in users],
    )
    conn.commit()


def seed_merchant_rules(conn: sqlite3.Connection, seed_rules: list[tuple[str, str]]) -> None:
    """Seed merchant rules from (pattern, category_name) tuples. No-op if already seeded."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM merchant_rules WHERE source = 'seed'"
    ).fetchone()[0]
    if existing:
        return
    categories = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany("""
        INSERT OR IGNORE INTO merchant_rules (pattern, category_id, source, created_at)
        VALUES (?, ?, 'seed', ?)
    """, [(p, categories[c], now) for p, c in seed_rules if c in categories])
    conn.commit()


def seed_user_corrections(conn: sqlite3.Connection, corrections: list[tuple[str, str]]) -> None:
    """Upsert user correction rules — runs every init, overrides seed rules."""
    if not corrections:
        return
    malformed = [c for c in corrections if len(c) != 2]
    if malformed:
        print(f"WARNING: seed_user_corrections: skipped {len(malformed)} malformed correction(s) "
              f"(expected [pattern, category]): {malformed}")
    corrections = [c for c in corrections if len(c) == 2]
    categories = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    unknown = [c for _p, c in corrections if c not in categories]
    if unknown:
        print(f"WARNING: seed_user_corrections: skipped {len(unknown)} correction(s) with unknown categories: {unknown}")
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany("""
        INSERT INTO merchant_rules (pattern, category_id, source, created_at)
        VALUES (?, ?, 'user_correction', ?)
        ON CONFLICT(pattern) DO UPDATE SET
            category_id = excluded.category_id,
            source = 'user_correction',
            created_at = excluded.created_at
    """, [(p, categories[c], now) for p, c in corrections if c in categories])
    conn.commit()


def export_user_corrections(conn: sqlite3.Connection) -> int:
    """Write all user_correction rules from DB into seed_config.json; returns count written."""
    rows = conn.execute("""
        SELECT mr.pattern, c.name
        FROM merchant_rules mr
        JOIN categories c ON c.id = mr.category_id
        WHERE mr.source = 'user_correction'
        ORDER BY mr.pattern
    """).fetchall()
    corrections = [[r["pattern"], r["name"]] for r in rows]
    config = load_seed_config()
    config["user_corrections"] = corrections
    _save_seed_config(config)
    return len(corrections)


def get_merchant_rules(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Returns (pattern, category_name) tuples, newest-first."""
    rows = conn.execute("""
        SELECT mr.pattern, c.name
        FROM merchant_rules mr
        JOIN categories c ON c.id = mr.category_id
        ORDER BY mr.id DESC
    """).fetchall()
    return [(r["pattern"], r["name"]) for r in rows]


def get_settlement_data(conn: sqlite3.Connection, period: str) -> dict:
    """Return raw settlement inputs for a YYYY-MM period.

    Returns a dict with:
      - users: list of {id, display_name, paid, fair_share} — one entry per user
      - total_spend: net spend across all transactions in the period
      - txn_count: number of qualifying transactions
      - uncategorized_count: spend transactions with NULL category_id (excluded from totals)

    Raises ValueError if the DB does not have exactly 2 users (settlement math
    is defined for exactly two people; the data model supports N but we don't).
    """
    users = conn.execute("SELECT id, display_name FROM users ORDER BY id").fetchall()
    if len(users) != 2:
        raise ValueError(
            f"Settlement requires exactly 2 users; found {len(users)}. "
            "Add or remove users before running settlement."
        )

    SIGNED = "CASE WHEN t.direction = 'credit' THEN -t.amount ELSE t.amount END"
    # transaction_type is the hard exclusion: set at parse time from source
    # signals, not dependent on user-maintained category types.
    # categories.type = 'spend' is an additional filter for user-controlled
    # distinctions (e.g. Rental Property as investment).
    SPEND_FILTER = f"""
        AND t.transaction_type NOT IN ({_SETTLEMENT_EXCLUDED_SQL})
          AND c.type = 'spend'
          AND t.duplicate_status != 'confirmed_duplicate'
          AND substr(t.transaction_date, 1, 7) = :period
    """

    # What each user actually paid (they own the account)
    paid_rows = conn.execute(f"""
        SELECT t.owner_id, ROUND(SUM({SIGNED}), 2) AS paid
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE 1=1 {SPEND_FILTER}
        GROUP BY t.owner_id
    """, {"period": period}).fetchall()
    paid_by_user = {r["owner_id"]: r["paid"] for r in paid_rows}

    # What each user's fair share is across all spend in the period
    share_rows = conn.execute(f"""
        SELECT cs.user_id, ROUND(SUM(({SIGNED}) * cs.pct / 100.0), 2) AS fair_share
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        JOIN category_splits cs ON cs.category_id = t.category_id
        WHERE 1=1 {SPEND_FILTER}
        GROUP BY cs.user_id
    """, {"period": period}).fetchall()
    share_by_user = {r["user_id"]: r["fair_share"] for r in share_rows}

    total_spend = conn.execute(f"""
        SELECT ROUND(SUM({SIGNED}), 2)
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE 1=1 {SPEND_FILTER}
    """, {"period": period}).fetchone()[0] or 0.0

    txn_count = conn.execute(f"""
        SELECT COUNT(*)
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE 1=1 {SPEND_FILTER}
    """, {"period": period}).fetchone()[0]

    uncategorized_count = conn.execute(f"""
        SELECT COUNT(*) FROM transactions
        WHERE category_id IS NULL
          AND transaction_type NOT IN ({_SETTLEMENT_EXCLUDED_SQL})
          AND duplicate_status != 'confirmed_duplicate'
          AND substr(transaction_date, 1, 7) = :period
    """, {"period": period}).fetchone()[0]

    return {
        "period": period,
        "users": [
            {
                "id": u["id"],
                "display_name": u["display_name"],
                "paid": paid_by_user.get(u["id"], 0.0),
                "fair_share": share_by_user.get(u["id"], 0.0),
            }
            for u in users
        ],
        "total_spend": total_spend,
        "txn_count": txn_count,
        "uncategorized_count": uncategorized_count,
    }


def compute_settlement(data: dict) -> dict:
    """Compute who owes whom from get_settlement_data() output.

    Adds `balance` (paid - fair_share) to each user entry and a `settlement`
    key with transfer direction and amount.  settlement is None when the period
    has no qualifying spend or both users are exactly square.
    """
    users = [
        {**u, "balance": round(u["paid"] - u["fair_share"], 2)}
        for u in data["users"]
    ]

    creditor = max(users, key=lambda u: u["balance"])  # overpaid — is owed
    debtor = min(users, key=lambda u: u["balance"])    # underpaid — owes
    amount = creditor["balance"]

    settlement = (
        None
        if amount <= 0
        else {
            "from_user": {"id": debtor["id"], "display_name": debtor["display_name"]},
            "to_user": {"id": creditor["id"], "display_name": creditor["display_name"]},
            "amount": amount,
        }
    )
    return {**data, "users": users, "settlement": settlement}


def add_merchant_rule(conn: sqlite3.Connection, pattern: str, category_name: str) -> None:
    category_id = conn.execute(
        "SELECT id FROM categories WHERE name = ?", (category_name,)
    ).fetchone()
    if not category_id:
        raise ValueError(f"Unknown category: {category_name!r}")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO merchant_rules (pattern, category_id, source, created_at)
        VALUES (?, ?, 'user_correction', ?)
        ON CONFLICT(pattern) DO UPDATE SET
            category_id = excluded.category_id,
            source = 'user_correction',
            created_at = excluded.created_at
    """, (pattern, category_id["id"], now))
    _write_correction_to_config(pattern, category_name)
    conn.commit()


def _write_correction_to_config(pattern: str, category_name: str) -> None:
    """Persist a user correction to seed_config.json so it survives a DB rebuild."""
    config = load_seed_config()
    corrections: list[list[str]] = config.setdefault("user_corrections", [])
    # Drop malformed (wrong-arity) entries before matching — seed_user_corrections()
    # already treats them as dead weight (skipped with a warning), and matching
    # against one here would risk entry[1] = ... on a too-short list (IndexError).
    # This also self-heals a malformed entry the moment its pattern is corrected
    # again through the normal flow, instead of leaving it as a permanent landmine.
    malformed = [e for e in corrections if len(e) != 2]
    if malformed:
        print(f"WARNING: _write_correction_to_config: dropped {len(malformed)} "
              f"malformed correction(s) from seed_config.json: {malformed}")
    corrections[:] = [e for e in corrections if len(e) == 2]
    # Update in place if pattern already present, otherwise append.
    for entry in corrections:
        if entry[0] == pattern:
            entry[1] = category_name
            break
    else:
        corrections.append([pattern, category_name])
    _save_seed_config(config)


if __name__ == "__main__":
    conn = get_conn()
    init_db(conn)
    users = seed_users(conn)
    accounts = seed_accounts(conn, users)
    categories = seed_categories(conn)
    seed_category_splits(conn)
    config = load_seed_config()
    seed_merchant_rules(conn, [tuple(r) for r in config.get("merchant_rules", [])])
    seed_user_corrections(conn, [tuple(r) for r in config.get("user_corrections", [])])
    print("DB initialized:", DB_PATH)
    print("Users:", users)
    print("Accounts:", len(accounts), "accounts")
    print("Categories:", len(categories), "categories")
    conn.close()
