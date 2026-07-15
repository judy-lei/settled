"""
Categorization: merchant rules table (override/fallback) + source category
mapping (primary signal where the source provides one).

Precedence (highest wins):
    1. Merchant rules table   — your manual corrections, always win
    2. Source category, mapped to our taxonomy — used when no rule matches
    3. None (category_id NULL) — flagged for review

Merchant rules only predict CATEGORY. Household-vs-personal is deliberately
NOT decided at this stage — it's a split-time question, configured later as
per-category split percentages.

The only automatic exclusion from spend here is "payment" transaction type
— that's not a household judgment, it's not spend at all, just money moving
between your own accounts.

Merchant rules live in the DB (merchant_rules table) so the review UI can
add new ones. Seed rules are user data, not product code — they load from
the local, git-ignored data/seed_config.json ("merchant_rules" key, optional).
A fresh install with no seed rules starts empty and builds rules organically
through review corrections.

See DECISIONS.md for why source-category-mapping was added.
"""

import json
from pathlib import Path

_SEED_CONFIG_PATH = Path(__file__).parent.parent / "data" / "seed_config.json"


def _load_seed_rules() -> list:
    """Optional one-time seed data for merchant_rules, from local config.
    Format in JSON: "merchant_rules": [["PATTERN", "Category"], ...]
    Case-insensitive substring match on merchant_normalized. Newest rule wins."""
    if not _SEED_CONFIG_PATH.exists():
        return []
    with open(_SEED_CONFIG_PATH) as f:
        return [tuple(rule) for rule in json.load(f).get("merchant_rules", [])]


SEED_MERCHANT_RULES = _load_seed_rules()


# ---------------------------------------------------------------------------
# Source category mapping — translates each source's own vocabulary into
# our taxonomy. Used only when no merchant rule matched.
# ---------------------------------------------------------------------------
WEALTHSIMPLE_CATEGORY_MAP = {
    "other food and drink": "Eating Out",
    "restaurants": "Eating Out",
    "coffee": "Eating Out",
    "bars and nightlife": "Eating Out",
    "groceries": "Groceries",
    "subscriptions": "Subscriptions",
    "other shopping": "Shopping",
    "clothing": "Shopping",
    "pets": "Pet",
    "hotels": "Travel",
    "other travel": "Travel",
    "flights": "Travel",
    "services": "Services",
    "beauty": "Personal Care",
    "taxis and rideshares": "Transport",
    "public transit": "Transport",
    "other transportation": "Transport",
    "gas, parking, and tolls": "Transport",
    "medical": "Health",
    "other health": "Health",
    "home and auto": "Home",
    "other bills": "Bills",
    "internet and phone": "Bills",
    "gifts": "Gifts",
    "entertainment": "Entertainment",
    "auto insurance": "Auto",
    "other housing": "Home",
    "kids' activities": "Family",
    "education": "Education",
    "donations": "Donations",
    "other work": None,       # ambiguous — needs review
    "rent": None,             # household vs personal — needs review
    "miscellaneous": None,    # needs review
    "uncategorized": None,    # needs review
}

AMEX_ANNUAL_CATEGORY_MAP = {
    ("merchandise", "retail"): "Shopping",
    ("merchandise", "supermarkets"): "Groceries",
    ("restaurant", "restaurant"): "Eating Out",
    ("other", "entertainment"): "Entertainment",
    ("merchandise", "mail order/telephone"): "Shopping",
    ("merchandise", "merchandise other"): "Shopping",
    ("other", "communications"): "Bills",
    ("travel", "travel related"): "Travel",
    ("merchandise", "gas"): "Transport",
    ("other", "other charges"): None,  # needs review
    ("other", "health care"): "Health",
    ("financial services", "fee services"): "Subscriptions",
    ("travel", "lodging"): "Travel",
    ("merchandise", "auto services"): "Auto",
    ("travel", "travel other"): "Travel",
    ("other", "charities"): "Donations",
}


def apply_merchant_rule(merchant_normalized: str, rules: list):
    """Return category, or None if no rule matches.
    `rules` is a list of (pattern, category), newest first."""
    upper = merchant_normalized.upper()
    for pattern, category in rules:
        if pattern.upper() in upper:
            return category
    return None


def map_wealthsimple_category(source_category: str):
    """Returns mapped category name, None if unmapped or explicitly needs review."""
    if not source_category:
        return None
    return WEALTHSIMPLE_CATEGORY_MAP.get(source_category.strip().lower())


def map_amex_annual_category(category: str, sub_category: str):
    if not category or not sub_category:
        return None
    return AMEX_ANNUAL_CATEGORY_MAP.get((category.strip().lower(), sub_category.strip().lower()))


def categorize(merchant_normalized: str, rules: list, source_category: str = None,
                transaction_type: str = None) -> dict:
    """
    Apply the full precedence: payment short-circuit -> merchant rule ->
    source category -> None.
    `rules` is the current merchant rules list (from schema.get_merchant_rules()).
    `source_category` should already be mapped to our taxonomy by the caller
    (parsers.py) before being passed in here, or None if unavailable.

    Returns dict: category (name string or None), category_source
    """
    if transaction_type == "payment":
        return {"category": "Payment", "category_source": "transaction_type"}

    category = apply_merchant_rule(merchant_normalized, rules)
    if category:
        return {"category": category, "category_source": "merchant_rule"}

    if source_category:
        return {"category": source_category, "category_source": "source_mapped"}

    return {"category": None, "category_source": "none"}
