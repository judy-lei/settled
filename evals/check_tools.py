"""
Deterministic tool-output check — Slice 1.

Asserts the read tools return the hand-computed ground truth in
`fixture_expected.py` for the tool-checkable half of S01–S08. No model in the
loop: this is the regression lock that makes `fixture_expected.py` active rather
than declarative. If a future change silently alters a tool's numbers, this
fails.

This is NOT the reliability eval. Whether the *agent* picks the right tool and
narrates without embellishment — the slice's actual risk — is measured by the
agent-behavior suite (LLM in the loop, n_trials, must-not-compute, caveat
wording) that lands in Slice 2 as `evals/run.py`. See EVAL_SPEC.md.

Run: .venv/bin/python evals/check_tools.py   (exit 0 = all pass, 1 = any fail)
"""

import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_FIXTURES))

import fixture_expected as fx
from build_fixture import EVAL_DB, FIXTURE_JSON, build

from agent.tools_read import get_settlement, query_spend
from categories import categorize, map_wealthsimple_category, map_amex_annual_category
from report import get_review_metrics, spend_by_category, spend_by_payer, spend_total


def _money(x) -> float:
    """Compare dollar figures at cent precision, never on raw float identity."""
    return round((x or 0.0) + 0.0, 2)


def _pet_subset(txns: list) -> list:
    return [
        {"date": t["date"], "merchant": t["merchant"],
         "amount": _money(t["amount"]), "owner": t["owner"]}
        for t in txns
    ]


_checks: list[tuple] = []


def check(name: str, actual, expected) -> None:
    _checks.append((actual == expected, name, expected, actual))


def rebuild_fixture() -> None:
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    conn = sqlite3.connect(EVAL_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    build(conn, json.loads(FIXTURE_JSON.read_text()))
    conn.close()


def run_checks(conn: sqlite3.Connection) -> None:
    # S01 / S08 — total June spend (payments excluded; equal total proves it)
    r = query_spend(conn, "2026-06")
    check("S01 June total spend", _money(r["total"]), _money(fx.JUNE_TOTAL_SPEND))
    check("S01 June txn_count", r["txn_count"], fx.JUNE_TXN_COUNT)
    check("S01 June uncategorized count",
          r["caveats"]["uncategorized_count_in_period"], fx.JUNE_UNCATEGORIZED_COUNT)
    check("S08 $400 payment excluded (total unchanged)",
          _money(r["total"]), _money(fx.JUNE_TOTAL_SPEND))

    # S02 — Groceries, June
    r = query_spend(conn, "2026-06", category="Groceries")
    check("S02 June Groceries total", _money(r["total"]), _money(fx.JUNE_GROCERIES_TOTAL))
    check("S02 June Groceries txn_count", r["txn_count"], fx.JUNE_GROCERIES_TXN_COUNT)

    # S03 — Eating Out, Sam, June
    r = query_spend(conn, "2026-06", owner="Sam", category="Eating Out")
    check("S03 June Sam Eating Out total", _money(r["total"]), _money(fx.JUNE_EATING_OUT_SAM))
    check("S03 June Sam Eating Out txn_count", r["txn_count"], fx.JUNE_EATING_OUT_SAM_TXN_COUNT)

    # S06 — Pet, May, list mode (also exercises the new truncation fields)
    r = query_spend(conn, "2026-05", category="Pet", mode="list")
    check("S06 May Pet returned", r["returned"], len(fx.MAY_PET_TRANSACTIONS))
    check("S06 May Pet not truncated", r["list_truncated"], False)
    check("S06 May Pet rows", _pet_subset(r["transactions"]), _pet_subset(fx.MAY_PET_TRANSACTIONS))

    # S07 — net Shopping, May (refund nets against purchase)
    r = query_spend(conn, "2026-05", category="Shopping")
    check("S07 May Shopping net total", _money(r["total"]), _money(fx.MAY_SHOPPING_NET))
    check("S07 May Shopping txn_count", r["txn_count"], fx.MAY_SHOPPING_TXN_COUNT)
    check("S07 May Shopping credit_count", r["credit_count"], 1)
    check("S07 May Shopping credit_total", _money(r["credit_total"]), 25.00)

    # S04 / S05 — settlement, June
    s = get_settlement(conn, "2026-06")
    users = {u["display_name"]: u for u in s["users"]}
    check("S04 Alex paid", _money(users["Alex"]["paid"]), _money(fx.JUNE_ALEX_PAID))
    check("S04 Alex fair_share", _money(users["Alex"]["fair_share"]), _money(fx.JUNE_ALEX_FAIR_SHARE))
    check("S04 Alex balance", _money(users["Alex"]["balance"]), _money(fx.JUNE_ALEX_BALANCE))
    check("S04 Sam paid", _money(users["Sam"]["paid"]), _money(fx.JUNE_SAM_PAID))
    check("S04 Sam fair_share", _money(users["Sam"]["fair_share"]), _money(fx.JUNE_SAM_FAIR_SHARE))
    check("S04 Sam balance", _money(users["Sam"]["balance"]), _money(fx.JUNE_SAM_BALANCE))
    check("S04 settlement from_user",
          s["settlement"]["from_user"]["display_name"], fx.JUNE_SETTLEMENT["from_user"])
    check("S04 settlement to_user",
          s["settlement"]["to_user"]["display_name"], fx.JUNE_SETTLEMENT["to_user"])
    check("S04 settlement amount", _money(s["settlement"]["amount"]), _money(fx.JUNE_SETTLEMENT["amount"]))
    check("S05 settlement surfaces uncategorized_count", s["uncategorized_count"], fx.JUNE_UNCATEGORIZED_COUNT)

    # Books-balance invariants (deterministic, independent of the constants above)
    check("Invariant: fair shares sum to total spend",
          _money(users["Alex"]["fair_share"] + users["Sam"]["fair_share"]), _money(s["total_spend"]))
    check("Invariant: balances sum to zero",
          _money(users["Alex"]["balance"] + users["Sam"]["balance"]), 0.00)


def check_write_path() -> None:
    """Lock the categorize() write-path contract: unmatched input must produce
    category=None, not the string 'Uncategorized'. These assertions are the
    root-cause fix for P-01 — they prevent the fixture/pipeline divergence
    that let the bug survive undetected."""
    _unmatched = categorize("UNKNOWN MERCHANT XYZ", rules=[])
    check("write path: unmatched merchant → None",
          _unmatched["category"], None)
    check("write path: unmatched merchant category_source",
          _unmatched["category_source"], "none")
    check("write path: WS 'miscellaneous' → None",
          map_wealthsimple_category("miscellaneous"), None)
    check("write path: WS 'rent' → None",
          map_wealthsimple_category("rent"), None)
    check("write path: WS 'other work' → None",
          map_wealthsimple_category("other work"), None)
    check("write path: WS 'uncategorized' → None",
          map_wealthsimple_category("uncategorized"), None)
    check("write path: Amex 'other/other charges' → None",
          map_amex_annual_category("other", "other charges"), None)
    # Confirm a mapped value still works (regression guard)
    check("write path: WS 'groceries' still maps correctly",
          map_wealthsimple_category("groceries"), "Groceries")


def check_report_queries(conn: sqlite3.Connection) -> None:
    """CR-1 regression: report.py TOTAL and by-category must include NULL-category rows.
    Pre-fix, the INNER JOIN in SPEND_FILTER silently dropped them.

    Calls report.py's own spend_by_category()/spend_total() (not a cloned SQL
    string) so a regression in the real query is what this test would catch —
    the structural gap the original CR-1 check had."""
    period_filter = "AND substr(t.transaction_date, 1, 7) = :period"
    params = {"period": "2026-06"}

    total = spend_total(conn, period_filter, params)
    check("CR-1 report total includes uncategorized ($370 + T14+T15+T16 = $478)",
          _money(total), _money(fx.JUNE_REPORT_TOTAL_SPEND))

    cat_rows = spend_by_category(conn, period_filter, params)
    uncategorized = next((r for r in cat_rows if r["category"] == 'Uncategorized'), None)
    check("CR-1 by-category shows Uncategorized row",
          uncategorized is not None, True)
    check("CR-1 by-category Uncategorized total (T14+T15+T16 = $108)",
          _money(uncategorized["total"] if uncategorized else 0),
          _money(fx.JUNE_REPORT_TOTAL_SPEND - fx.JUNE_TOTAL_SPEND))

    # HARDEN-1 read-site audit: by-payer is the same spend surface grouped by
    # owner, so it must reconcile to the by-category total. Lock it against
    # hand-computed per-payer figures AND assert the cross-grouping invariant,
    # so a stale owner_id or a category JOIN creeping into by-payer goes red.
    payer_rows = spend_by_payer(conn, period_filter, params)
    by_payer = {r["payer"]: _money(r["total"]) for r in payer_rows}
    check("by-payer surfaces exactly the two payers (none dropped)",
          sorted(by_payer), sorted(fx.JUNE_REPORT_BY_PAYER))
    for name, expected in fx.JUNE_REPORT_BY_PAYER.items():
        check(f"by-payer {name} total (categorized + uncategorized)",
              by_payer.get(name), _money(expected))
    # Exact equality holds because every June fixture amount is whole dollars,
    # so sum-of-rounded-per-group == round-of-sum. If an odd-cent row is ever
    # added to the June fixture, expect this to need a ±1¢ tolerance (that would
    # be the residual-cent rounding gap surfacing, not a by-payer regression).
    check("reconcile: by-payer total == by-category total == spend_total",
          (_money(sum(by_payer.values())),
           _money(sum(r["total"] for r in cat_rows))),
          (_money(total), _money(total)))

    # uncategorized_at_import lock: the freshly built fixture has had no review
    # pass, so every still-blank row is exactly a row the rules blanked at import.
    # The durable marker (blanked_by_rules) must equal the live NULL-category
    # count AND the hand-counted June blanks (T14, T15, T16) — catches the
    # importer/fixture failing to stamp the marker.
    m = get_review_metrics(conn, "2026-06")
    check("blanked_by_rules == live uncategorized (no review done yet)",
          m["blanked_by_rules"], m["uncategorized"])
    check("blanked_by_rules == hand-counted June blanks (T14, T15, T16)",
          m["blanked_by_rules"], 3)


def main() -> int:
    check_write_path()
    rebuild_fixture()
    conn = sqlite3.connect(EVAL_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_checks(conn)
        check_report_queries(conn)
    finally:
        conn.close()

    failures = [c for c in _checks if not c[0]]
    print()
    for ok, name, expected, actual in _checks:
        tag = "PASS" if ok else "FAIL"
        line = f"  [{tag}] {name}"
        if not ok:
            line += f"\n         expected: {expected!r}\n         actual:   {actual!r}"
        print(line)
    print(f"\n{len(_checks) - len(failures)}/{len(_checks)} checks passed.")
    if failures:
        print(f"{len(failures)} FAILED — tool output no longer matches fixture_expected.py.")
        return 1
    print("All tool outputs match hand-computed ground truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
