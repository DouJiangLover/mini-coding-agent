from decimal import Decimal

from src.receipt import build_receipt


def test_receipt_keeps_two_decimal_places() -> None:
    assert build_receipt("咖啡", Decimal("12.5")) == "咖啡：¥12.50"


def test_receipt_uses_business_half_up_rounding() -> None:
    assert build_receipt("折扣", Decimal("2.345")) == "折扣：¥2.35"
