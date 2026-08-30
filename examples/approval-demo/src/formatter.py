from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def format_currency(amount: Decimal) -> str:
    """Format a Decimal amount as a currency string with two decimal places."""
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"¥{rounded}"
