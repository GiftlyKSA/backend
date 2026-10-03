"""Committed orders produce durable, bounded city push work."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.config import Environment, Settings
from app.integrations.push.fake import FakePushClient
from app.models import CourierProfile, DeviceToken, OrderNotification, User
from app.models.enums import DeviceOs, UserRole, UserStatus
from app.repositories.order_repository import OrderRepository
from app.services.order_notification_service import send_pending_order_notifications
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from tests.integration.conftest import city_by_name


async def test_order_push_waits_for_commit_and_uses_outbox(
    db_session: AsyncSession, db_connection: AsyncConnection, test_settings: Settings
) -> None:
    city = await city_by_name(db_session, "Jeddah")
    customer = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(
        phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
        role=UserRole.COURIER,
        status=UserStatus.ACTIVE,
    )
    db_session.add_all((customer, courier))
    await db_session.flush()
    db_session.add_all(
        (
            CourierProfile(
                user_id=courier.id,
                city=city,
                is_verified=True,
                national_id_encrypted="ciphertext",
            ),
            DeviceToken(user_id=courier.id, token="order-push-test", device_os=DeviceOs.IOS),
        )
    )
    order = await OrderRepository(db_session).create(
        customer_id=customer.id,
        description=None,
        delivery_city=city,
        delivery_date=date.today() + timedelta(days=30),
        address_note=None,
    )
    notification = await db_session.get(OrderNotification, order.id)
    assert notification is not None

    push = FakePushClient(Environment.TEST)
    assert push.sent == []
    await db_session.commit()
    factory = async_sessionmaker(db_connection, expire_on_commit=False)
    assert (
        await send_pending_order_notifications(
            limit=1, push=push, factory=factory, settings=test_settings
        )
        == 1
    )
    assert len(push.sent) == 1
    assert push.sent[0].tokens == ["order-push-test"]
    await db_session.refresh(notification)
    assert notification.completed_at is not None


async def test_rolled_back_order_has_no_outbox_row(db_session: AsyncSession) -> None:
    city = await city_by_name(db_session, "Jeddah")
    customer = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(customer)
    await db_session.flush()
    nested = await db_session.begin_nested()
    order = await OrderRepository(db_session).create(
        customer_id=customer.id,
        description=None,
        delivery_city=city,
        delivery_date=date.today() + timedelta(days=30),
        address_note=None,
    )
    await nested.rollback()
    assert await db_session.get(OrderNotification, order.id) is None
