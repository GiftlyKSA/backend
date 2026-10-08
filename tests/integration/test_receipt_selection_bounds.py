"""Deliverable receipts must not wait behind customers without an email."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Invoice, Order, User
from app.models.enums import InvoiceStatus, OrderStatus, UserRole
from app.repositories.invoice_repository import InvoiceRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import city_by_name


async def test_missing_email_backlog_does_not_starve_deliverable_receipts(
    db_session: AsyncSession,
) -> None:
    customer = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    buyer = User(
        phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
        role=UserRole.CUSTOMER,
        email=f"{uuid.uuid4()}@example.com",
    )
    db_session.add_all([customer, courier, buyer])
    await db_session.flush()
    city = await city_by_name(db_session, "Jeddah")
    invoices = []
    now = datetime.now(UTC)
    for index in range(101):
        order = Order(
            customer_id=customer.id if index < 100 else buyer.id,
            courier_id=courier.id,
            city=city,
            delivery_date=now.date(),
            status=OrderStatus.IN_PROGRESS,
        )
        db_session.add(order)
        await db_session.flush()
        invoice = Invoice(
            order_id=order.id,
            issued_by_courier_id=courier.id,
            status=InvoiceStatus.PAID,
            items_net_amount=Decimal("1.00"),
            courier_fee_amount=Decimal("0.00"),
            service_fee_amount=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            net_after_discount_amount=Decimal("1.00"),
            total_amount=Decimal("1.00"),
            issued_at=now,
            paid_at=now - timedelta(days=200 - index),
        )
        db_session.add(invoice)
        invoices.append(invoice)
    await db_session.flush()
    pending = await InvoiceRepository(db_session).list_receipt_pending(100)
    assert invoices[-1].id in [invoice.id for invoice in pending]
    assert not any(invoice.receipt_email_sent_at for invoice in invoices[:-1])
    customer.email = f"{uuid.uuid4()}@example.com"
    await db_session.flush()
    pending = await InvoiceRepository(db_session).list_receipt_pending(100)
    assert invoices[0].id in [invoice.id for invoice in pending]
