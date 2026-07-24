"""
Review-flow tests — Slice A (trust layer).

Cover the write path (confirm_reviewed, apply_correction) and the metrics
(get_review_metrics) at money-math grade: the correction trail must be recorded
exactly, and the miscategorization-rate denominator must survive a correction —
the whole reason category_changes.old_category_source exists. Without it, the
denominator shrinks as corrections land and the rate reads too high (the P-01
class of bug: the overwrite erases the evidence the metric needs).

Metric tests generate their change rows by calling apply_correction — never by
hand-inserting category_changes — so the fixture is produced by the real code
path and cannot certify a world the code does not produce.

Every test redirects schema.SEED_CONFIG_PATH to a temp file so the real
household config (data/seed_config.json) is never touched by apply_correction's
merchant-rule write.

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
from schema import init_db
from categories import blanked_at_import
from review import apply_correction, assign_blank, confirm_reviewed
from report import get_review_metrics

PERIOD = "2026-05"


def _setup_db():
    """In-memory DB: 2 users, 1 account/import_file, 4 categories, 50/50 splits."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    conn.executemany("INSERT INTO users (id, display_name) VALUES (?, ?)",
                     [(1, "Alice"), (2, "Bob")])
    conn.execute(
        "INSERT INTO accounts (id, owner_id, institution, account_name, account_type)"
        " VALUES (1, 1, 'Amex', 'Cobalt', 'credit_card')")
    conn.execute(
        "INSERT INTO import_files"
        " (id, account_id, source_filename, source_format, row_count, source_hash, imported_at)"
        " VALUES (1, 1, 'alice.csv', 'amex_monthly', 1, 'hash', '2026-06-01')")
    conn.executemany("INSERT INTO categories (id, name, type) VALUES (?, ?, ?)",
                     [(1, "Groceries", "spend"), (2, "Eating Out", "spend"),
                      (3, "Shopping", "spend"), (4, "Payment", "transfer")])
    conn.executemany(
        "INSERT INTO category_splits (category_id, user_id, pct) VALUES (?, ?, 50.0)",
        [(c, u) for c in (1, 2, 3) for u in (1, 2)])
    conn.commit()
    return conn


def _insert_txn(conn, tid, *, category_id, source, merchant="MERCH",
                review="unreviewed", ttype="purchase", amount=10.0,
                direction="debit", dup="unique", period=PERIOD, blank=0):
    conn.execute("""
        INSERT INTO transactions
            (id, import_file_id, account_id, owner_id,
             merchant_raw, merchant_normalized, transaction_date,
             amount, direction, transaction_type,
             category_id, category_source, uncategorized_at_import,
             review_status, duplicate_status)
        VALUES (?, 1, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tid, merchant, merchant, f"{period}-15",
          amount, direction, ttype, category_id, source, blank, review, dup))
    conn.commit()



def _changes(conn, tid=None):
    sql = "SELECT * FROM category_changes"
    params = ()
    if tid is not None:
        sql += " WHERE transaction_id = ?"
        params = (tid,)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def _txn(conn, tid):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()


class _RedirectsSeedConfig(unittest.TestCase):
    """apply_correction upserts a merchant rule, which persists to seed_config.
    Redirect it to a temp file so the real household config is never touched."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        cfg = Path(self._tmpdir) / "seed_config.json"
        cfg.write_text(json.dumps({"user_corrections": []}))
        self._orig_path = schema.SEED_CONFIG_PATH
        schema.SEED_CONFIG_PATH = cfg
        self.conn = _setup_db()

    def tearDown(self):
        schema.SEED_CONFIG_PATH = self._orig_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        self.conn.close()


class TestWritePath(_RedirectsSeedConfig):

    def test_confirm_inserts_no_change_row(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        n = confirm_reviewed(self.conn, [1])
        self.assertEqual(n, 1)
        self.assertEqual(_txn(self.conn, 1)["review_status"], "reviewed")
        self.assertEqual(_txn(self.conn, 1)["category_id"], 1)  # unchanged
        self.assertEqual(_changes(self.conn), [])  # no trail row

    def test_correction_inserts_exactly_one_row(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        n = apply_correction(self.conn, [1], "Eating Out")
        self.assertEqual(n, 1)

        rows = _changes(self.conn)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(
            (r["transaction_id"], r["old_category_id"],
             r["new_category_id"], r["old_category_source"]),
            (1, 1, 2, "merchant_rule"))

        t = _txn(self.conn, 1)
        self.assertEqual(t["category_id"], 2)
        self.assertEqual(t["category_source"], "user_manual")  # overwritten
        self.assertEqual(t["review_status"], "reviewed")

    def test_correcting_twice_appends_second_row(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        apply_correction(self.conn, [1], "Eating Out")   # merchant_rule -> Eating Out
        apply_correction(self.conn, [1], "Shopping")     # user_manual  -> Shopping

        rows = _changes(self.conn, 1)
        self.assertEqual(len(rows), 2)
        # First correction carries the auto source; the second records user_manual.
        self.assertEqual(rows[0]["old_category_source"], "merchant_rule")
        self.assertEqual(rows[1]["old_category_source"], "user_manual")
        self.assertEqual(rows[1]["old_category_id"], 2)   # was Eating Out
        self.assertEqual(rows[1]["new_category_id"], 3)   # now Shopping
        self.assertEqual(_txn(self.conn, 1)["category_id"], 3)

    def test_correction_to_same_category_is_skipped(self):
        # new == current: not a correction. No trail row, nothing to roll back.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        n = apply_correction(self.conn, [1], "Groceries")
        self.assertEqual(n, 0)
        self.assertEqual(_changes(self.conn), [])

    def test_uncategorized_row_is_rejected(self):
        # Initial categorization of a NULL row is the Uncategorized tab's job.
        _insert_txn(self.conn, 1, category_id=None, source="none")
        with self.assertRaises(ValueError):
            apply_correction(self.conn, [1], "Groceries")
        self.assertEqual(_changes(self.conn), [])

    def test_batch_updates_merchant_rule_once(self):
        # Two rows, same merchant, corrected together -> one merchant rule upserted.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule", merchant="AISLE 5")
        _insert_txn(self.conn, 2, category_id=1, source="merchant_rule", merchant="AISLE 5")
        n = apply_correction(self.conn, [1, 2], "Eating Out")
        self.assertEqual(n, 2)
        rule = self.conn.execute(
            "SELECT c.name FROM merchant_rules mr JOIN categories c ON c.id = mr.category_id"
            " WHERE mr.pattern = 'AISLE 5'").fetchone()
        self.assertEqual(rule["name"], "Eating Out")

    def test_correction_batch_rolls_back_on_error(self):
        # Atomic batch: if any transaction in the batch is invalid, the whole
        # correction rolls back — no partial trail, no half-applied change. txn 1
        # is corrected first (opening the transaction), then txn 2 raises.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        _insert_txn(self.conn, 2, category_id=None, source="none")  # invalid: uncategorized
        with self.assertRaises(ValueError):
            apply_correction(self.conn, [1, 2], "Eating Out")
        t = _txn(self.conn, 1)
        self.assertEqual(t["category_id"], 1)              # rolled back
        self.assertEqual(t["category_source"], "merchant_rule")
        self.assertEqual(t["review_status"], "unreviewed")
        self.assertEqual(_changes(self.conn), [])          # no trail row survived

    def test_confirm_on_uncategorized_is_noop(self):
        # "Looks right" applies only to categorized rows; confirming an
        # uncategorized row must not mark it reviewed (it would then count as
        # 'confirmed correct') and must report 0 rows touched.
        _insert_txn(self.conn, 1, category_id=None, source="none")
        n = confirm_reviewed(self.conn, [1])
        self.assertEqual(n, 0)
        self.assertEqual(_txn(self.conn, 1)["review_status"], "unreviewed")


class TestCheckConstraint(_RedirectsSeedConfig):

    def test_check_rejects_old_equals_new(self):
        # Schema-level guard: a trail row where nothing changed is meaningless.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO category_changes"
                " (transaction_id, old_category_id, new_category_id,"
                "  old_category_source, changed_at)"
                " VALUES (1, 1, 1, 'merchant_rule', '2026-05-15T00:00:00Z')")


class TestMetrics(_RedirectsSeedConfig):
    """All change rows are produced by apply_correction, never hand-inserted."""

    def test_uncategorized_rate(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        _insert_txn(self.conn, 2, category_id=None, source="none")
        _insert_txn(self.conn, 3, category_id=None, source="none")
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["total"], 3)
        self.assertEqual(m["uncategorized"], 2)
        self.assertEqual(m["uncategorized_rate"], round(2 / 3, 4))

    def test_payments_and_dupes_excluded_from_total(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        _insert_txn(self.conn, 2, category_id=4, source="transaction_type", ttype="payment")
        _insert_txn(self.conn, 3, category_id=1, source="merchant_rule", dup="confirmed_duplicate")
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["total"], 1)  # only the real charge qualifies

    def test_confirmed_vs_corrected_derivation(self):
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        _insert_txn(self.conn, 2, category_id=1, source="merchant_rule")
        _insert_txn(self.conn, 3, category_id=1, source="merchant_rule")
        confirm_reviewed(self.conn, [1, 2])       # looked at, kept
        apply_correction(self.conn, [3], "Shopping")  # looked at, changed
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["reviewed"], 3)
        self.assertEqual(m["corrected"], 1)
        self.assertEqual(m["confirmed"], 2)   # auto rows kept
        self.assertEqual(m["assigned"], 0)    # no blanks filled in this test

    def test_confirmed_vs_assigned_split(self):
        # confirmed = auto-categorized + kept; assigned = blank at import + filled in.
        # Both end up reviewed with no correction row — they must count separately
        # because they signal different things (guess quality vs rule coverage).
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule", blank=0)
        _insert_txn(self.conn, 2, category_id=None, source="none", blank=1)
        confirm_reviewed(self.conn, [1])
        assign_blank(self.conn, [2], "Groceries")
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["reviewed"], 2)
        self.assertEqual(m["confirmed"], 1)   # only the auto row
        self.assertEqual(m["assigned"], 1)    # only the blank row
        self.assertEqual(m["corrected"], 0)

    def test_miscat_denominator_survives_correction(self):
        # THE headline test. 4 rows auto-categorized by a merchant rule; correct
        # one. The corrected row is now user_manual, but it was auto-categorized,
        # so the denominator must stay 4 (not shrink to 3) and the rate must read
        # 1/4, not 1/3. This is exactly what old_category_source is captured for.
        for tid in (1, 2, 3, 4):
            _insert_txn(self.conn, tid, category_id=1, source="merchant_rule",
                        merchant=f"M{tid}")
        before = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(before["miscategorization_rate"], 0.0)
        self.assertEqual(before["miscategorization_by_source"]["merchant_rule"]["denominator"], 4)

        apply_correction(self.conn, [1], "Eating Out")

        after = get_review_metrics(self.conn, PERIOD)
        rule = after["miscategorization_by_source"]["merchant_rule"]
        self.assertEqual(rule["errors"], 1)
        self.assertEqual(rule["denominator"], 4)       # NOT 3
        self.assertEqual(rule["rate"], 0.25)           # NOT 1/3
        self.assertEqual(after["miscategorization_rate"], 0.25)

    def test_miscat_split_by_source(self):
        # Two auto sources; a wrong one from each. Rates reported separately
        # because the fixes differ (a merchant rule vs a source-map edit).
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule", merchant="R1")
        _insert_txn(self.conn, 2, category_id=1, source="source_mapped", merchant="S1")
        apply_correction(self.conn, [1], "Eating Out")  # merchant_rule error
        apply_correction(self.conn, [2], "Shopping")    # source_mapped error

        m = get_review_metrics(self.conn, PERIOD)
        by = m["miscategorization_by_source"]
        self.assertEqual(by["merchant_rule"]["errors"], 1)
        self.assertEqual(by["merchant_rule"]["denominator"], 1)
        self.assertEqual(by["merchant_rule"]["rate"], 1.0)
        self.assertEqual(by["source_mapped"]["errors"], 1)
        self.assertEqual(by["source_mapped"]["denominator"], 1)
        self.assertEqual(by["source_mapped"]["rate"], 1.0)

    def test_twice_corrected_txn_counts_once(self):
        # A transaction corrected twice is ONE corrected transaction and ONE
        # auto-source error, not two — the metric counts distinct transactions.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule")
        apply_correction(self.conn, [1], "Eating Out")   # merchant_rule -> Eating Out
        apply_correction(self.conn, [1], "Shopping")     # user_manual  -> Shopping
        self.assertEqual(len(_changes(self.conn, 1)), 2)  # two trail rows...
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["corrected"], 1)               # ...but one corrected txn
        self.assertEqual(m["miscategorization_by_source"]["merchant_rule"]["errors"], 1)

    def test_empty_month_uncat_rate_na(self):
        m = get_review_metrics(self.conn, "2020-01")
        self.assertEqual(m["total"], 0)
        self.assertEqual(m["uncategorized_rate"], "n/a")

    def test_no_auto_rows_miscat_rate_na(self):
        # Rows exist, but none are auto-categorized (all uncategorized) -> the
        # miscat denominator is zero -> 'n/a', not 0% and not a crash.
        _insert_txn(self.conn, 1, category_id=None, source="none")
        _insert_txn(self.conn, 2, category_id=None, source="none")
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["total"], 2)
        self.assertEqual(m["miscategorization_rate"], "n/a")


class TestBlankMarker(_RedirectsSeedConfig):
    """uncategorized_at_import — the durable 'rules left this blank' record.

    The whole point is that it survives the blank being filled in, so the
    'blanked_by_rules' rate stays computable after a review pass has erased the
    live NULL-category signal. Mirror of the miscat-denominator lock, one metric
    over: filling a blank must not shrink blanked_by_rules the way it would if the
    metric read the live category state.
    """

    def test_marker_survives_filling_the_blank(self):
        # THE headline test. A row the rules blanked (marker=1). Fill it in via
        # the Uncategorized-tab path. The marker must stay 1 and blanked_by_rules
        # must NOT drop to 0 — that is exactly what a live category_id read would
        # do, and why the durable column exists.
        _insert_txn(self.conn, 1, category_id=None, source="none", blank=1)
        before = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(before["blanked_by_rules"], 1)
        self.assertEqual(before["uncategorized"], 1)          # live: still blank

        assign_blank(self.conn, [1], "Shopping")

        after = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(_txn(self.conn, 1)["uncategorized_at_import"], 1)  # untouched
        self.assertEqual(after["blanked_by_rules"], 1)        # durable: still counts
        self.assertEqual(after["blanked_by_rules_rate"], 1.0)
        self.assertEqual(after["uncategorized"], 0)           # live: no longer blank

    def test_marker_not_set_for_auto_categorized_rows(self):
        # A row the rules DID categorize is not "blanked by rules", and staying
        # correct after a correction is part of the contract.
        _insert_txn(self.conn, 1, category_id=1, source="merchant_rule", blank=0)
        apply_correction(self.conn, [1], "Eating Out")
        self.assertEqual(_txn(self.conn, 1)["uncategorized_at_import"], 0)  # untouched
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["blanked_by_rules"], 0)

    def test_blanked_by_rules_rate(self):
        # Two blanked (marker 1), two auto (marker 0) -> 2/4.
        _insert_txn(self.conn, 1, category_id=None, source="none", blank=1, merchant="A")
        _insert_txn(self.conn, 2, category_id=None, source="none", blank=1, merchant="B")
        _insert_txn(self.conn, 3, category_id=1, source="merchant_rule", merchant="C")
        _insert_txn(self.conn, 4, category_id=1, source="source_mapped", merchant="D")
        m = get_review_metrics(self.conn, PERIOD)
        self.assertEqual(m["blanked_by_rules"], 2)
        self.assertEqual(m["blanked_by_rules_rate"], 0.5)

    def test_blanked_by_rules_na_on_empty_month(self):
        m = get_review_metrics(self.conn, "2020-01")
        self.assertEqual(m["blanked_by_rules"], 0)
        self.assertEqual(m["blanked_by_rules_rate"], "n/a")

    def test_blanked_at_import_rule(self):
        # The single shared rule the importer AND the fixture builder stamp with.
        # Only 'none' (the categorizer had no opinion) is a blank; every assigned
        # source is not.
        self.assertEqual(blanked_at_import("none"), 1)
        for source in ("merchant_rule", "source_mapped", "transaction_type", "user_manual"):
            self.assertEqual(blanked_at_import(source), 0)


if __name__ == "__main__":
    unittest.main()
