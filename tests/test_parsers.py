"""
Parser contract tests.

Locks the `direction` semantic that SIGNED_AMOUNT (report.py, get_settlement_data,
review metrics) reads: debit = spend goes up, credit = spend goes back. Parsers
whose source CSVs use inverted sign conventions must invert on the way in — a
regression here silently sign-flips every downstream money reading, unseen by
the trust-test on any file that has no registered statement_total.

Run:  .venv/bin/python -m unittest discover tests/ -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from parsers import parse_ws_visa


WS_VISA_CSV = (
    "transaction_date,transaction_type,status,merchant,amount,currency,notes,category\n"
    "2026-05-15,Purchase,Completed,GROCERIES INC,-42.50,CAD,,Groceries\n"
    "2026-05-16,Refund,Completed,GROCERIES INC,7.25,CAD,,Groceries\n"
    "2026-05-20,Payment,Completed,PAYMENT RECEIVED,1000.00,CAD,,Payment\n"
)


class TestWSVisaSignConvention(unittest.TestCase):
    """WS Visa CSV: purchases negative, refunds/payments positive. The parser
    must invert so `direction` matches the accounting sense downstream reads."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        self.tmp.write(WS_VISA_CSV)
        self.tmp.close()
        self.df = parse_ws_visa(Path(self.tmp.name))

    def tearDown(self):
        Path(self.tmp.name).unlink()

    def test_purchase_negative_raw_is_debit(self):
        purchase = self.df[self.df["transaction_type"] == "purchase"].iloc[0]
        self.assertEqual(purchase["direction"], "debit")
        self.assertEqual(purchase["amount"], 42.50)

    def test_refund_positive_raw_is_credit(self):
        refund = self.df[self.df["transaction_type"] == "refund"].iloc[0]
        self.assertEqual(refund["direction"], "credit")
        self.assertEqual(refund["amount"], 7.25)

    def test_payment_positive_raw_is_credit(self):
        payment = self.df[self.df["transaction_type"] == "payment"].iloc[0]
        self.assertEqual(payment["direction"], "credit")
        self.assertEqual(payment["amount"], 1000.00)

    def test_amount_always_positive(self):
        self.assertTrue((self.df["amount"] > 0).all())


if __name__ == "__main__":
    unittest.main()
