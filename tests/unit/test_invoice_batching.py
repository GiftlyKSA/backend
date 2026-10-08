"""Invoice authoring keeps item persistence bounded and preserves the draft freeze."""

import uuid
from dataclasses import fields
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.pricing import PricingItem, PricingLine, calculate_invoice_totals
from app.models import Invoice, InvoiceItem
from app.models.enums import InvoiceStatus, OrderStatus
from app.repositories.invoice_repository import InvoiceRepository
from app.services.invoice_service import InvoiceLineInput, InvoiceService, NewInvoiceInput


class RecordingSession:
    """Record flush boundaries without substituting SQLite for PostgreSQL."""

    def __init__(self):
        self.invoice = None
        self.items = []
        self.flushes = []

    def add(self, instance):
        if isinstance(instance, Invoice):
            instance.id = uuid.uuid4()
            self.invoice = instance
        else:
            assert isinstance(instance, InvoiceItem)
            self.items.append(instance)

    def add_all(self, instances):
        for instance in instances:
            self.add(instance)

    async def scalar(self, query):
        return None

    async def flush(self):
        self.flushes.append((self.invoice.status, len(self.items)))


@pytest.mark.parametrize("item_count", [1, 20])
async def test_authoring_flush_count_is_constant_and_all_items_are_draft(test_settings, item_count):
    session = RecordingSession()
    courier_id = uuid.uuid4()
    order = SimpleNamespace(
        id=uuid.uuid4(),
        courier_id=courier_id,
        customer_id=uuid.uuid4(),
        status=OrderStatus.ASSIGNED,
    )
    service = InvoiceService(
        invoices=InvoiceRepository(session),
        orders=SimpleNamespace(lock_for_actor=AsyncMock(return_value=order)),
        promos=SimpleNamespace(),
        eligibility=SimpleNamespace(require_courier=AsyncMock()),
        reservations=SimpleNamespace(),
        settings=test_settings,
    )
    inputs = [
        InvoiceLineInput(
            title=f"Gift {position}",
            unit_price_amount=Decimal("10.25"),
            quantity=2,
            description="Wrapped",
        )
        for position in range(item_count)
    ]

    invoice = await service.create_invoice(
        order_id=order.id,
        courier_id=courier_id,
        data=NewInvoiceInput(items=inputs, courier_fee_amount=Decimal("15"), promo_code=None),
    )

    assert session.flushes == [
        (InvoiceStatus.DRAFT, 0),
        (InvoiceStatus.DRAFT, item_count),
        (InvoiceStatus.ISSUED, item_count),
        (InvoiceStatus.ISSUED, item_count),
    ]
    assert order.status is OrderStatus.WAITING_PAYMENT
    assert order.total_amount == invoice.total_amount
    assert len(session.items) == item_count
    for position, item in enumerate(session.items, start=1):
        assert item.invoice_id == invoice.id
        assert item.position == position
        for field in fields(InvoiceLineInput):
            assert getattr(item, field.name) == getattr(inputs[position - 1], field.name)
        assert item.line_total_amount == item.line_net_amount - item.line_discount_amount


async def test_single_item_compatibility_copies_every_computed_field(test_settings):
    session = RecordingSession()
    repository = InvoiceRepository(session)
    service = InvoiceService(
        invoices=repository,
        orders=None,
        promos=None,
        eligibility=None,
        reservations=None,
        settings=test_settings,
    )
    result = calculate_invoice_totals(
        [
            PricingItem(
                title="Gift",
                unit_price_amount=Decimal("12.50"),
                quantity=3,
                description="Wrapped",
                position=1,
            )
        ],
        Decimal("15"),
        None,
        service._pricing_config(),
    )
    invoice = await repository.create_draft(
        order_id=uuid.uuid4(),
        courier_id=uuid.uuid4(),
        result=result,
        promo_id=None,
        promo_code_snapshot=None,
    )
    await repository.add_item(invoice_id=invoice.id, line=result.lines[0])
    assert session.flushes == [(InvoiceStatus.DRAFT, 0), (InvoiceStatus.DRAFT, 1)]
    for field in fields(PricingLine):
        assert getattr(session.items[0], field.name) == getattr(result.lines[0], field.name)
