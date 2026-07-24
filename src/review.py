"""
Review flow — confirm or correct already-categorized transactions.

The write path behind the review screen (app.py is a thin caller). Two actions:

  confirm_reviewed  — "Looks right": mark reviewed, no category change, no trail row.
  apply_correction  — "Change to X": record the correction, update the category,
                      and upsert a merchant rule so future imports follow.

Every correction appends a row to category_changes (the audit trail) capturing
what the category_source WAS before the correction overwrites it to 'user_manual'.
Metrics are read separately by report.get_review_metrics().
"""
from datetime import datetime, timezone

from schema import add_merchant_rule

# The only category_source values a review-eligible row can have: it is already
# categorized (not 'none') and not a payment (not 'transaction_type'). A row
# outside this set reaching a correction is a caller bug — fail loudly rather
# than write a trail row the CHECK constraint would reject anyway.
_CORRECTABLE_SOURCES = ("merchant_rule", "source_mapped", "user_manual")


def _category_id(conn, category_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM categories WHERE name = ?", (category_name,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown category: {category_name!r}")
    return row["id"]


def assign_blank(conn, txn_ids: list[int], category_name: str, commit: bool = True) -> int:
    """Assign a category to blank (uncategorized) transactions.

    The Uncategorized tab's write path. Sets category_id, category_source,
    and review_status. Does NOT touch uncategorized_at_import — that marker
    must stay 1 so blanked_by_rules remains correct after a blank is filled.
    Merchant-rule upsert is the caller's responsibility (caller holds the
    merchant key and may upsert after all ids for that merchant are assigned).

    An already-categorized row is skipped (not an assignment — use
    apply_correction for a category change). Returns the number of rows updated.

    commit=False lets the caller batch this UPDATE with a subsequent
    add_merchant_rule() so both commit atomically on that function's conn.commit().
    """
    if not txn_ids:
        return 0
    cat_id = _category_id(conn, category_name)
    ids = [int(tid) for tid in txn_ids]
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE transactions SET category_id = ?, category_source = 'user_manual', "
        f"review_status = 'reviewed' "
        f"WHERE id IN ({placeholders}) AND category_id IS NULL",
        [cat_id] + ids,
    )
    if commit:
        conn.commit()
    return cur.rowcount


def confirm_reviewed(conn, txn_ids: list[int]) -> int:
    """Mark transactions reviewed without changing their category. No trail row.

    "Looks right" in the review screen. Returns the number of rows touched.
    Stamps no timestamp — reviewed_at is deferred to the settlements rebuild.
    """
    if not txn_ids:
        return 0
    ids = [int(tid) for tid in txn_ids]
    placeholders = ",".join("?" * len(ids))
    # AND category_id IS NOT NULL: "Looks right" only applies to categorized
    # rows. Guarding here keeps an uncategorized row from being marked reviewed
    # and then counted as 'confirmed correct' in get_review_metrics. rowcount is
    # the number actually updated, so the caller sees a real no-op as 0.
    cur = conn.execute(
        f"UPDATE transactions SET review_status = 'reviewed' "
        f"WHERE id IN ({placeholders}) AND category_id IS NOT NULL",
        ids,
    )
    conn.commit()
    return cur.rowcount


def apply_correction(conn, txn_ids: list[int], new_category_name: str) -> int:
    """Recategorize transactions and record the correction trail.

    For each transaction: capture its current category and category_source,
    append a category_changes row, then update category_id ->
    new_category_name, category_source -> 'user_manual', review_status ->
    'reviewed'. The trail row + transaction update commit together; the
    merchant-rule upsert runs afterward (it commits internally and writes
    seed_config), so a rule-write failure leaves the trail and category
    consistent and the rule retryable.

    A transaction already in new_category_name is skipped (not a correction —
    inserting a trail row would violate the old != new CHECK). An uncategorized
    (NULL) row raises: initial categorization is the Uncategorized tab's job.

    Returns the number of transactions actually corrected.
    """
    new_cat = _category_id(conn, new_category_name)
    now = datetime.now(timezone.utc).isoformat()
    changed_merchants: set[str] = set()
    corrected = 0

    try:
        for tid in txn_ids:
            tid = int(tid)
            row = conn.execute(
                "SELECT category_id, category_source, merchant_normalized "
                "FROM transactions WHERE id = ?",
                (tid,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Transaction {tid} not found")

            old_cat = row["category_id"]
            old_source = row["category_source"]
            if old_cat is None:
                raise ValueError(
                    f"Transaction {tid} is uncategorized; categorize it in the "
                    "Uncategorized tab, not the review flow."
                )
            if old_source not in _CORRECTABLE_SOURCES:
                raise ValueError(
                    f"Transaction {tid} has category_source {old_source!r}; only "
                    "already-categorized non-payment rows are correctable here."
                )
            if old_cat == new_cat:
                continue  # not a correction

            conn.execute(
                "INSERT INTO category_changes "
                "(transaction_id, old_category_id, new_category_id, "
                " old_category_source, changed_at) VALUES (?, ?, ?, ?, ?)",
                (tid, old_cat, new_cat, old_source, now),
            )
            conn.execute(
                "UPDATE transactions "
                "SET category_id = ?, category_source = 'user_manual', "
                "    review_status = 'reviewed' WHERE id = ?",
                (new_cat, tid),
            )
            changed_merchants.add(row["merchant_normalized"])
            corrected += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # Future imports of these merchants follow the correction. Each call commits
    # internally and persists to seed_config; kept out of the trail transaction
    # so a failure here does not roll back the recorded correction.
    for merchant in changed_merchants:
        add_merchant_rule(conn, merchant, new_category_name)

    return corrected
