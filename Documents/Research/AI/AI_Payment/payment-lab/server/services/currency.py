"""
Currency Service - Handles currency formatting and conversion.

This is where localization gets interesting immediately:
- $1,234.56 (en-US) vs 1.234,56 € (de-DE) vs ¥1,235 (ja-JP)
- Some currencies have no decimal places (JPY, KRW)
- Symbol placement varies: $100 vs 100€ vs 100 Fr.
- Thousands separator varies: comma vs period vs space

We use Babel for locale-aware formatting. In Phase 3, an AI agent
will audit these for correctness and suggest fixes.
"""

from babel.numbers import format_currency, get_currency_precision


# Zero-decimal currencies (Stripe expects whole units, not cents)
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW",
                "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF",
                "XOF", "XPF"}


def format_price(amount_minor, currency, locale="en_US"):
    """
    Format a price for display.
    
    Args:
        amount_minor: Amount in smallest currency unit (cents for USD, yen for JPY)
        currency: ISO 4217 currency code (e.g., 'usd', 'jpy')
        locale: Babel locale string (e.g., 'en_US', 'fr_CA', 'ja_JP')
    
    Returns:
        Formatted price string (e.g., '$49.99', '¥5,000', '49,99 €')
    """
    currency_upper = currency.upper()

    # Convert minor units to major units
    if currency_upper in ZERO_DECIMAL:
        amount = amount_minor  # Already in major units
    else:
        amount = amount_minor / 100

    try:
        return format_currency(amount, currency_upper, locale=locale)
    except Exception:
        # Fallback: basic formatting
        if currency_upper in ZERO_DECIMAL:
            return f"{currency_upper} {amount_minor:,}"
        return f"{currency_upper} {amount_minor / 100:,.2f}"


def get_precision(currency):
    """Get decimal precision for a currency (0 for JPY, 2 for USD, etc.)"""
    try:
        return get_currency_precision(currency.upper())
    except Exception:
        return 0 if currency.upper() in ZERO_DECIMAL else 2


def convert_amount(amount_minor, from_currency, to_currency):
    """
    Convert between currencies. Input is in minor units of from_currency
    (e.g., cents for USD). Output is in minor units of to_currency
    (e.g., whole yen for JPY, cents for EUR).
    
    In production, this would call a rates API (e.g., Open Exchange Rates).
    For now, uses mock rates for testing.
    
    TODO (Phase 2): Integrate real exchange rates
    TODO (Phase 3): AI agent to flag suspicious conversion patterns
    """
    # Mock rates: how many units of currency X per 1 USD
    mock_rates = {
        "usd": 1.0,
        "cad": 1.38,
        "eur": 0.92,
        "gbp": 0.79,
        "jpy": 154,
        "mxn": 17.2,
    }

    from_rate = mock_rates.get(from_currency.lower(), 1.0)
    to_rate = mock_rates.get(to_currency.lower(), 1.0)

    # Step 1: Convert minor units to major units of source currency
    from_upper = from_currency.upper()
    if from_upper in ZERO_DECIMAL:
        major_amount = amount_minor  # Already major units
    else:
        major_amount = amount_minor / 100  # e.g., 19800 cents -> $198.00

    # Step 2: Convert to USD as intermediate
    usd_amount = major_amount / from_rate

    # Step 3: Convert USD to target currency major units
    target_major = usd_amount * to_rate

    # Step 4: Convert to minor units of target currency
    to_upper = to_currency.upper()
    if to_upper in ZERO_DECIMAL:
        return round(target_major)  # JPY 30,591 -> 30591
    else:
        return round(target_major * 100)  # EUR 182.16 -> 18216 cents
