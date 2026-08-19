"""Multi-user account_key lookup — regression lock.

Locks the requirement that two users holding accounts with the same
(institution, account_name) pair round-trip through seed_accounts as
DISTINCT dict entries AND that the importer routes each file's rows to
the correct owner's account. Three tests:

1. `test_two_users_same_institution_get_distinct_ids` — locks the
   producer: seed_accounts returns two entries with distinct
   (account_id, owner_id) tuples. Regression: dropping owner from the
   key format collapses the entries and this test goes red.

2. `test_orphan_account_raises_with_clear_message` — locks the loud
   failure: an account whose owner_id resolves to no user row raises
   RuntimeError rather than being silently dropped. Regression:
   reverting the LEFT JOIN + None-check to an INNER JOIN goes red.

3. `test_two_owner_files_route_to_correct_accounts` — locks the
   full round-trip through the importer. A regression that keeps the
   3-part dict format but reverts importer's account_key resolution
   (or hardcodes an old-format lookup) would leave test #1 green;
   this test catches it by asserting each file's rows land on the
   right owner's account after a real import.

Run:  .venv/bin/python -m unittest discover tests/ -v
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import importer
from helpers import RedirectsSeedConfig
from schema import (get_merchant_rules, init_db, seed_accounts, seed_categories,
                    seed_category_splits, seed_merchant_rules,
                    seed_user_corrections, seed_users)


class MultiUserAccountKeyTest(RedirectsSeedConfig):
    def _initial_seed_config(self):
        return {
            "users": [
                {"display_name": "Alex"},
                {"display_name": "Sam"},
            ],
            "accounts": [
                {"owner_name": "Alex", "institution": "Wealthsimple",
                 "account_name": "Visa", "account_type": "credit_card"},
                {"owner_name": "Sam", "institution": "Wealthsimple",
                 "account_name": "Visa", "account_type": "credit_card"},
            ],
            "import_files": [],
            "merchant_rules": [],
            "user_corrections": [],
        }

    def test_two_users_same_institution_get_distinct_ids(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_db(conn)
        users = seed_users(conn)
        accounts = seed_accounts(conn, users)

        alex_key = "Alex:Wealthsimple:Visa"
        sam_key = "Sam:Wealthsimple:Visa"

        self.assertIn(alex_key, accounts,
                      f"Missing key {alex_key!r} — owner not in lookup format")
        self.assertIn(sam_key, accounts,
                      f"Missing key {sam_key!r} — owner not in lookup format")
        self.assertEqual(
            len(accounts), 2,
            "seed_accounts collapsed two accounts into one entry — "
            "owner must be part of the returned dict key",
        )

        # The (account_id, owner_id) tuple returned by seed_accounts (the dict
        # values are tuples, not bare ids) must point at accounts owned by the
        # matching user.
        alex_account_id, alex_owner_id = accounts[alex_key]
        sam_account_id, sam_owner_id = accounts[sam_key]

        self.assertNotEqual(
            alex_account_id, sam_account_id,
            "Two users' same-named accounts resolved to the same id — "
            "importer would silently attribute one owner's spend to the other",
        )
        self.assertEqual(alex_owner_id, users["Alex"])
        self.assertEqual(sam_owner_id, users["Sam"])


class OrphanAccountRaisesTest(RedirectsSeedConfig):
    """seed_accounts must raise loudly on any account whose owner_id doesn't
    resolve to a user row (the LEFT JOIN + None-check lock).
    Regression: reverting to INNER JOIN silently drops the orphan and the
    downstream KeyError misdirects the diagnosis."""

    def _initial_seed_config(self):
        return {
            "users": [{"display_name": "Alex"}],
            "accounts": [
                {"owner_name": "Alex", "institution": "Wealthsimple",
                 "account_name": "Visa", "account_type": "credit_card"},
            ],
            "import_files": [],
            "merchant_rules": [],
            "user_corrections": [],
        }

    def test_orphan_account_raises_with_clear_message(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_db(conn)
        users = seed_users(conn)
        seed_accounts(conn, users)  # baseline: no orphan → no raise

        # Inject an orphan account (owner_id points at a nonexistent user).
        # FK OFF to allow the insert; the whole point of the loud-failure
        # invariant is defending against exactly this kind of state.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO accounts (owner_id, institution, account_name, account_type)"
            " VALUES (?, ?, ?, ?)",
            (999, "Ghost", "Card", "credit_card"),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

        with self.assertRaises(RuntimeError) as cm:
            seed_accounts(conn, users)
        self.assertIn("Ghost:Card", str(cm.exception))
        self.assertIn("no matching users row", str(cm.exception))


_WS_VISA_CSV_HEADER = (
    "transaction_date,transaction_type,status,merchant,amount,currency,notes,category\n"
)


class TwoOwnerImportRoundTripTest(RedirectsSeedConfig):
    """End-to-end lock: a config with new-format account_keys drives the real
    importer, and each file's rows attribute to the correct owner. Catches
    regressions in the importer's lookup that the dict-shape test #1 can't."""

    def _initial_seed_config(self):
        return {
            "users": [{"display_name": "Alex"}, {"display_name": "Sam"}],
            "accounts": [
                {"owner_name": "Alex", "institution": "Wealthsimple",
                 "account_name": "Visa", "account_type": "credit_card"},
                {"owner_name": "Sam", "institution": "Wealthsimple",
                 "account_name": "Visa", "account_type": "credit_card"},
            ],
            "import_files": [
                {"filename": "alex.csv", "account_key": "Alex:Wealthsimple:Visa",
                 "source_format": "ws_visa"},
                {"filename": "sam.csv", "account_key": "Sam:Wealthsimple:Visa",
                 "source_format": "ws_visa"},
            ],
            "merchant_rules": [],
            "user_corrections": [],
        }

    def test_two_owner_files_route_to_correct_accounts(self):
        # Point the importer at a temp data dir with two CSVs, one per owner.
        data_dir = Path(self._tmpdir) / "data"
        data_dir.mkdir()
        (data_dir / "alex.csv").write_text(
            _WS_VISA_CSV_HEADER
            + "2026-05-15,Purchase,Completed,ALEX MERCH,-10.00,CAD,,Restaurants\n"
        )
        (data_dir / "sam.csv").write_text(
            _WS_VISA_CSV_HEADER
            + "2026-05-15,Purchase,Completed,SAM MERCH,-20.00,CAD,,Restaurants\n"
        )

        original_data_dir = importer.DATA_DIR
        original_known_sources = importer.KNOWN_SOURCES
        original_statement_totals = importer.STATEMENT_TOTALS
        self.addCleanup(setattr, importer, "DATA_DIR", original_data_dir)
        self.addCleanup(setattr, importer, "KNOWN_SOURCES", original_known_sources)
        self.addCleanup(setattr, importer, "STATEMENT_TOTALS", original_statement_totals)

        importer.DATA_DIR = data_dir
        (importer.KNOWN_SOURCES, importer.STATEMENT_TOTALS, _) = importer.load_import_registry()

        # Fresh DB seeded from the redirected config, then drive the importer
        # loop body — same shape as importer.main() but without stdout side-effects.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_db(conn)
        users = seed_users(conn)
        accounts = seed_accounts(conn, users)
        seed_categories(conn)
        seed_category_splits(conn)
        seed_merchant_rules(conn, [])
        seed_user_corrections(conn, [])
        rules = get_merchant_rules(conn)

        for filename, (account_key, _fmt) in importer.KNOWN_SOURCES.items():
            account_id, owner_id = accounts[account_key]
            result = importer.import_file(conn, filename, account_id, owner_id, rules)
            self.assertEqual(result["status"], "imported",
                             f"{filename}: {result}")

        # The load-bearing assertion: each file's row landed on the RIGHT owner.
        alex_row = conn.execute("""
            SELECT u.display_name AS owner
            FROM transactions t JOIN users u ON u.id = t.owner_id
            WHERE t.merchant_normalized = 'ALEX MERCH'
        """).fetchone()
        sam_row = conn.execute("""
            SELECT u.display_name AS owner
            FROM transactions t JOIN users u ON u.id = t.owner_id
            WHERE t.merchant_normalized = 'SAM MERCH'
        """).fetchone()
        self.assertIsNotNone(alex_row, "Alex's row not imported")
        self.assertIsNotNone(sam_row, "Sam's row not imported")
        self.assertEqual(
            alex_row["owner"], "Alex",
            "Alex's file's row landed on wrong owner — importer lookup broken",
        )
        self.assertEqual(
            sam_row["owner"], "Sam",
            "Sam's file's row landed on wrong owner — importer lookup broken",
        )


if __name__ == "__main__":
    unittest.main()
