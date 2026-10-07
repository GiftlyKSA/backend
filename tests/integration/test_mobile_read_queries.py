from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models import Invoice, Order, OrderMedia, Transaction, User
from app.models.enums import (
    InvoiceStatus,
    MediaType,
    TransactionStatus,
    TransactionType,
    UserRole,
)
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.wallet_repository import WalletRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import city_by_name


async def test_invoice_revision_pages_and_customer_draft_exclusion(db_session: AsyncSession):
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db_session.add_all([customer, courier])
    await db_session.flush()
    order = Order(
        customer_id=customer.id,
        courier_id=courier.id,
        city=await city_by_name(db_session, "Riyadh"),
        delivery_date=date(2026, 10, 7),
    )
    db_session.add(order)
    await db_session.flush()
    old = Invoice(
        order_id=order.id,
        issued_by_courier_id=courier.id,
        status=InvoiceStatus.CANCELLED,
        items_net_amount=Decimal("10.00"),
        net_after_discount_amount=Decimal("10.00"),
        total_amount=Decimal("10.00"),
        issued_at=datetime(2026, 10, 1, tzinfo=UTC),
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    current = Invoice(
        order_id=order.id,
        issued_by_courier_id=courier.id,
        status=InvoiceStatus.DRAFT,
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    db_session.add_all([old, current])
    await db_session.flush()
    repo = InvoiceRepository(db_session)
    params = dict(
        limit=26, cursor=None, status=None, include_historical=False, start=None, end=None
    )
    customer_rows = await repo.list_for_actor(customer.id, role=UserRole.CUSTOMER, **params)
    courier_rows = await repo.list_for_actor(courier.id, role=UserRole.COURIER, **params)
    assert [row.id for row, _ in customer_rows] == [old.id]
    assert [row.id for row, _ in courier_rows] == [current.id]
    with pytest.raises(NotFoundError):
        await repo.list_for_actor(uuid4(), role=UserRole.CUSTOMER, **{**params, "cursor": old.id})


async def test_empty_and_status_separated_statement_aggregate(db_session: AsyncSession):
    user = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    repo = WalletRepository(db_session)
    wallet = await repo.get_by_user(user.id)
    assert wallet is not None
    start, end = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
    empty = await repo.statement(wallet.id, start=start, end=end, limit=26, cursor=None)
    assert empty.items == [] and all(amount == 0 for amount in empty.totals.values())
    for status, amount in [
        (TransactionStatus.SETTLED, "25.00"),
        (TransactionStatus.PENDING, "50.00"),
        (TransactionStatus.REVERSED, "10.00"),
    ]:
        db_session.add(
            Transaction(
                wallet_id=wallet.id,
                type=TransactionType.TOPUP,
                status=status,
                amount=Decimal(amount),
                balance_after=Decimal(0),
                correlation_id=uuid4(),
                created_at=start,
            )
        )
    await db_session.flush()
    result = await repo.statement(wallet.id, start=start, end=end, limit=2, cursor=None)
    assert len(result.items) == 2
    assert result.totals["settled_credits"] == Decimal("25.00")
    assert result.totals["pending_credits"] == Decimal("50.00")
    assert result.totals["reversed_credits"] == Decimal("10.00")


async def test_order_media_equal_timestamp_pages_and_foreign_cursor(db_session: AsyncSession):
    user = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    order = Order(
        customer_id=user.id,
        city=await city_by_name(db_session, "Riyadh"),
        delivery_date=date(2026, 10, 7),
    )
    db_session.add(order)
    await db_session.flush()
    for _ in range(5):
        db_session.add(
            OrderMedia(
                order_id=order.id,
                uploaded_by_user_id=user.id,
                media_type=MediaType.CUSTOMER_REQUEST,
                storage_key=str(uuid4()),
                content_type="image/jpeg",
                byte_size=100,
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
    await db_session.flush()
    repo = OrderRepository(db_session)
    found, cursor = [], None
    for _ in range(4):
        rows = await repo.page_media_for_actor(
            order.id, user.id, purpose=None, limit=2, cursor=cursor
        )
        found.extend(row.id for row in rows)
        if len(rows) < 2:
            break
        cursor = rows[-1].id
    assert len(found) == len(set(found)) == 5
    with pytest.raises(NotFoundError):
        await repo.page_media_for_actor(order.id, uuid4(), purpose=None, limit=2, cursor=found[0])
