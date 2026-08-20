"""
Review UI — categorize uncategorized transactions (bulk, by merchant),
resolve suspected duplicates (side-by-side compare).

Run: .venv/bin/streamlit run src/app.py
"""

import html
import streamlit as st
from display import format_account
from schema import (
    get_conn, add_merchant_rule, seed_category_splits,
    get_settlement_data, compute_settlement, settlement_checks,
)
from review import assign_blank, confirm_reviewed, apply_correction
from report import get_review_metrics, SPEND_PREDICATE

st.set_page_config(page_title="Household Spend Review", layout="centered")

_ADD_NEW = "+ Add new category…"


def get_categories(conn) -> list[str]:
    """All categories from the categories table, excluding system values
    not offered as review choices. Merges in names added this session
    before they're committed to the DB."""
    if "custom_categories" not in st.session_state:
        st.session_state["custom_categories"] = []
    db_cats = [r[0] for r in conn.execute("""
        SELECT name FROM categories
        WHERE name != 'Payment'
        ORDER BY name
    """)]
    return sorted(set(db_cats) | set(st.session_state["custom_categories"]))


def inject_style():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap');

    :root {
        --bg-deep: #140f1c;
        --bg-mid: #1e1730;
        --bg-surface: rgba(255,255,255,.045);
        --accent: #E8AE60;
        --accent-dim: rgba(232,174,96,.14);
        --accent-border: rgba(232,174,96,.30);
        --text-primary: #EDE5DA;
        --text-secondary: #a099b8;
        --text-muted: #7a6e8a;
        --text-label: #4a4060;
        --border: rgba(255,255,255,.06);
    }

    .stApp {
        background: linear-gradient(160deg, var(--bg-deep) 0%, var(--bg-mid) 100%);
        background-attachment: fixed;
    }

    html, body, [class*="css"], .stMarkdown, p, span, div, label {
        font-family: 'Source Sans 3', system-ui, sans-serif;
    }

    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em;
        color: var(--text-primary) !important;
    }
    h1 { font-weight: 700 !important; }

    /* Active tab in accent + amber underline */
    .stTabs [data-baseweb="tab-list"] { gap: 28px; border-bottom: 1px solid var(--border); }
    .stTabs [data-baseweb="tab"] {
        font-family: 'Source Sans 3', sans-serif;
        letter-spacing: 0.02em;
    }
    .stTabs [aria-selected="true"] { color: var(--accent) !important; }
    .stTabs [data-baseweb="tab-highlight"] { background-color: var(--accent) !important; }

    .stButton button {
        font-family: 'Source Sans 3', sans-serif;
        text-transform: uppercase;
        letter-spacing: 0.10em;
        font-size: 0.70rem;
        font-weight: 600;
        border-radius: 12px;
        border: 1px solid var(--accent-border);
        background: var(--accent-dim);
        color: var(--accent);
    }
    .stButton button:hover {
        border-color: var(--accent);
        background: rgba(232,174,96,.20);
        color: var(--accent);
    }

    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
        display: flex;
        align-items: center;
    }

    /* Cards: warm surface, soft glow, no hard border */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--bg-surface);
        border: 1px solid var(--border);
        border-radius: 20px;
        box-shadow: 0 4px 24px rgba(0,0,0,.30);
        margin-bottom: 0.85rem;
        padding: 2px;
    }

    [data-testid="stMarkdownContainer"] { width: 100%; }
    .merchant-header {
        display: flex;
        align-items: baseline;
        gap: 14px;
        width: 100%;
    }
    .merchant-header .merchant-name { flex: 1; }
    .merchant-stats {
        display: flex;
        align-items: baseline;
        gap: 10px;
    }

    /* Inputs don't need the full row width */
    [data-testid="stSelectbox"], [data-testid="stTextInput"] { max-width: 340px; }
    .merchant-name {
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--text-primary);
        letter-spacing: 0.01em;
    }
    .merchant-amount {
        font-size: 1rem;
        font-weight: 600;
        color: var(--accent);
        font-family: 'JetBrains Mono', monospace;
    }
    .merchant-meta {
        font-size: 0.85rem;
        color: var(--text-muted);
        font-family: 'JetBrains Mono', monospace;
    }
    /* Uppercase section labels */
    .section-label {
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.68rem;
        font-weight: 600;
        color: var(--text-label);
    }

    /* Tighter page top; hide Streamlit chrome */
    .block-container { padding-top: 2rem; }
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


def categorize_tab(conn, period):
    st.subheader("Uncategorized — grouped by merchant")

    rows = conn.execute("""
        SELECT t.merchant_normalized,
               COUNT(*) AS txns,
               ROUND(SUM(CASE WHEN t.direction = 'credit' THEN -t.amount ELSE t.amount END), 2) AS total,
               GROUP_CONCAT(t.id) AS ids
        FROM transactions t
        WHERE t.category_id IS NULL
          AND substr(t.transaction_date, 1, 7) = :period
        GROUP BY t.merchant_normalized
        ORDER BY ABS(total) DESC
    """, {"period": period}).fetchall()

    if not rows:
        st.success("Nothing uncategorized.")
        return

    total_txns = sum(r["txns"] for r in rows)
    st.caption(f"{len(rows)} merchant(s), {total_txns} transaction(s) remaining")

    if "pending_categories" not in st.session_state:
        st.session_state["pending_categories"] = {}
    pending = st.session_state["pending_categories"]

    # Count by reading selectbox state directly — the loop below hasn't run yet
    # so pending dict is one render behind; session_state is always current.
    n_pending = sum(
        1 for r in rows
        if st.session_state.get(f"cat_{r['merchant_normalized']}", "") not in ("", _ADD_NEW)
    )
    if st.button(f"Apply All ({n_pending} pending)", disabled=(n_pending == 0), type="primary"):
        for merchant, entry in pending.items():
            cat_name = entry["category"]
            # Ensure category exists (handles names added via "+ Add new category…").
            # seed_category_splits commits, so assign_blank reads a committed category.
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, type) VALUES (?, 'spend')",
                (cat_name,)
            )
            seed_category_splits(conn)
            assign_blank(conn, entry["ids"], cat_name, commit=False)
            add_merchant_rule(conn, merchant, cat_name)
        st.session_state["pending_categories"] = {}
        st.rerun()

    st.divider()

    for r in rows:
        ids = [int(x) for x in r["ids"].split(",")]
        merchant = r["merchant_normalized"]

        details = conn.execute(f"""
            SELECT t.id, t.transaction_date, t.amount, t.direction, a.account_name
            FROM transactions t JOIN accounts a ON t.account_id = a.id
            WHERE t.id IN ({','.join('?' * len(ids))})
            ORDER BY t.transaction_date
        """, ids).fetchall()

        with st.container(border=True):
            st.markdown(
                f'<div class="merchant-header">'
                f'<span class="merchant-name">{html.escape(merchant)}</span>'
                f'<span class="merchant-stats">'
                f'<span class="merchant-meta">{r["txns"]} transaction(s)</span>'
                f'<span class="merchant-amount">${r["total"]:.2f}</span>'
                f'</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Always show transaction rows. Multi-txn gets an expander with checkboxes;
            # single-txn renders inline (nothing to select).
            selected_ids = list(ids)
            if r["txns"] > 1:
                with st.expander(f"Select which of {r['txns']} transaction(s) to update"):
                    selected_ids = []
                    for d in details:
                        cols = st.columns([0.2, 6], gap="small")
                        checked = cols[0].checkbox("Select", value=True, key=f"sel_{d['id']}",
                                                   label_visibility="collapsed")
                        cols[1].markdown(
                            f"<span class='merchant-meta'>{d['transaction_date']}&emsp;·&emsp;"
                            f"{d['account_name']}&emsp;·&emsp;${d['amount']:.2f} ({d['direction']})</span>",
                            unsafe_allow_html=True
                        )
                        if checked:
                            selected_ids.append(d["id"])
            else:
                d = details[0]
                st.markdown(
                    f"<span class='merchant-meta'>{d['transaction_date']}&emsp;·&emsp;"
                    f"{d['account_name']}&emsp;·&emsp;${d['amount']:.2f} ({d['direction']})</span>",
                    unsafe_allow_html=True
                )

            cat_cols = st.columns([4, 1])
            category = cat_cols[0].selectbox(
                "Category", [""] + get_categories(conn) + [_ADD_NEW],
                key=f"cat_{merchant}", label_visibility="collapsed",
                format_func=lambda x: "Select category…" if x == "" else x,
            )

            if category == _ADD_NEW:
                def _commit_new_cat(merchant=merchant):
                    val = st.session_state.get(f"new_cat_{merchant}", "").strip()
                    if val and val not in st.session_state["custom_categories"]:
                        st.session_state["custom_categories"].append(val)
                    if val:
                        st.session_state[f"cat_{merchant}"] = val

                add_cols = st.columns([4, 1])
                add_cols[0].text_input(
                    "New category name", key=f"new_cat_{merchant}",
                    placeholder="e.g. Hobbies", label_visibility="collapsed",
                    on_change=_commit_new_cat
                )
                if add_cols[1].button("Add", key=f"add_cat_{merchant}"):
                    _commit_new_cat()
                category = ""

            # Read directly from session_state so the pending dict stays accurate
            # across reruns triggered by other merchants' widgets.
            effective_cat = st.session_state.get(f"cat_{merchant}", "")
            if effective_cat and effective_cat != _ADD_NEW:
                pending[merchant] = {"ids": selected_ids, "category": effective_cat}
            elif merchant in pending:
                del pending[merchant]


def duplicates_tab(conn, period):
    st.subheader("Suspected duplicates")

    rows = conn.execute("""
        SELECT t.id, t.transaction_date, t.amount, t.merchant_normalized,
               t.duplicate_of_id, u.display_name AS owner_name,
               a.institution, a.account_name,
               o.transaction_date AS orig_transaction_date,
               o.amount           AS orig_amount,
               o.merchant_normalized AS orig_merchant_normalized
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        JOIN users u ON a.owner_id = u.id
        LEFT JOIN transactions o ON o.id = t.duplicate_of_id
        WHERE t.duplicate_status = 'suspected_duplicate'
          AND substr(t.transaction_date, 1, 7) = :period
        ORDER BY t.merchant_normalized, t.transaction_date
    """, {"period": period}).fetchall()

    if not rows:
        st.success("No suspected duplicates.")
        return

    st.caption(f"{len(rows)} suspected duplicate(s) — flagged conservatively, never auto-resolved")

    # Group by merchant so clusters (e.g. 5 Anthropic charges) get a bulk option
    by_merchant = {}
    for r in rows:
        by_merchant.setdefault(r["merchant_normalized"], []).append(r)

    for merchant, group in by_merchant.items():
        st.markdown(f'<div class="merchant-name">{html.escape(merchant)}</div>', unsafe_allow_html=True)
        if len(group) > 1:
            if st.button(f"Dismiss all {len(group)} as not duplicates", key=f"dismiss_all_{merchant}"):
                ids = [g["id"] for g in group]
                conn.executemany(
                    "UPDATE transactions SET duplicate_status = 'dismissed' WHERE id = ?",
                    [(i,) for i in ids]
                )
                conn.commit()
                st.rerun()

        for r in group:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    st.markdown("**Original**")
                    if r["orig_transaction_date"] is None:
                        st.warning("Original transaction not found.")
                    else:
                        st.write(f"${r['orig_amount']:.2f}")
                        st.markdown(
                            f"<span class='merchant-meta'>{r['orig_transaction_date']}</span>",
                            unsafe_allow_html=True,
                        )
                with c2:
                    st.markdown("**Suspected duplicate**")
                    st.write(f"${r['amount']:.2f}")
                    st.markdown(f"<span class='merchant-meta'>{r['transaction_date']} · "
                                f"{format_account(r)}</span>",
                                unsafe_allow_html=True)
                with c3:
                    if st.button("Confirm", key=f"confirm_{r['id']}"):
                        conn.execute("""
                            UPDATE transactions
                            SET duplicate_status = 'confirmed_duplicate'
                            WHERE id = ?
                        """, (r["id"],))
                        conn.commit()
                        st.rerun()
                    if st.button("Not a duplicate", key=f"dismiss_{r['id']}"):
                        conn.execute(
                            "UPDATE transactions SET duplicate_status = 'dismissed' WHERE id = ?",
                            (r["id"],)
                        )
                        conn.commit()
                        st.rerun()


def review_tab(conn, period):
    st.subheader("Review — categorized spend")

    m = get_review_metrics(conn, period)

    # The list below shows only categorized rows; still-blank rows live on the
    # Uncategorized tab and can't be reviewed here. get_review_metrics defines the
    # categorized (reviewable) count so "Reviewed X / Y" is reachable and matches
    # the rows shown; the blanks are pointed at separately below.
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Transactions", m["categorized"])
    c2.metric("Reviewed", f"{m['reviewed']} / {m['categorized']}")
    c3.metric("Corrected", m["corrected"])
    mr = m["miscategorization_rate"]
    c4.metric("Miscat rate", f"{mr:.0%}" if isinstance(mr, float) else mr)

    if m["uncategorized"] > 0:
        st.caption(
            f"{m['uncategorized']} transaction(s) still uncategorized this month — "
            f"categorize them on the Uncategorized tab"
        )
    # Durable rule-quality metric. Suppress it only when it would exactly restate
    # the line above (fresh month, nothing filled yet → blanked_by_rules ==
    # uncategorized); once any blank is filled the two diverge and both inform.
    if m["blanked_by_rules"] > 0 and m["blanked_by_rules"] != m["uncategorized"]:
        blr = m["blanked_by_rules_rate"]
        rate_str = f"{blr:.0%}" if isinstance(blr, float) else blr
        st.caption(
            f"Rules left {m['blanked_by_rules']} transaction(s) blank at import ({rate_str})"
        )

    st.divider()

    rows = conn.execute(f"""
        SELECT t.id, t.merchant_normalized, t.transaction_date, t.amount, t.direction,
               t.review_status, c.name as category_name,
               (SELECT c2.name
                FROM category_changes cc
                JOIN categories c2 ON c2.id = cc.old_category_id
                WHERE cc.transaction_id = t.id
                ORDER BY cc.id ASC LIMIT 1) as was_category
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE {SPEND_PREDICATE}
          AND t.category_id IS NOT NULL
          AND substr(t.transaction_date, 1, 7) = :period
        ORDER BY c.name, t.transaction_date
    """, {"period": period}).fetchall()

    if not rows:
        st.info("No categorized transactions for this month.")
        return

    by_cat: dict = {}
    for r in rows:
        by_cat.setdefault(r["category_name"], []).append(r)

    cats = get_categories(conn)

    for cat_name, txns in by_cat.items():
        n_reviewed = sum(1 for t in txns if t["review_status"] == "reviewed")
        all_done = n_reviewed == len(txns)

        with st.expander(
            f"**{cat_name}** — {len(txns)} txn(s), {n_reviewed}/{len(txns)} reviewed",
            expanded=not all_done,
        ):
            for t in txns:
                tid = t["id"]
                signed = -t["amount"] if t["direction"] == "credit" else t["amount"]
                was_html = (
                    f'&emsp;<span class="merchant-meta">was: {html.escape(t["was_category"])}</span>'
                    if t["was_category"] else ""
                )
                status_badge = (
                    '<span style="color:#5EEAD4;margin-right:6px">✓</span>'
                    if t["review_status"] == "reviewed" else
                    '<span style="color:#A8B4C8;margin-right:6px">·</span>'
                )

                with st.container(border=True):
                    info_col, action_col = st.columns([5, 3])
                    info_col.markdown(
                        f'<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">'
                        f'{status_badge}'
                        f'<span class="merchant-meta">{t["transaction_date"]}</span>'
                        f'&emsp;<span class="merchant-name">{html.escape(t["merchant_normalized"])}</span>'
                        f'&emsp;<span class="merchant-amount">${signed:.2f}</span>'
                        f'{was_html}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    b1, b2, b3 = action_col.columns([1, 3, 1])
                    if b1.button("✓", key=f"ok_{tid}", help="Looks right — mark reviewed"):
                        confirm_reviewed(conn, [tid])
                        st.rerun()

                    new_cat = b2.selectbox(
                        "Change to",
                        [""] + [c for c in cats if c != cat_name],
                        key=f"chg_{tid}",
                        label_visibility="collapsed",
                        format_func=lambda x: "Change to…" if x == "" else x,
                    )
                    if b3.button("→", key=f"go_{tid}", disabled=not new_cat,
                                 help="Apply change" if new_cat else "Select a category first"):
                        apply_correction(conn, [tid], new_cat)
                        st.session_state.pop(f"chg_{tid}", None)
                        st.rerun()


_PERSON_GRADIENTS = [
    ("#C17B2F", "#E8AE60"),  # Person A — warm amber
    ("#6B5AAF", "#9B8BE0"),  # Person B — plum
]


def _avatar(name: str, idx: int) -> str:
    start, end = _PERSON_GRADIENTS[idx % len(_PERSON_GRADIENTS)]
    initial = html.escape(name[:1].upper())
    return (
        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
        f'width:26px;height:26px;border-radius:50%;font-size:0.8rem;font-weight:600;'
        f'color:#140f1c;background:linear-gradient(135deg,{start},{end});'
        f'margin-right:8px;vertical-align:middle">{initial}</span>'
    )


def settlement_tab(conn, period):
    """Read-only settlement view. Renders get_settlement_data + compute_settlement —
    the same deterministic tool the agent calls. No arithmetic in the view; no write
    actions (mark-settled / lifecycle are future work, not shown here)."""
    try:
        result = compute_settlement(get_settlement_data(conn, period))
    except ValueError as e:
        st.error(f"Cannot settle {period}: {e}")
        return

    s = result["settlement"]
    users = result["users"]

    # Hero — who owes whom. settlement is None when the period is square
    # (nobody owes anyone) or has no qualifying spend.
    if s is None:
        st.markdown(
            '<div style="text-align:center;padding:28px 0 8px">'
            '<div class="section-label">Settlement</div>'
            '<div style="font-family:\'Playfair Display\',serif;font-size:1.6rem;'
            'color:var(--text-primary);margin-top:10px">Settled — nobody owes anyone.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        frm = s["from_user"]["display_name"]
        to = s["to_user"]["display_name"]
        st.markdown(
            f'<div style="text-align:center;padding:24px 0 4px">'
            f'<div class="section-label">Settlement · {period}</div>'
            f'<div style="font-family:\'Playfair Display\',serif;font-size:1.5rem;'
            f'color:var(--text-secondary);margin-top:12px">'
            f'{html.escape(frm)} owes {html.escape(to)}</div>'
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:3.4rem;'
            f'font-weight:600;color:var(--accent);margin-top:6px">'
            f'${s["amount"]:.2f}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Uncategorized caveat — loud, since it means the number is incomplete
    unc = result.get("uncategorized_count", 0)
    if unc:
        st.markdown(
            f'<div style="background:var(--accent-dim);border:1px solid var(--accent-border);'
            f'border-radius:12px;padding:10px 14px;margin:10px 0;color:var(--text-primary);'
            f'font-size:0.9rem">⚠ {unc} transaction(s) in {period} are uncategorized and '
            f'<b>excluded</b> from this settlement — the number is not yet complete.</div>',
            unsafe_allow_html=True,
        )

    # Per-person breakdown
    cols = st.columns(len(users))
    for idx, (col, u) in enumerate(zip(cols, users)):
        bal = u["balance"]
        bal_color = "var(--accent)" if bal > 0 else "var(--text-secondary)"
        bal_label = "is owed" if bal > 0 else ("owes" if bal < 0 else "settled")
        col.markdown(
            f'<div style="background:var(--bg-surface);border:1px solid var(--border);'
            f'border-radius:20px;padding:16px 18px;box-shadow:0 4px 24px rgba(0,0,0,.30)">'
            f'<div style="margin-bottom:10px">{_avatar(u["display_name"], idx)}'
            f'<span style="font-weight:600;color:var(--text-primary)">'
            f'{html.escape(u["display_name"])}</span></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:0.85rem;'
            f'color:var(--text-muted);margin:4px 0">'
            f'<span>Paid</span><span style="font-family:\'JetBrains Mono\',monospace;'
            f'color:var(--text-primary)">${u["paid"]:.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:0.85rem;'
            f'color:var(--text-muted);margin:4px 0">'
            f'<span>Fair share</span><span style="font-family:\'JetBrains Mono\',monospace;'
            f'color:var(--text-primary)">${u["fair_share"]:.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:0.85rem;'
            f'margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">'
            f'<span style="color:var(--text-muted)">{bal_label}</span>'
            f'<span style="font-family:\'JetBrains Mono\',monospace;color:{bal_color}">'
            f'${abs(bal):.2f}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Reconciliation checks — consistency of the tool's own output (not new math)
    total = result["total_spend"]
    checks = settlement_checks(result)
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;font-size:0.8rem;'
        f'color:var(--text-muted);padding:3px 0">'
        f'<span>{"✓" if ok else "✗"} {label}</span>'
        f'<span style="font-family:\'JetBrains Mono\',monospace;'
        f'color:{"var(--accent)" if ok else "#e07a7a"}">{"pass" if ok else "FAIL"}</span></div>'
        for label, ok in checks
    )
    st.markdown(
        f'<div style="margin-top:18px"><div class="section-label" '
        f'style="margin-bottom:6px">Reconciliation · ${total:.2f} categorized spend'
        f'</div>{rows}</div>',
        unsafe_allow_html=True,
    )


def main():
    inject_style()
    conn = get_conn()  # fresh connection per script run — Streamlit can rerun on a
                        # different thread, and SQLite connections are thread-bound
    st.title("Household Spend — Review")

    # One period context for the whole app — every tab views the same month.
    periods = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(transaction_date, 1, 7) AS p "
        "FROM transactions ORDER BY p DESC"
    )]
    if not periods:
        st.info("No transactions imported yet.")
        return
    period = st.selectbox("Period", periods, index=0, key="global_period")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Uncategorized", "Suspected duplicates", "Review", "Settlement"]
    )
    with tab1:
        categorize_tab(conn, period)
    with tab2:
        duplicates_tab(conn, period)
    with tab3:
        review_tab(conn, period)
    with tab4:
        settlement_tab(conn, period)


if __name__ == "__main__":
    main()
