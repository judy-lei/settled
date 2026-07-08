"""
Read-only agent tools — Slice 1.

Each function is a thin wrapper over existing DB queries. No settlement math
is reimplemented here; get_settlement and query_spend call the canonical
functions in schema.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import re
import sqlite3
from typing import Optional

from schema import (
    _SETTLEMENT_EXCLUDED_SQL as _EXCLUDED_SQL,
    compute_settlement,
    get_settlement_data,
)

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _validate_period(period: str) -> Optional[dict]:
    if not _PERIOD_RE.match(period):
        return {"error": f"Invalid period {period!r}. Expected YYYY-MM (e.g. '2026-06')."}
    return None

_SPEND_WHERE = f"""
    t.transaction_type NOT IN ({_EXCLUDED_SQL})
    AND c.type = 'spend'
    AND t.duplicate_status != 'confirmed_duplicate'
    AND substr(t.transaction_date, 1, 7) = :period
"""


def query_spend(
    conn: sqlite3.Connection,
    period: str,
    category: Optional[str] = None,
    owner: Optional[str] = None,
    mode: str = "total",
) -> dict:
    """Aggregate or list spend transactions for a YYYY-MM period."""
    if err := _validate_period(period):
        return err
    params: dict = {"period": period}
    extra = ""

    if category is not None:
        valid = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM categories WHERE type = 'spend' ORDER BY name"
            )
        ]
        matched = next((v for v in valid if v.lower() == category.lower()), None)
        if matched is None:
            return {"error": f"Unknown category {category!r}. Valid: {valid}"}
        category = matched
        extra += " AND c.name = :category"
        params["category"] = category

    if owner is not None:
        valid_owners = [r[0] for r in conn.execute("SELECT display_name FROM users")]
        matched_owner = next((o for o in valid_owners if o.lower() == owner.lower()), None)
        if matched_owner is None:
            return {"error": f"Unknown owner {owner!r}. Valid: {valid_owners}"}
        owner = matched_owner
        extra += " AND u.display_name = :owner"
        params["owner"] = owner

    uncategorized_count = conn.execute(
        f"""SELECT COUNT(*) FROM transactions t
            WHERE t.transaction_type NOT IN ({_EXCLUDED_SQL})
              AND t.category_id IS NULL
              AND t.duplicate_status != 'confirmed_duplicate'
              AND substr(t.transaction_date, 1, 7) = :period""",
        {"period": period},
    ).fetchone()[0]

    total_row = conn.execute(
        f"""SELECT ROUND(SUM(CASE WHEN t.direction='credit' THEN -t.amount ELSE t.amount END), 2),
                   COUNT(*)
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            JOIN users u ON u.id = t.owner_id
            WHERE {_SPEND_WHERE} {extra}""",
        params,
    ).fetchone()

    total = total_row[0] or 0.0
    txn_count = total_row[1]

    credit_row = conn.execute(
        f"""SELECT COUNT(*), ROUND(SUM(t.amount), 2)
            FROM transactions t
            JOIN categories c ON c.id = t.category_id
            JOIN users u ON u.id = t.owner_id
            WHERE {_SPEND_WHERE} {extra}
              AND t.direction = 'credit'""",
        params,
    ).fetchone()

    result: dict = {
        "period": period,
        "total": total,
        "txn_count": txn_count,
        "credit_count": credit_row[0],
        "credit_total": credit_row[1] or 0.0,
        "caveats": {"uncategorized_count_in_period": uncategorized_count},
    }
    if category is not None:
        result["category"] = category
    if owner is not None:
        result["owner"] = owner

    if mode == "list":
        rows = conn.execute(
            f"""SELECT t.id, t.transaction_date AS date, t.merchant_normalized AS merchant,
                       CASE WHEN t.direction='credit' THEN -t.amount ELSE t.amount END AS amount,
                       t.direction, u.display_name AS owner
                FROM transactions t
                JOIN categories c ON c.id = t.category_id
                JOIN users u ON u.id = t.owner_id
                WHERE {_SPEND_WHERE} {extra}
                ORDER BY t.transaction_date
                LIMIT 50""",
            params,
        ).fetchall()
        # txn_count is the unfiltered match count; rows is capped at 50.
        # Surface truncation so the agent never implies a partial list is complete.
        result["returned"] = len(rows)
        result["list_truncated"] = txn_count > len(rows)
        result["transactions"] = [dict(r) for r in rows]

    return result


def get_settlement(conn: sqlite3.Connection, period: str) -> dict:
    """Settlement for a YYYY-MM period. Wraps get_settlement_data + compute_settlement."""
    if err := _validate_period(period):
        return err
    try:
        data = get_settlement_data(conn, period)
    except ValueError as e:
        return {"error": str(e)}
    return compute_settlement(data)


def list_uncategorized(conn: sqlite3.Connection, period: Optional[str] = None) -> dict:
    """List up to 50 uncategorized spend transactions. period is optional (YYYY-MM)."""
    if period is not None and (err := _validate_period(period)):
        return err
    params: dict = {}
    period_filter = ""
    if period is not None:
        period_filter = "AND substr(t.transaction_date, 1, 7) = :period"
        params["period"] = period

    rows = conn.execute(
        f"""SELECT t.id, t.transaction_date AS date, t.merchant_normalized AS merchant,
                   t.amount, u.display_name AS owner, t.source_category_raw
            FROM transactions t
            JOIN users u ON u.id = t.owner_id
            WHERE t.category_id IS NULL
              AND t.transaction_type NOT IN ({_EXCLUDED_SQL})
              AND t.duplicate_status != 'confirmed_duplicate'
              {period_filter}
            ORDER BY t.transaction_date
            LIMIT 50""",
        params,
    ).fetchall()

    total_count = conn.execute(
        f"""SELECT COUNT(*) FROM transactions t
            WHERE t.category_id IS NULL
              AND t.transaction_type NOT IN ({_EXCLUDED_SQL})
              AND t.duplicate_status != 'confirmed_duplicate'
              {period_filter}""",
        params,
    ).fetchone()[0]

    return {
        "period": period,
        "returned": len(rows),
        "total_uncategorized": total_count,
        "transactions": [dict(r) for r in rows],
    }
