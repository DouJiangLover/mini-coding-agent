from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OrderLine:
    sku: str
    unit_price: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if not self.sku:
            raise ValueError("sku is required")
        if self.unit_price < 0:
            raise ValueError("unit_price cannot be negative")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True)
class OrderCommand:
    command_id: str
    customer_id: str
    lines: tuple[OrderLine, ...]
    coupon_rate: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("0")


@dataclass(frozen=True)
class Receipt:
    order_id: str
    command_id: str
    total: Decimal
