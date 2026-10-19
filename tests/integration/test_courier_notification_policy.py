"""Courier inbox policy shares one visible set across paging and read operations."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.audit_context import set_audit_actor
from app.core.exceptions import NotFoundError
from app.models import Invoice, Order, User
from app.models.enums import InvoiceStatus, OrderStatus, UserRole
from app.models.notification_models import InboxNotification
from app.repositories.notification_inbox_repository import NotificationInboxRepository
from app.services.notification_cursor import encode_cursor
from app.services.notification_inbox_service import NotificationInboxService
from sqlalchemy import select

from tests.integration.conftest import city_by_name


async def create_order(session, customer, courier):
    order = Order(
        customer_id=customer.id,
        courier_id=courier.id,
        city=await city_by_name(session, "Riyadh"),
        delivery_date=date.today(),
        status=OrderStatus.ASSIGNED,
    )
    session.add(order)
    await session.flush()
    return order


async def transition(session, order, status, category, actor_id):
    await set_audit_actor(session, category=category, actor_user_id=actor_id)
    order.status = status
    await session.flush()


async def test_courier_sees_only_paid_customer_cancelled_and_customer_confirmed(db_session):
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96651{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db_session.add_all([customer, courier])
    await db_session.flush()
    paid = await create_order(db_session, customer, courier)
    await transition(db_session, paid, OrderStatus.WAITING_PAYMENT, "COURIER", courier.id)
    invoice = Invoice(
        order_id=paid.id,
        issued_by_courier_id=courier.id,
        status=InvoiceStatus.ISSUED,
        items_net_amount=Decimal("100.00"),
        net_after_discount_amount=Decimal("100.00"),
        total_amount=Decimal("100.00"),
    )
    db_session.add(invoice)
    await db_session.flush()
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = datetime.now(UTC)
    await transition(db_session, paid, OrderStatus.IN_PROGRESS, "SYSTEM", None)
    await transition(db_session, paid, OrderStatus.DELIVERED, "COURIER", courier.id)
    await transition(db_session, paid, OrderStatus.COMPLETED, "CUSTOMER", customer.id)
    cancelled = await create_order(db_session, customer, courier)
    await transition(db_session, cancelled, OrderStatus.CANCELLED, "CUSTOMER", customer.id)
    for category, actor_id, status in [
        ("SYSTEM", None, OrderStatus.COMPLETED),
        ("ADMIN", customer.id, OrderStatus.COMPLETED),
        ("SYSTEM", None, OrderStatus.CANCELLED),
        ("COURIER", courier.id, OrderStatus.CANCELLED),
    ]:
        other = await create_order(db_session, customer, courier)
        await transition(db_session, other, status, category, actor_id)
    unpaid = await create_order(db_session, customer, courier)
    await transition(db_session, unpaid, OrderStatus.IN_PROGRESS, "ADMIN", customer.id)
    db_session.add(
        InboxNotification(
            user_id=courier.id,
            event_key=f"withdrawal:{uuid4()}",
            notification_type="WITHDRAWAL_STATUS_CHANGED",
            destination_kind="WALLET",
            resource_id=uuid4(),
            status="PAID",
        )
    )
    await db_session.flush()
    service = NotificationInboxService(NotificationInboxRepository(db_session), AsyncMock())
    first = await service.list(courier.id, unread_only=True, limit=2, cursor=None)
    assert first.unread_count == 3
    assert len(first.items) == 2 and first.next_cursor is not None
    second = await service.list(courier.id, unread_only=True, limit=2, cursor=first.next_cursor)
    assert second.next_cursor is None and len(second.items) == 1
    visible = [*first.items, *second.items]
    assert len({row.id for row in visible}) == 3
    assert {row.parameters.status for row in visible} == {"IN_PROGRESS", "COMPLETED", "CANCELLED"}
    assert all(row.type == "ORDER_STATUS_CHANGED" for row in visible)
    assert {row.navigation.order_id for row in visible} == {paid.id, cancelled.id}
    customer_page = await service.list(customer.id, unread_only=False, limit=100, cursor=None)
    assert len(customer_page.items) > 3
    assert any(row.type == "INVOICE_ISSUED" for row in customer_page.items)
    hidden = await db_session.scalar(
        select(InboxNotification).where(
            InboxNotification.user_id == courier.id, InboxNotification.status == "WAITING_PAYMENT"
        )
    )
    with pytest.raises(NotFoundError):
        await service.read(courier.id, hidden.id)
    with pytest.raises(NotFoundError):
        await service.list(courier.id, unread_only=False, limit=2, cursor=encode_cursor(hidden.id))
    with pytest.raises(NotFoundError):
        await service.read(uuid4(), visible[0].id)
    read = await service.read(courier.id, visible[0].id)
    assert read.read_at is not None
    assert (await service.read(courier.id, visible[0].id)).read_at == read.read_at
    assert (await service.unread_count(courier.id)).unread_count == 2
    assert (await service.read_all(courier.id)).marked_count == 2
    assert (await service.unread_count(courier.id)).unread_count == 0
    assert (await service.read_all(courier.id)).marked_count == 0
    await db_session.refresh(hidden)
    assert hidden.read_at is None
    empty = await service.list(courier.id, unread_only=True, limit=2, cursor=None)
    assert empty.items == [] and empty.next_cursor is None
