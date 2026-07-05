"""
One parser per source format. Each returns a DataFrame with this common shape:

    merchant_raw, merchant_normalized, transaction_date, posted_date,
    amount (always positive), currency, direction (debit|credit),
    transaction_type, source_category_mapped (or None)

Raw source fields are preserved in merchant_raw alongside the normalized
version — nothing is discarded before normalization, so every transformation
stays auditable against the original.
"""

import re
import pandas as pd
from pathlib import Path

from categories import map_wealthsimple_category, map_amex_annual_category


def _normalize_merchant(s: pd.Series) -> pd.Series:
    return s.str.replace(r"\s{2,}", " ", regex=True).str.strip()


def parse_amex_monthly(filepath: Path) -> pd.DataFrame:
    """Amex Cobalt monthly export: Date,Date Processed,Description,Amount,..."""
    df = pd.read_csv(filepath, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    out["merchant_raw"] = df["Description"].str.strip()
    out["merchant_normalized"] = _normalize_merchant(out["merchant_raw"])
    out["transaction_date"] = pd.to_datetime(df["Date"].str.strip(), format="%d %b %Y")
    out["posted_date"] = pd.to_datetime(df["Date Processed"].str.strip(), format="%d %b %Y")

    raw_amount = pd.to_numeric(df["Amount"].str.strip(), errors="coerce")
    out["amount"] = raw_amount.abs()
    out["direction"] = raw_amount.apply(lambda x: "credit" if x < 0 else "debit")
    out["currency"] = "CAD"

    is_payment = out["merchant_raw"].str.contains("PAYMENT RECEIVED", case=False, na=False)
    out["transaction_type"] = is_payment.map({True: "payment", False: "purchase"})
    out["source_category_mapped"] = None

    return out


def parse_amex_annual(filepath: Path) -> pd.DataFrame:
    """Amex Cobalt annual export: Category,Card Member,Account Number,Sub-Category,
    Date,Month-Billed,Transaction,Charges $,Credits $"""
    df = pd.read_csv(filepath, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    out["merchant_raw"] = df["Transaction"].str.strip()
    out["merchant_normalized"] = _normalize_merchant(out["merchant_raw"])
    out["transaction_date"] = pd.to_datetime(df["Date"].str.strip(), format="%d/%m/%Y")
    out["posted_date"] = pd.NaT

    charges = pd.to_numeric(df["Charges $"].str.strip(), errors="coerce").fillna(0)
    credits = pd.to_numeric(df["Credits $"].str.strip(), errors="coerce").fillna(0)
    out["amount"] = (charges - credits).abs()
    out["direction"] = (charges - credits).apply(lambda x: "credit" if x < 0 else "debit")
    out["currency"] = "CAD"

    is_payment = out["merchant_raw"].str.contains("PAYMENT RECEIVED|AUTOPAY", case=False, na=False)
    out["transaction_type"] = is_payment.map({True: "payment", False: "purchase"})

    out["source_category_mapped"] = [
        map_amex_annual_category(cat, sub)
        for cat, sub in zip(df["Category"], df["Sub-Category"])
    ]

    return out


def parse_ws_visa(filepath: Path) -> pd.DataFrame:
    """Wealthsimple Visa export: transaction_date,transaction_type,status,
    merchant,amount,currency,notes,category"""
    df = pd.read_csv(filepath, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    out["merchant_raw"] = df["merchant"].fillna("").str.strip()
    out["merchant_normalized"] = _normalize_merchant(out["merchant_raw"])
    out["transaction_date"] = pd.to_datetime(df["transaction_date"].str.strip())
    out["posted_date"] = pd.NaT

    raw_amount = pd.to_numeric(df["amount"].str.strip(), errors="coerce")
    out["amount"] = raw_amount.abs()
    out["direction"] = raw_amount.apply(lambda x: "credit" if x >= 0 else "debit")
    out["currency"] = df["currency"].str.strip()

    out["transaction_type"] = df["transaction_type"].str.strip().str.lower()
    out["source_category_mapped"] = df["category"].apply(map_wealthsimple_category)

    # Payments have no merchant — distinct transaction_type already marks them
    return out


def parse_ws_chequing_clean(filepath: Path) -> pd.DataFrame:
    """Pre-cleaned chequing CSV: date,merchant,transaction_type,amount,direction,currency"""
    df = pd.read_csv(filepath, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    out["merchant_raw"] = df["merchant"].str.strip()
    out["merchant_normalized"] = _normalize_merchant(out["merchant_raw"])
    out["transaction_date"] = pd.to_datetime(df["date"].str.strip(), format="%B %d, %Y")
    out["posted_date"] = pd.NaT
    out["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    out["direction"] = df["direction"].str.strip()
    out["currency"] = df["currency"].str.strip()
    out["transaction_type"] = df["transaction_type"].str.strip().str.lower()
    out["source_category_mapped"] = None

    return out


PARSERS = {
    "amex_monthly": parse_amex_monthly,
    "amex_annual": parse_amex_annual,
    "ws_visa": parse_ws_visa,
    "ws_chequing_clean": parse_ws_chequing_clean,
}
