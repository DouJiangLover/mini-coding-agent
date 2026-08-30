from __future__ import annotations

from .audit import AuditLog
from .engine import InventoryLedger, calculate_total
from .models import OrderCommand, Receipt


class MemoryOrderRepository:
    def __init__(self) -> None:
        self._by_command: dict[str, Receipt] = {}

    def find_by_command(self, command_id: str) -> Receipt | None:
        return self._by_command.get(command_id)

    def save(self, receipt: Receipt) -> None:
        self._by_command[receipt.command_id] = receipt

    def remove(self, command_id: str) -> None:
        self._by_command.pop(command_id, None)


class OrderService:
    def __init__(self, inventory: InventoryLedger, repository: MemoryOrderRepository, audit: AuditLog) -> None:
        self.inventory = inventory
        self.repository = repository
        self.audit = audit
        self._sequence = 0

    def process(self, command: OrderCommand) -> Receipt:
        # BUG: customer_id is used instead of command_id, breaking idempotency.
        previous = self.repository.find_by_command(command.customer_id)
        if previous is not None:
            return previous

        self.inventory.reserve_batch(command.lines)
        total = calculate_total(command.lines, command.coupon_rate, command.tax_rate)
        self._sequence += 1
        receipt = Receipt(
            order_id=f"order-{self._sequence:04d}",
            command_id=command.command_id,
            total=total,
        )
        self.repository.save(receipt)
        # BUG: an audit failure leaves both the saved order and stock deduction behind.
        self.audit.record("order.created", {"order_id": receipt.order_id, "command_id": command.command_id})
        return receipt
