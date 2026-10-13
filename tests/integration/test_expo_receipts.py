"""Expo receipt registration and lease fences against real PostgreSQL behavior."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.integrations.push.expo import DeviceSnapshot
from app.models import DeviceToken, ExpoPushReceipt, User
from app.models.enums import DeviceOs, UserRole
from app.repositories.expo_receipt_repository import ExpoReceiptRepository
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def _device(session: AsyncSession) -> DeviceToken:
    user = User(phone=f"+9665{uuid.uuid4().int % 100000000:08d}", role=UserRole.CUSTOMER)
    session.add(user)
    await session.flush()
    device = DeviceToken(
        user_id=user.id, token=f"ExpoPushToken[{uuid.uuid4()}]", device_os=DeviceOs.IOS
    )
    session.add(device)
    await session.flush()
    return device


@pytest.mark.parametrize("refreshed", [False, True])
async def test_invalid_device_cleanup_keeps_refreshed_registration(
    db_session: AsyncSession,
    refreshed: bool,
) -> None:
    device = await _device(db_session)
    snapshot = DeviceSnapshot(device.id, device.token, device.last_seen_at)
    if refreshed:
        device.last_seen_at += timedelta(seconds=1)
        await db_session.flush()
    await ExpoReceiptRepository(db_session).remove_invalid(snapshot)
    found = await db_session.scalar(select(DeviceToken.id).where(DeviceToken.id == device.id))
    assert (found is not None) is refreshed


async def test_receipt_delay_retry_expiration_and_stale_lease(
    db_session: AsyncSession,
) -> None:
    device = await _device(db_session)
    snapshot = DeviceSnapshot(device.id, device.token, device.last_seen_at)
    now = datetime.now(UTC)
    ticket_id = str(uuid.uuid4())
    repository = ExpoReceiptRepository(db_session)
    await repository.save_tickets([(ticket_id, snapshot)], now=now)
    await repository.save_tickets([(ticket_id, snapshot)], now=now)
    assert await repository.claim_pending(now=now + timedelta(minutes=14)) == []
    claims = await repository.claim_pending(now=now + timedelta(minutes=15))
    claim = next(c for c in claims if c.ticket_id == ticket_id)
    assert claim.expires_at == now + timedelta(hours=24)
    assert await repository.claim_pending(now=now + timedelta(minutes=15)) == []
    replacement = uuid.uuid4()
    await db_session.execute(
        update(ExpoPushReceipt)
        .where(ExpoPushReceipt.ticket_id == ticket_id)
        .values(lease_id=replacement)
    )
    await repository.finish(
        claim,
        now=now + timedelta(minutes=15, seconds=1),
        error_code="DeviceNotRegistered",
        invalid_device=True,
    )
    assert (
        await db_session.scalar(select(DeviceToken.id).where(DeviceToken.id == device.id))
        == device.id
    )
    receipt = await db_session.scalar(
        select(ExpoPushReceipt)
        .where(ExpoPushReceipt.ticket_id == ticket_id)
        .execution_options(populate_existing=True)
    )
    assert receipt is not None
    assert receipt.completed_at is None
    receipt.leased_until = now
    await db_session.flush()
    fresh = next(
        c
        for c in await repository.claim_pending(now=now + timedelta(minutes=16))
        if c.ticket_id == ticket_id
    )
    await repository.finish(
        fresh,
        now=now + timedelta(minutes=16, seconds=1),
        error_code="DeviceNotRegistered",
        invalid_device=True,
    )
    assert (
        await db_session.scalar(select(DeviceToken.id).where(DeviceToken.id == device.id)) is None
    )


async def test_reassigned_device_survives_old_invalid_ticket(db_session: AsyncSession) -> None:
    device = await _device(db_session)
    snapshot = DeviceSnapshot(device.id, device.token, device.last_seen_at)
    replacement_user = User(
        phone=f"+9665{uuid.uuid4().int % 100000000:08d}", role=UserRole.CUSTOMER
    )
    db_session.add(replacement_user)
    await db_session.flush()
    from app.repositories.device_token_repository import DeviceTokenRepository

    await DeviceTokenRepository(db_session).register(
        user_id=replacement_user.id, token=device.token, device_os=DeviceOs.IOS
    )
    await ExpoReceiptRepository(db_session).remove_invalid(snapshot)
    owner = await db_session.scalar(select(DeviceToken.user_id).where(DeviceToken.id == device.id))
    assert owner == replacement_user.id


async def test_missing_receipt_retries_then_expires_without_deleting_device(
    db_session: AsyncSession,
) -> None:
    device = await _device(db_session)
    snapshot = DeviceSnapshot(device.id, device.token, device.last_seen_at)
    now = datetime.now(UTC)
    ticket_id = str(uuid.uuid4())
    repository = ExpoReceiptRepository(db_session)
    await repository.save_tickets([(ticket_id, snapshot)], now=now)
    claim = next(
        c
        for c in await repository.claim_pending(now=now + timedelta(minutes=15))
        if c.ticket_id == ticket_id
    )
    await repository.finish(
        claim, now=now + timedelta(minutes=15, seconds=1), error_code=None, retry=True
    )
    receipt = await db_session.scalar(
        select(ExpoPushReceipt)
        .where(ExpoPushReceipt.ticket_id == ticket_id)
        .execution_options(populate_existing=True)
    )
    assert receipt is not None
    assert receipt.completed_at is None
    assert receipt.available_at > now + timedelta(minutes=15, seconds=1)
    assert receipt.token == snapshot.token
    await repository.claim_pending(now=now + timedelta(hours=24))
    await db_session.refresh(receipt)
    assert receipt.error_code == "ReceiptExpired"
    assert receipt.completed_at == now + timedelta(hours=24)
    assert receipt.token == ""
    assert (
        await db_session.scalar(select(DeviceToken.id).where(DeviceToken.id == device.id))
        == device.id
    )


async def test_order_snapshot_excludes_token_reassigned_after_outbox_page(
    db_session: AsyncSession,
) -> None:
    from datetime import date

    from app.models import City, CourierProfile, OrderNotificationRecipient
    from app.repositories.device_token_repository import DeviceTokenRepository
    from app.repositories.order_repository import OrderRepository

    suffix = uuid.uuid4().hex
    city = City(name=f"Expo snapshot {suffix}", name_ar="Test", shortcut=suffix[:16])
    courier = User(phone=f"+9665{uuid.uuid4().int % 100000000:08d}", role=UserRole.COURIER)
    customer = User(phone=f"+9665{uuid.uuid4().int % 100000000:08d}", role=UserRole.CUSTOMER)
    db_session.add_all([city, courier, customer])
    await db_session.flush()
    db_session.add(
        CourierProfile(
            user_id=courier.id, city=city, is_verified=True, national_id_encrypted="ciphertext"
        )
    )
    device = DeviceToken(
        user_id=courier.id, token=f"ExpoPushToken[{suffix}]", device_os=DeviceOs.IOS
    )
    db_session.add(device)
    await db_session.flush()
    order = await OrderRepository(db_session).create(
        customer_id=customer.id,
        description=None,
        delivery_city=city,
        delivery_date=date.today() + timedelta(days=30),
        address_note=None,
    )
    db_session.add(
        OrderNotificationRecipient(order_id=order.id, token_id=device.id, user_id=courier.id)
    )
    await db_session.flush()
    repository = ExpoReceiptRepository(db_session)
    assert device.token in await repository.snapshot_devices([device.token], order_id=order.id)
    await DeviceTokenRepository(db_session).register(
        user_id=customer.id, token=device.token, device_os=DeviceOs.IOS
    )
    assert await repository.snapshot_devices([device.token], order_id=order.id) == {}
