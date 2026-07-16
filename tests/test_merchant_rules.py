"""
Merchant-rule durability tests — DATA-1.

The slice exists so user corrections survive a DB rebuild: add_merchant_rule()
writes the correction to seed_config.json, and seed_user_corrections() re-hydrates
it when the DB is rebuilt from config. Before these tests that round-trip was
verified once by hand — a regression in either half would silently reintroduce the
exact data-loss the slice was built to prevent.

Every test redirects schema.SEED_CONFIG_PATH to a temp file so the real household
config (data/seed_config.json) is never touched.

Run:  .venv/bin/python -m unittest discover tests/ -v
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import schema
from schema import (add_merchant_rule, export_user_corrections, init_db,
                    seed_merchant_rules, seed_user_corrections)


def _fresh_db():
    """In-memory DB with two spend categories (Groceries=1, Eating Out=2)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    conn.executemany(
        "INSERT INTO categories (id, name, type) VALUES (?, ?, 'spend')",
        [(1, "Groceries"), (2, "Eating Out")],
    )
    conn.commit()
    return conn


def _rule(conn, pattern):
    """(category_name, source) for a pattern, or None if absent."""
    row = conn.execute(
        """SELECT c.name AS category, mr.source
           FROM merchant_rules mr JOIN categories c ON c.id = mr.category_id
           WHERE mr.pattern = ?""",
        (pattern,),
    ).fetchone()
    return (row["category"], row["source"]) if row else None


class TestUserCorrectionRoundTrip(unittest.TestCase):
    """A correction made in the live DB must reappear, intact, in a DB rebuilt
    from config alone — the whole point of DATA-1."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cfg_path = Path(self._tmpdir) / "seed_config.json"
        self._cfg_path.write_text(json.dumps({"user_corrections": []}))
        self._orig_path = schema.SEED_CONFIG_PATH
        schema.SEED_CONFIG_PATH = self._cfg_path

    def tearDown(self):
        schema.SEED_CONFIG_PATH = self._orig_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _corrections_in_config(self):
        return json.loads(self._cfg_path.read_text())["user_corrections"]

    def test_new_correction_survives_rebuild(self):
        # 1. User corrects a merchant in the live DB.
        conn1 = _fresh_db()
        add_merchant_rule(conn1, "WHOLE FOODS", "Groceries")
        # add_merchant_rule persisted it to config (the append path).
        self.assertIn(["WHOLE FOODS", "Groceries"], self._corrections_in_config())

        # 2. Rebuild a fresh DB from config only.
        conn2 = _fresh_db()
        seed_user_corrections(conn2, [tuple(r) for r in self._corrections_in_config()])

        # 3. The rule is present, correctly categorised, marked user_correction.
        self.assertEqual(_rule(conn2, "WHOLE FOODS"), ("Groceries", "user_correction"))

    def test_recategorization_survives_rebuild(self):
        # Correcting the same merchant twice must not duplicate or strand the
        # first value — exercises the update-in-place path in the config write.
        conn1 = _fresh_db()
        add_merchant_rule(conn1, "SQUARE COFFEE", "Groceries")
        add_merchant_rule(conn1, "SQUARE COFFEE", "Eating Out")

        corrections = self._corrections_in_config()
        self.assertEqual(
            [c for c in corrections if c[0] == "SQUARE COFFEE"],
            [["SQUARE COFFEE", "Eating Out"]],  # exactly one entry, latest value
        )

        conn2 = _fresh_db()
        seed_user_corrections(conn2, [tuple(r) for r in corrections])
        self.assertEqual(_rule(conn2, "SQUARE COFFEE"), ("Eating Out", "user_correction"))

    def test_user_correction_overrides_seed_rule_on_rebuild(self):
        # The rebuild order is seed rules first, then user corrections. A user
        # correction on the same pattern must win — the precedence a regression
        # in the ON CONFLICT clause would silently break (the bug code review
        # already caught once on this branch).
        conn1 = _fresh_db()
        add_merchant_rule(conn1, "COSTCO", "Eating Out")  # user disagrees with seed
        corrections = [tuple(r) for r in self._corrections_in_config()]

        conn2 = _fresh_db()
        seed_merchant_rules(conn2, [("COSTCO", "Groceries")])  # seed default
        seed_user_corrections(conn2, corrections)              # correction overrides

        self.assertEqual(_rule(conn2, "COSTCO"), ("Eating Out", "user_correction"))

    def test_export_writes_corrections_and_excludes_seed(self):
        # The reverse direction: export_user_corrections() is how the 43-rule
        # bootstrap wrote the DB to config. It must export every user_correction,
        # sorted, and never leak a seed rule — then rebuild losslessly.
        conn = _fresh_db()
        seed_merchant_rules(conn, [("NETFLIX", "Groceries")])  # seed rule — must NOT export
        add_merchant_rule(conn, "BLUE BOTTLE", "Eating Out")
        add_merchant_rule(conn, "AISLE 5", "Groceries")

        count = export_user_corrections(conn)
        exported = self._corrections_in_config()

        self.assertEqual(count, 2)
        # Sorted by pattern; the seed rule (NETFLIX) is absent.
        self.assertEqual(
            exported, [["AISLE 5", "Groceries"], ["BLUE BOTTLE", "Eating Out"]]
        )

        # Full DB -> config -> DB round trip: corrections rebuild, seed not promoted.
        conn2 = _fresh_db()
        seed_user_corrections(conn2, [tuple(r) for r in exported])
        self.assertEqual(_rule(conn2, "BLUE BOTTLE"), ("Eating Out", "user_correction"))
        self.assertEqual(_rule(conn2, "AISLE 5"), ("Groceries", "user_correction"))
        self.assertIsNone(_rule(conn2, "NETFLIX"))


if __name__ == "__main__":
    unittest.main()
