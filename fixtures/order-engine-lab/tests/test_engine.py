from decimal import Decimal

import pytest

from src.engine import InsufficientStock, InventoryLedger, calculate_total
from src.models import OrderLine


def line(sku: str, price: str, quantity: int = 1) -> OrderLine:
    return OrderLine(sku=sku, unit_price=Decimal(price), quantity=quantity)


def test_money_uses_business_half_up_rounding() -> None:
    assert calculate_total((line("book", "10.005"),)) == Decimal("10.01")


def test_discount_and_tax_keep_decimal_precision() -> None:
    total = calculate_total(
        (line("keyboard", "19.99", 3),),
        coupon_rate=Decimal("0.15"),
        tax_rate=Decimal("0.0825"),
    )
    assert total == Decimal("55.18")


def test_batch_reservation_rolls_back_when_a_later_sku_is_short() -> None:
    inventory = InventoryLedger({"available": 2, "sold-out": 0})

    with pytest.raises(InsufficientStock):
        inventory.reserve_batch((line("available", "3.00"), line("sold-out", "4.00")))

    assert inventory.available("available") == 2
    assert inventory.available("sold-out") == 0
