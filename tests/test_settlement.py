"""
Settlement math test cases — Slice 0.

Unit tests (TestComputeSettlement) — pure function, no DB.
Integration tests (TestGetSettlementDataFilters) — in-memory SQLite, verify
SQL filter and split-percentage correctness for cases that cannot be tested
without running the query.

Hand-computed expected results are embedded as comments beside each assertion.
Every unit test asserts sum(balances) == 0 — the financial invariant that the
books balance.

Run:  .venv/bin/python -m unittest discover tests/ -v
"""

import sys
import unittest
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import compute_settlement, get_settlement_data, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_data(paid_A, paid_B, fair_share_A, fair_share_B,
               total=None, txn_count=2, uncategorized=0):
    """Build a minimal get_settlement_data() return value for unit testing."""
    if total is None:
        total = round(paid_A + paid_B, 2)
    return {
        "period": "2026-06",
        "total_spend": total,
        "txn_count": txn_count,
        "uncategorized_count": uncategorized,
        "users": [
            {"id": 1, "display_name": "Alice", "paid": paid_A, "fair_share": fair_share_A},
            {"id": 2, "display_name": "Bob",   "paid": paid_B, "fair_share": fair_share_B},
        ],
    }


def _setup_db(alice_pct=50.0, bob_pct=50.0):
    """In-memory DB seeded with 2 users, 2 accounts, 2 import files, 2 categories.

    alice_pct / bob_pct set the Groceries split — default 50/50.
    Transfer-type category (id=2) never gets splits (correct by design).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    conn.executemany("INSERT INTO users (id, display_name) VALUES (?, ?)",
                     [(1, "Alice"), (2, "Bob")])
    conn.executemany(
        "INSERT INTO accounts"
        " (id, owner_id, institution, account_name, account_type)"
        " VALUES (?, ?, ?, ?, ?)",
        [(1, 1, "Amex", "Cobalt", "credit_card"),
         (2, 2, "Amex", "Gold",   "credit_card")],
    )
    conn.executemany(
        "INSERT INTO import_files"
        " (id, account_id, source_filename, source_format,"
        "  row_count, source_hash, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(1, 1, "alice.csv", "amex_monthly", 1, "hash_alice", "2026-07-01"),
         (2, 2, "bob.csv",   "amex_monthly", 1, "hash_bob",   "2026-07-01")],
    )
    # category 1: Groceries (spend)  category 2: Payment (transfer — no splits)
    conn.executemany("INSERT INTO categories (id, name, type) VALUES (?, ?, ?)",
                     [(1, "Groceries", "spend"), (2, "Payment", "transfer")])
    conn.executemany(
        "INSERT INTO category_splits (category_id, user_id, pct) VALUES (?, ?, ?)",
        [(1, 1, alice_pct), (1, 2, bob_pct)],
    )
    conn.commit()
    return conn


def _insert_txn(conn, *, txn_id, owner_id, account_id, import_file_id=None,
                amount, direction, txn_type, category_id=1,
                dup_status="unique", period="2026-06"):
    # import_file_id defaults to account_id so existing callers don't need updating;
    # both are seeded with matching ids (account 1 → import_file 1, account 2 → import_file 2).
    if import_file_id is None:
        import_file_id = account_id
    conn.execute("""
        INSERT INTO transactions
            (id, import_file_id, account_id, owner_id,
             merchant_raw, merchant_normalized, transaction_date,
             amount, direction, transaction_type,
             category_id, category_source, review_status, duplicate_status)
        VALUES (?, ?, ?, ?, 'MERCHANT', 'MERCHANT', ?,
                ?, ?, ?,
                ?, 'merchant_rule', 'unreviewed', ?)
    """, (txn_id, import_file_id, account_id, owner_id,
          f"{period}-15",
          amount, direction, txn_type,
          category_id, dup_status))
    conn.commit()


# ---------------------------------------------------------------------------
# Unit tests — compute_settlement()
# ---------------------------------------------------------------------------

class TestComputeSettlement(unittest.TestCase):

    def _assert_balances_sum_to_zero(self, result):
        """Financial invariant: paid_A + paid_B == fair_share_A + fair_share_B == total_spend,
        so balance_A + balance_B must equal 0. Holds exactly for hand-computed mock data."""
        total = sum(u["balance"] for u in result["users"])
        self.assertAlmostEqual(
            total, 0.0, places=2,
            msg=f"Balances sum to {total:.4f}, expected 0 — books don't balance",
        )

    def test_basic_50_50_alice_paid_all(self):
        # Alice paid $200, Bob paid $0; all Groceries 50/50.
        # total = $200; fair_share each = $100.
        # balance_Alice = $200 − $100 = +$100 (overpaid)
        # balance_Bob   = $0   − $100 = −$100 (owes)
        # settlement: Bob owes Alice $100.
        data = _mock_data(paid_A=200.0, paid_B=0.0,
                          fair_share_A=100.0, fair_share_B=100.0, total=200.0)
        result = compute_settlement(data)

        alice = next(u for u in result["users"] if u["id"] == 1)
        bob   = next(u for u in result["users"] if u["id"] == 2)
        self.assertAlmostEqual(alice["balance"],  100.0)
        self.assertAlmostEqual(bob["balance"],   -100.0)
        self._assert_balances_sum_to_zero(result)

        s = result["settlement"]
        self.assertIsNotNone(s)
        self.assertEqual(s["from_user"]["display_name"], "Bob")
        self.assertEqual(s["to_user"]["display_name"],   "Alice")
        self.assertAlmostEqual(s["amount"], 100.0)

    def test_non_even_split_60_40(self):
        # Alice paid $180 Groceries, Bob paid $80 Groceries; split 60% Alice / 40% Bob.
        # total = $260
        # fair_share_Alice = $260 × 0.60 = $156.00
        # fair_share_Bob   = $260 × 0.40 = $104.00  (check: $156 + $104 = $260 ✓)
        # balance_Alice = $180 − $156 = +$24.00 (overpaid)
        # balance_Bob   = $80  − $104 = −$24.00 (owes)
        # settlement: Bob owes Alice $24.00.
        data = _mock_data(paid_A=180.0, paid_B=80.0,
                          fair_share_A=156.0, fair_share_B=104.0, total=260.0)
        result = compute_settlement(data)

        alice = next(u for u in result["users"] if u["id"] == 1)
        bob   = next(u for u in result["users"] if u["id"] == 2)
        self.assertAlmostEqual(alice["balance"],  24.0)
        self.assertAlmostEqual(bob["balance"],   -24.0)
        self._assert_balances_sum_to_zero(result)

        s = result["settlement"]
        self.assertIsNotNone(s)
        self.assertEqual(s["from_user"]["display_name"], "Bob")
        self.assertEqual(s["to_user"]["display_name"],   "Alice")
        self.assertAlmostEqual(s["amount"], 24.0)

    def test_refund_reduces_net_paid(self):
        # Alice: $100 purchase + $20 refund → net paid $80.
        # Bob:   $60 purchase.  All 50/50.
        # total = $140; fair_share each = $70.
        # balance_Alice = $80  − $70 = +$10 (overpaid)
        # balance_Bob   = $60  − $70 = −$10 (owes)
        # settlement: Bob owes Alice $10.
        data = _mock_data(paid_A=80.0, paid_B=60.0,
                          fair_share_A=70.0, fair_share_B=70.0, total=140.0, txn_count=3)
        result = compute_settlement(data)

        self._assert_balances_sum_to_zero(result)

        s = result["settlement"]
        self.assertIsNotNone(s)
        self.assertEqual(s["from_user"]["display_name"], "Bob")
        self.assertEqual(s["to_user"]["display_name"],   "Alice")
        self.assertAlmostEqual(s["amount"], 10.0)

    def test_100_0_split(self):
        # Alice paid $100 Groceries (50/50).
        # Bob   paid $80 Rental Property (100% Alice, 0% Bob).
        # fair_share_Alice = $100×0.5 + $80×1.0 = $130
        # fair_share_Bob   = $100×0.5 + $80×0.0 = $50   (check: $130 + $50 = $180 ✓)
        # balance_Alice = $100 − $130 = −$30 (owes)
        # balance_Bob   = $80  − $50  = +$30 (overpaid)
        # settlement: Alice owes Bob $30.
        data = _mock_data(paid_A=100.0, paid_B=80.0,
                          fair_share_A=130.0, fair_share_B=50.0, total=180.0)
        result = compute_settlement(data)

        self._assert_balances_sum_to_zero(result)

        s = result["settlement"]
        self.assertIsNotNone(s)
        self.assertEqual(s["from_user"]["display_name"], "Alice")
        self.assertEqual(s["to_user"]["display_name"],   "Bob")
        self.assertAlmostEqual(s["amount"], 30.0)

    def test_empty_period(self):
        # No qualifying transactions — all zeros.
        # settlement: None (nothing owed).
        data = _mock_data(paid_A=0.0, paid_B=0.0,
                          fair_share_A=0.0, fair_share_B=0.0, total=0.0, txn_count=0)
        result = compute_settlement(data)

        for u in result["users"]:
            self.assertAlmostEqual(u["balance"], 0.0)
        self._assert_balances_sum_to_zero(result)
        self.assertIsNone(result["settlement"])

    def test_exact_tie(self):
        # Alice paid $100, Bob paid $100; all 50/50.
        # fair_share each = $100; balance = $0 for both.
        # settlement: None (exactly square).
        data = _mock_data(paid_A=100.0, paid_B=100.0,
                          fair_share_A=100.0, fair_share_B=100.0, total=200.0)
        result = compute_settlement(data)

        self._assert_balances_sum_to_zero(result)
        self.assertIsNone(result["settlement"])


# ---------------------------------------------------------------------------
# Integration tests — get_settlement_data() SQL filter and split correctness
# ---------------------------------------------------------------------------

class TestGetSettlementDataFilters(unittest.TestCase):

    def setUp(self):
        self.conn = _setup_db()  # default 50/50 splits

    def tearDown(self):
        self.conn.close()

    def test_payment_transaction_excluded(self):
        # Alice has a $200 Groceries purchase AND a $200 card payment categorised
        # as Groceries. The payment row must be excluded by the transaction_type
        # filter regardless of its category.
        # Expected: total=$200, txn_count=1.
        _insert_txn(self.conn, txn_id=1, owner_id=1, account_id=1,
                    amount=200.0, direction="debit", txn_type="purchase", category_id=1)
        _insert_txn(self.conn, txn_id=2, owner_id=1, account_id=1,
                    amount=200.0, direction="debit", txn_type="payment", category_id=1)

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 1)
        self.assertAlmostEqual(data["total_spend"], 200.0)

    def test_transfer_transaction_excluded(self):
        # Bob has a $120 inter-account transfer categorised as Groceries (spend).
        # Alice has a $100 Groceries purchase.
        # The transfer row must be excluded by the transaction_type filter
        # regardless of its category — same filter as payments.
        # Expected: total=$100, txn_count=1.
        _insert_txn(self.conn, txn_id=1, owner_id=1, account_id=1,
                    amount=100.0, direction="debit", txn_type="purchase", category_id=1)
        _insert_txn(self.conn, txn_id=2, owner_id=2, account_id=2,
                    amount=120.0, direction="debit", txn_type="transfer", category_id=1)

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 1)
        self.assertAlmostEqual(data["total_spend"], 100.0)

    def test_confirmed_duplicate_excluded(self):
        # Two identical Alice rows — the second is marked confirmed_duplicate.
        # Expected: total=$100, txn_count=1.
        _insert_txn(self.conn, txn_id=1, owner_id=1, account_id=1,
                    amount=100.0, direction="debit", txn_type="purchase",
                    category_id=1, dup_status="unique")
        _insert_txn(self.conn, txn_id=2, owner_id=1, account_id=1,
                    amount=100.0, direction="debit", txn_type="purchase",
                    category_id=1, dup_status="confirmed_duplicate")

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 1)
        self.assertAlmostEqual(data["total_spend"], 100.0)

    def test_non_spend_category_excluded(self):
        # Bob has a $50 purchase categorised as 'Payment' (type='transfer').
        # That category type must exclude it from settlement totals.
        # Expected: txn_count=0, total_spend=0.
        _insert_txn(self.conn, txn_id=1, owner_id=2, account_id=2,
                    amount=50.0, direction="debit", txn_type="purchase", category_id=2)

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 0)
        self.assertAlmostEqual(data["total_spend"], 0.0)

    def test_different_period_excluded(self):
        # Alice has a $150 Groceries purchase in July — must not appear in a
        # June settlement query.
        # Expected: txn_count=0, total_spend=0 for "2026-06".
        _insert_txn(self.conn, txn_id=1, owner_id=1, account_id=1,
                    amount=150.0, direction="debit", txn_type="purchase",
                    category_id=1, period="2026-07")

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 0)
        self.assertAlmostEqual(data["total_spend"], 0.0)

    def test_uncategorized_excluded_from_spend_but_counted(self):
        # A $75 purchase with NULL category_id is excluded from spend totals
        # (can't join to category_splits) but must appear in uncategorized_count.
        # Expected: txn_count=0, total_spend=0, uncategorized_count=1.
        _insert_txn(self.conn, txn_id=1, owner_id=1, account_id=1,
                    amount=75.0, direction="debit", txn_type="purchase",
                    category_id=None)

        data = get_settlement_data(self.conn, "2026-06")
        self.assertEqual(data["txn_count"], 0)
        self.assertAlmostEqual(data["total_spend"], 0.0)
        self.assertEqual(data["uncategorized_count"], 1)

    def test_non_even_split_60_40_sql(self):
        # Verifies the SQL correctly reads pct from category_splits and weights
        # fair_share accordingly — this is what the unit test cannot cover.
        #
        # Setup: Groceries split 60% Alice / 40% Bob.
        # Alice paid $180 (Groceries), Bob paid $80 (Groceries).
        # total = $260
        # fair_share_Alice = $260 × 0.60 = $156.00
        # fair_share_Bob   = $260 × 0.40 = $104.00
        # balance_Alice = $180 − $156 = +$24.00 → Bob owes Alice $24.00.
        conn = _setup_db(alice_pct=60.0, bob_pct=40.0)
        _insert_txn(conn, txn_id=1, owner_id=1, account_id=1,
                    amount=180.0, direction="debit", txn_type="purchase", category_id=1)
        _insert_txn(conn, txn_id=2, owner_id=2, account_id=2,
                    amount=80.0, direction="debit", txn_type="purchase", category_id=1)

        data = get_settlement_data(conn, "2026-06")
        result = compute_settlement(data)
        conn.close()

        alice = next(u for u in result["users"] if u["id"] == 1)
        bob   = next(u for u in result["users"] if u["id"] == 2)
        self.assertAlmostEqual(alice["paid"],       180.0)
        self.assertAlmostEqual(bob["paid"],          80.0)
        self.assertAlmostEqual(alice["fair_share"], 156.0)
        self.assertAlmostEqual(bob["fair_share"],   104.0)
        self.assertAlmostEqual(alice["balance"],     24.0)
        self.assertAlmostEqual(bob["balance"],      -24.0)

        s = result["settlement"]
        self.assertIsNotNone(s)
        self.assertEqual(s["from_user"]["display_name"], "Bob")
        self.assertEqual(s["to_user"]["display_name"],   "Alice")
        self.assertAlmostEqual(s["amount"], 24.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
