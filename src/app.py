"""
Review UI — categorize uncategorized transactions (bulk, by merchant),
resolve suspected duplicates (side-by-side compare).

Run: .venv/bin/streamlit run src/app.py
"""

import html
import streamlit as st
from schema import get_conn, add_merchant_rule, seed_category_splits

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
        WHERE name NOT IN ('Payment')
        ORDER BY name
    """)]
    return sorted(set(db_cats) | set(st.session_state["custom_categories"]))


def inject_style():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=JetBrains+Mono:wght@500&display=swap');

    .stApp {
        background: linear-gradient(180deg, #05070d 0%, #0A0F1C 35%, #0f1b2e 100%);
    }

    h1 {
        font-family: 'Playfair Display', Georgia, serif !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em;
    }

    .stButton button {
        font-family: 'JetBrains Mono', monospace;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-size: 0.75rem;
        border-radius: 8px;
    }

    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
        display: flex;
        align-items: center;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        background: rgba(20, 30, 51, 0.55);
        border: 1px solid rgba(94, 234, 212, 0.18);
        border-left: 3px solid rgba(94, 234, 212, 0.6);
        border-radius: 14px;
        margin-bottom: 0.75rem;
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
        font-weight: 700;
        color: #F0F4FF;
        letter-spacing: 0.01em;
    }
    .merchant-amount {
        font-size: 1rem;
        font-weight: 700;
        color: #5EEAD4;
        font-family: 'JetBrains Mono', monospace;
    }
    .merchant-meta {
        font-size: 0.85rem;
        color: #A8B4C8;
    }

    /* Tighter page top; hide Streamlit chrome */
    .block-container { padding-top: 2rem; }
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)


def categorize_tab(conn):
    st.subheader("Uncategorized — grouped by merchant")

    rows = conn.execute("""
        SELECT t.merchant_normalized,
               COUNT(*) AS txns,
               ROUND(SUM(CASE WHEN t.direction = 'credit' THEN -t.amount ELSE t.amount END), 2) AS total,
               GROUP_CONCAT(t.id) AS ids
        FROM transactions t
        WHERE t.category_id IS NULL
        GROUP BY t.merchant_normalized
        ORDER BY ABS(total) DESC
    """).fetchall()

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
            # Ensure category exists (handles names added via "+ Add new category…")
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, type) VALUES (?, 'spend')",
                (cat_name,)
            )
            seed_category_splits(conn)
            cat_id = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (cat_name,)
            ).fetchone()["id"]
            conn.executemany("""
                UPDATE transactions
                SET category_id = ?, category_source = 'user_manual',
                    review_status = 'reviewed'
                WHERE id = ?
            """, [(cat_id, tid) for tid in entry["ids"]])
            add_merchant_rule(conn, merchant, cat_name)
        conn.commit()
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


def duplicates_tab(conn):
    st.subheader("Suspected duplicates")

    rows = conn.execute("""
        SELECT t.id, t.transaction_date, t.amount, t.merchant_normalized,
               t.duplicate_of_id, a.institution, a.account_name
        FROM transactions t JOIN accounts a ON t.account_id = a.id
        WHERE t.duplicate_status = 'suspected_duplicate'
        ORDER BY t.merchant_normalized, t.transaction_date
    """).fetchall()

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
            orig = conn.execute(
                "SELECT transaction_date, amount, merchant_normalized FROM transactions WHERE id = ?",
                (r["duplicate_of_id"],)
            ).fetchone()

            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    st.markdown("**Original**")
                    if orig is None:
                        st.warning("Original transaction not found.")
                    else:
                        st.write(f"${orig['amount']:.2f}")
                        st.markdown(f"<span class='merchant-meta'>{orig['transaction_date']}</span>",
                                    unsafe_allow_html=True)
                with c2:
                    st.markdown("**Suspected duplicate**")
                    st.write(f"${r['amount']:.2f}")
                    st.markdown(f"<span class='merchant-meta'>{r['transaction_date']} · "
                                f"{r['institution']} {r['account_name']}</span>", unsafe_allow_html=True)
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


def main():
    inject_style()
    conn = get_conn()  # fresh connection per script run — Streamlit can rerun on a
                        # different thread, and SQLite connections are thread-bound
    st.title("Household Spend — Review")

    tab1, tab2 = st.tabs(["Uncategorized", "Suspected duplicates"])
    with tab1:
        categorize_tab(conn)
    with tab2:
        duplicates_tab(conn)


if __name__ == "__main__":
    main()
