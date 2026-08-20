"""Shared display helpers.

Small formatting functions used by multiple output surfaces (CLI reports,
Streamlit views). One source of truth so a display change lands once,
not in three separate f-strings.
"""


def format_account(row) -> str:
    """Owner + account label. Expects owner_name, institution, account_name
    columns on the row (e.g. from a JOIN to users + accounts)."""
    return f"{row['owner_name']} · {row['institution']} {row['account_name']}"
