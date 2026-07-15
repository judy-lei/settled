"""
Hand-computed ground truth for S01-S08.

These values were derived from fixture_transactions.json and verified against
eval.db via build_fixture.py. The arithmetic is shown inline so a reviewer
can check it without running any code.

The arithmetic below was hand-computed from fixture_transactions.json
and independently verified against eval.db before being used as ground truth.
Any change to the fixture requires re-verifying the totals here.
"""

# ---------------------------------------------------------------------------
# June 2026 qualifying transactions
# (categorized, type not in payment/transfer, not confirmed_duplicate)
#
#   T08  Groceries   Alex  LOBLAWS       +150.00
#   T09  Groceries   Sam   METRO         +100.00
#   T10  Eating Out  Sam   MCDONALD'S     +22.00
#   T11  Eating Out  Sam   BURGER KING    +18.00
#   T12  Coffee&Tea  Alex  STARBUCKS      +30.00
#   T13  Transport   Alex  PRESTO         +50.00
#
#   Excluded: T17 (payment), T18 (confirmed_duplicate), T14/T15/T16 (uncategorized)
# ---------------------------------------------------------------------------

# S01 — total spend June, no filter
JUNE_TOTAL_SPEND = 370.00          # 150+100+22+18+30+50
JUNE_TXN_COUNT = 6
JUNE_UNCATEGORIZED_COUNT = 3      # T14, T15, T16

# S02 — Groceries, June
JUNE_GROCERIES_TOTAL = 250.00     # T08(150) + T09(100)
JUNE_GROCERIES_TXN_COUNT = 2

# S03 — Eating Out, Sam, June
JUNE_EATING_OUT_SAM = 40.00       # T10(22) + T11(18)
JUNE_EATING_OUT_SAM_TXN_COUNT = 2

# S04 / S05 — settlement, June
#
# What each user paid (they own the account):
#   Alex paid: T08(150) + T12(30) + T13(50) = 230.00
#   Sam  paid: T09(100) + T10(22) + T11(18) = 140.00
#
# Fair shares (by category split):
#   Groceries  $250 total → Alex 60% = 150.00 | Sam 40% = 100.00
#   Eating Out  $40 total → Alex 50% =  20.00 | Sam 50% =  20.00
#   Coffee&Tea  $30 total → Alex 50% =  15.00 | Sam 50% =  15.00
#   Transport   $50 total → Alex 50% =  25.00 | Sam 50% =  25.00
#                           Alex total 210.00 | Sam total 160.00
#   Cross-check: 210 + 160 = 370 ✓
#
# Balances (paid - fair_share):
#   Alex: 230 - 210 = +20.00  (overpaid — is owed)
#   Sam:  140 - 160 = -20.00  (underpaid — owes)
JUNE_SETTLEMENT = {
    "from_user": "Sam",
    "to_user": "Alex",
    "amount": 20.00,
}
JUNE_ALEX_PAID = 230.00
JUNE_SAM_PAID = 140.00
JUNE_ALEX_FAIR_SHARE = 210.00
JUNE_SAM_FAIR_SHARE = 160.00
JUNE_ALEX_BALANCE = 20.00         # positive = overpaid = is owed
JUNE_SAM_BALANCE = -20.00

# S05 — settlement must surface uncategorized caveat
# Same settlement numbers as S04, but response must mention
# that 3 transactions are uncategorized and excluded.
JUNE_SETTLEMENT_UNCATEGORIZED_CAVEAT = True   # assert mentioned in response

# S06 — Pet transactions, May, list mode
MAY_PET_TRANSACTIONS = [
    {"date": "2026-05-10", "merchant": "PETCO", "amount": 80.00, "owner": "Alex"},
    {"date": "2026-05-22", "merchant": "PETCO", "amount": 35.00, "owner": "Alex"},
]

# S07 — net Shopping, May (includes refund)
#   T03 INDIGO  +60.00 (purchase)
#   T04 INDIGO  -25.00 (refund / credit)
#   Net = 60 - 25 = 35.00
MAY_SHOPPING_NET = 35.00
MAY_SHOPPING_TXN_COUNT = 2        # both rows returned; net amount is what matters

# S08 — total June must NOT include T17 ($400 payment)
# Payments are excluded by the spend filter; total remains 370.00.
# This scenario verifies the agent does not add T17 into the total.
JUNE_TOTAL_EXCLUDES_PAYMENTS = True   # assert: $400 payment not in response total
# The correct total is still JUNE_TOTAL_SPEND = 370.00

# CR-1 regression — report.py TOTAL must include NULL-category rows.
# T14 ($75) + T15 ($15) + T16 ($18) = $108 uncategorized June spend.
# JUNE_TOTAL_SPEND ($370) is the agent-tool total (intentionally excludes
# uncategorized — they appear in the caveat instead). JUNE_REPORT_TOTAL_SPEND
# is what report.py should show after dropping the vestigial INNER JOIN.
JUNE_REPORT_TOTAL_SPEND = 478.00   # JUNE_TOTAL_SPEND + T14+T15+T16
