"""Committed orders produce durable, bounded city push work."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.core.config import Environment, Settings
from app.integrations.push.fake import FakePushClient
from app.models import (
    CourierProfile,
    DeviceToken,
    OrderNotification,
    OrderNotificationRecipient,
    User,
)
from app.models.enums import DeviceOs, UserRole, UserStatus
from app.repositories.order_notification_repository import OrderNotificationRepository
from app.repositories.order_repository import OrderRepository
from app.services.order_notification_service import send_pending_order_notifications
from sqlalchemy import select
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


async def test_snapshot_pages_exclude_new_revoked_and_reassigned_tokens(
    db_session: AsyncSession,
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
    db_session.add(
        CourierProfile(
            user_id=courier.id, city=city, is_verified=True, national_id_encrypted="ciphertext"
        )
    )
    tokens = [
        DeviceToken(
            id=uuid.UUID(int=number),
            user_id=courier.id,
            token=f"snapshot-token-{number}",
            device_os=DeviceOs.IOS,
        )
        for number in (100, 200, 300, 400, 500)
    ]
    db_session.add_all(tokens)
    order = await OrderRepository(db_session).create(
        customer_id=customer.id,
        description=None,
        delivery_city=city,
        delivery_date=date.today() + timedelta(days=30),
        address_note=None,
    )
    await db_session.commit()
    repo = OrderNotificationRepository(db_session)
    first_claim = (await repo.claim_pending(now=datetime.now(UTC), limit=1, lease_seconds=60))[0]
    await db_session.commit()
    first_page = await repo.token_page(order_id=order.id, city_id=city.id, after=None, limit=2)
    assert [token_id for token_id, _token in first_page] == [tokens[0].id, tokens[1].id]
    assert await repo.advance(
        first_claim, now=datetime.now(UTC), cursor=first_page[-1][0], complete=False
    )
    await db_session.commit()

    await db_session.delete(tokens[2])
    tokens[3].user_id = customer.id
    db_session.add(
        DeviceToken(
            id=uuid.UUID(int=600),
            user_id=courier.id,
            token="new-after-snapshot",
            device_os=DeviceOs.IOS,
        )
    )
    await db_session.commit()
    second_claim = (await repo.claim_pending(now=datetime.now(UTC), limit=1, lease_seconds=60))[0]
    await db_session.commit()
    second_page = await repo.token_page(
        order_id=order.id, city_id=city.id, after=second_claim.cursor_token_id, limit=2
    )
    assert second_page == [(tokens[4].id, tokens[4].token)]
    assert await repo.advance(
        second_claim, now=datetime.now(UTC), cursor=second_page[-1][0], complete=True
    )
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(OrderNotificationRecipient.order_id).where(
                OrderNotificationRecipient.order_id == order.id
            )
        )
        is None
    )
