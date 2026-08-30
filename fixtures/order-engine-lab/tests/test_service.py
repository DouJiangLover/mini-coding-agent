from decimal import Decimal

import pytest

from src.audit import InMemoryAuditLog
from src.engine import InventoryLedger
from src.models import OrderCommand, OrderLine
from src.service import MemoryOrderRepository, OrderService


def command(command_id: str = "cmd-001") -> OrderCommand:
    return OrderCommand(
        command_id=command_id,
        customer_id="customer-7",
        lines=(OrderLine("book", Decimal("12.50"), 1),),
    )


def test_repeated_command_is_idempotent() -> None:
    inventory = InventoryLedger({"book": 3})
    repository = MemoryOrderRepository()
    audit = InMemoryAuditLog()
    service = OrderService(inventory, repository, audit)

    first = service.process(command())
    second = service.process(command())

    assert second == first
    assert inventory.available("book") == 2
    assert len(audit.events) == 1


def test_audit_failure_rolls_back_order_and_inventory() -> None:
    inventory = InventoryLedger({"book": 3})
    repository = MemoryOrderRepository()
    audit = InMemoryAuditLog(fail=True)
    service = OrderService(inventory, repository, audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.process(command("cmd-audit-down"))

    assert inventory.available("book") == 3
    assert repository.find_by_command("cmd-audit-down") is None
