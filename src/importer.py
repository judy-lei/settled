"""
Multi-source importer. Reads the import file registry from the local
seed config (data/seed_config.json, git-ignored — see
seed_config.example.json), skips files already imported (by filename +
row count), categorizes via categories.py, and loads into the schema.

Registry format in seed_config.json ("import_files" key):
    { "filename": "statement.csv",       # lives in data/
      "account_key": "Amex:Cobalt",      # "{institution}:{account_name}"
      "source_format": "amex_monthly",   # a parser from parsers.PARSERS
      "statement_total": 1234.56 }       # optional — enables the trust test

Run: .venv/bin/python src/importer.py
"""

from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

from schema import (get_conn, init_db, load_seed_config, seed_persons,
                    seed_accounts, seed_merchant_rules, get_merchant_rules)
from parsers import PARSERS
from categories import categorize, SEED_MERCHANT_RULES

DATA_DIR = Path(__file__).parent.parent / "data"

TRUST_TEST_TOLERANCE = 1.00


def load_import_registry() -> tuple:
    """User's statement files are local data, never hardcoded in source.
    Returns (known_sources, statement_totals) in the shapes the importer uses."""
    entries = load_seed_config().get("import_files", [])
    known_sources = {e["filename"]: (e["account_key"], e["source_format"]) for e in entries}
    statement_totals = {e["filename"]: e["statement_total"]
                        for e in entries if e.get("statement_total") is not None}
    return known_sources, statement_totals


KNOWN_SOURCES, STATEMENT_TOTALS = load_import_registry()


def import_file(conn, filename: str, account_id: int, owner_id: int, rules: list) -> dict:
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return {"status": "missing", "filename": filename}

    _, source_format = KNOWN_SOURCES[filename]
    df = PARSERS[source_format](filepath)
    row_count = len(df)

    existing = conn.execute(
        "SELECT id FROM import_files WHERE source_filename = ? AND row_count = ?",
        (filename, row_count),
    ).fetchone()
    if existing:
        return {"status": "skipped (already imported)", "filename": filename, "rows": row_count}

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("""
        INSERT INTO import_files (account_id, source_filename, source_format, row_count, imported_at)
        VALUES (?, ?, ?, ?, ?)
    """, (account_id, filename, source_format, row_count, now))
    import_file_id = cur.lastrowid

    rows = []
    for _, r in df.iterrows():
        cat = categorize(r["merchant_normalized"], rules, r.get("source_category_mapped"),
                          r.get("transaction_type"))
        rows.append((
            import_file_id, account_id, owner_id,
            r["merchant_raw"], r["merchant_normalized"],
            r["transaction_date"].strftime("%Y-%m-%d"),
            r["posted_date"].strftime("%Y-%m-%d") if pd.notna(r["posted_date"]) else None,
            float(r["amount"]), r["currency"], r["direction"], r["transaction_type"],
            r.get("source_category_mapped"),
            cat["category"], cat["category_source"],
            1 if cat["include_in_household"] else 0,
            "unreviewed",
        ))

    conn.executemany("""
        INSERT INTO transactions (
            import_file_id, account_id, owner_id, merchant_raw, merchant_normalized,
            transaction_date, posted_date, amount, currency, direction, transaction_type,
            source_category_raw, category, category_source, include_in_household, review_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()

    return {"status": "imported", "filename": filename, "rows": row_count, "import_file_id": import_file_id}


def trust_test(conn, filename: str, import_file_id: int) -> None:
    if filename not in STATEMENT_TOTALS:
        return
    statement_total = STATEMENT_TOTALS[filename]
    computed = conn.execute("""
        SELECT ROUND(SUM(CASE WHEN direction = 'credit' THEN -amount ELSE amount END), 2)
        FROM transactions
        WHERE import_file_id = ? AND include_in_household = 1
    """, (import_file_id,)).fetchone()[0] or 0.0

    diff = round(computed - statement_total, 2)
    status = "PASS" if abs(diff) <= TRUST_TEST_TOLERANCE else "FAIL"
    print(f"  Trust test [{filename}]: computed ${computed:.2f} vs statement ${statement_total:.2f} "
          f"— {status} (diff ${diff:+.2f})")


def main():
    conn = get_conn()
    init_db(conn)
    persons = seed_persons(conn)
    accounts = seed_accounts(conn, persons)
    seed_merchant_rules(conn, SEED_MERCHANT_RULES)
    rules = get_merchant_rules(conn)

    print("Importing known sources...\n")
    for filename, (account_key, _fmt) in KNOWN_SOURCES.items():
        account_id = accounts[account_key]
        owner_row = conn.execute("SELECT owner_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        owner_id = owner_row["owner_id"]

        result = import_file(conn, filename, account_id, owner_id, rules)
        print(f"  {result['status']:<28} {filename}"
              + (f" ({result['rows']} rows)" if "rows" in result else ""))

        if result["status"] == "imported":
            trust_test(conn, filename, result["import_file_id"])

    conn.close()
    print("\nDone. Run: .venv/bin/python src/report.py")


if __name__ == "__main__":
    main()
