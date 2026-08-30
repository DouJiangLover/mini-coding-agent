from __future__ import annotations

from decimal import Decimal

from .models import OrderLine


CENT = Decimal("0.01")


class InsufficientStock(RuntimeError):
    pass


class InventoryLedger:
    def __init__(self, stock: dict[str, int]) -> None:
        self._stock = dict(stock)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve_batch(self, lines: tuple[OrderLine, ...]) -> None:
        # BUG: if a later line is unavailable, earlier deductions are not rolled back.
        for line in lines:
            current = self.available(line.sku)
            if current < line.quantity:
                raise InsufficientStock(f"not enough stock for {line.sku}")
            self._stock[line.sku] = current - line.quantity

    def release_batch(self, lines: tuple[OrderLine, ...]) -> None:
        for line in lines:
            self._stock[line.sku] = self.available(line.sku) + line.quantity


def calculate_total(
    lines: tuple[OrderLine, ...],
    coupon_rate: Decimal = Decimal("0"),
    tax_rate: Decimal = Decimal("0"),
) -> Decimal:
    if not Decimal("0") <= coupon_rate <= Decimal("1"):
        raise ValueError("coupon_rate must be between 0 and 1")
    if tax_rate < 0:
        raise ValueError("tax_rate cannot be negative")
    subtotal = sum((line.unit_price * line.quantity for line in lines), Decimal("0"))
    discounted = subtotal * (Decimal("1") - coupon_rate)
    taxed = discounted * (Decimal("1") + tax_rate)
    # BUG: Decimal defaults to ROUND_HALF_EVEN, but the business requires HALF_UP.
    return taxed.quantize(CENT)
