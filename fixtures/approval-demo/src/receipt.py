from __future__ import annotations

from decimal import Decimal

from .formatter import format_currency


def build_receipt(item: str, amount: Decimal) -> str:
    """Build one display line for a receipt."""
    return f"{item}：{format_currency(amount)}"
