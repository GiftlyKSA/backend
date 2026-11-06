"""Real PostgreSQL device delivery idempotency, leases and provider outcomes."""

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from app.core.db import build_engine, build_session_factory
from app.integrations.push.expo import ExpoOutcome
from app.models import City, CourierProfile, DeviceToken, ExpoPushReceipt, Order, User
from app.models.enums import DeviceOs, OrderStatus, UserRole
from app.models.push_delivery import PushDelivery
from app.repositories.expo_receipt_repository import ExpoReceiptRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.push_delivery_repository import PushDeliveryRepository
from app.schemas.push_event import PushEvent
from sqlalchemy import delete, func, select, text

from tests.conftest import make_test_settings


async def setup_order(session):
    suffix = uuid4().hex
    city = City(name=f"Push {suffix}", name_ar="Test", shortcut=suffix[:16])
    customer = User(phone=f"+9665{uuid4().int % 100000000:08d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+9665{uuid4().int % 100000000:08d}", role=UserRole.COURIER)
    session.add_all([city, customer, courier])
    await session.flush()
    session.add(
        CourierProfile(
            user_id=courier.id, city=city, is_verified=True, national_id_encrypted="ciphertext"
        )
    )
    devices = [
        DeviceToken(user_id=courier.id, token=f"ExpoPushToken[{uuid4()}]", device_os=DeviceOs.IOS)
        for _ in range(2)
    ]
    session.add_all(devices)
    await session.flush()
    order = await OrderRepository(session).create(
        customer_id=customer.id,
        description=None,
        delivery_city=city,
        delivery_date=date.today() + timedelta(days=7),
        address_note=None,
    )
    event = PushEvent(
        event_id=uuid5(NAMESPACE_URL, f"giftly:order:{order.id}"),
        event_type="ORDER_AVAILABLE",
        type="ORDER_AVAILABLE",
        order_id=order.id,
    )
    return customer, courier, order, devices, event


async def test_partial_acceptance_retries_only_failed_device_and_replay_does_not_reset(db_session):
    _, _, _, devices, event = await setup_order(db_session)
    now = datetime.now(UTC)
    repo = PushDeliveryRepository(db_session)
    await repo.enqueue([d.token for d in devices], event, now=now)
    claims = await repo.claim(now=now)
    assert len(claims) == 2
    assert await repo.claim(now=now) == []
    await repo.finish(
        claims,
        [
            ExpoOutcome(status="ok", id=str(uuid4())),
            ExpoOutcome(status="error", details={"error": "MessageRateExceeded"}),
        ],
        now=now + timedelta(seconds=1),
    )
    await repo.enqueue([d.token for d in devices], event, now=now)
    assert await db_session.scalar(select(func.count()).select_from(PushDelivery)) == 2
    retry = await repo.claim(now=now + timedelta(minutes=2))
    assert [c.id for c in retry] == [claims[1].id]
    receipt = await db_session.scalar(
        select(ExpoPushReceipt).where(ExpoPushReceipt.delivery_id == claims[0].id)
    )
    assert receipt is not None and receipt.token == ""


@pytest.mark.parametrize("change", ["cancel", "city", "unverify", "reassign"])
async def test_current_eligibility_is_rechecked_before_delivery(db_session, change):
    customer, courier, order, devices, event = await setup_order(db_session)
    now = datetime.now(UTC)
    repo = PushDeliveryRepository(db_session)
    await repo.enqueue([devices[0].token], event, now=now)
    if change == "cancel":
        order.status = OrderStatus.CANCELLED
    elif change == "city":
        city = City(name=f"Other {uuid4()}", name_ar="Other", shortcut=uuid4().hex[:16])
        db_session.add(city)
        await db_session.flush()
        order.delivery_city_id = city.id
    elif change == "unverify":
        profile = await db_session.scalar(
            select(CourierProfile).where(CourierProfile.user_id == courier.id)
        )
        profile.is_verified = False
    else:
        devices[0].user_id = customer.id
    await db_session.flush()
    assert await repo.claim(now=now) == []
    row = await db_session.scalar(
        select(PushDelivery).where(PushDelivery.event_id == event.event_id)
    )
    assert row.state == "EXPIRED"


async def test_expired_claim_recovery_and_stale_completion_are_fenced(db_session):
    _, _, _, devices, event = await setup_order(db_session)
    now = datetime.now(UTC)
    repo = PushDeliveryRepository(db_session)
    await repo.enqueue([devices[0].token], event, now=now)
    stale = (await repo.claim(now=now))[0]
    fresh = (await repo.claim(now=now + timedelta(seconds=61)))[0]
    await repo.finish(
        [stale], [ExpoOutcome(status="ok", id="stale")], now=now + timedelta(seconds=62)
    )
    assert (
        await db_session.scalar(
            select(ExpoPushReceipt.ticket_id).where(ExpoPushReceipt.ticket_id == "stale")
        )
        is None
    )
    await repo.finish(
        [fresh], [ExpoOutcome(status="ok", id=str(uuid4()))], now=now + timedelta(seconds=63)
    )
    assert await repo.claim(now=now + timedelta(minutes=3)) == []


async def test_transient_final_receipt_requeues_send_and_old_token_failure_is_fenced(db_session):
    _, _, _, devices, event = await setup_order(db_session)
    now = datetime.now(UTC)
    repo = PushDeliveryRepository(db_session)
    await repo.enqueue([devices[0].token], event, now=now)
    original = (await repo.claim(now=now))[0]
    ticket = str(uuid4())
    await repo.finish([original], [ExpoOutcome(status="ok", id=ticket)], now=now)
    receipts = ExpoReceiptRepository(db_session)
    claim = next(
        c
        for c in await receipts.claim_pending(now=now + timedelta(minutes=15))
        if c.ticket_id == ticket
    )
    await receipts.finish(
        claim, now=now + timedelta(minutes=15, seconds=1), error_code="MessageRateExceeded"
    )
    assert await receipts.claim_pending(now=now + timedelta(minutes=25)) == []
    resent = await repo.claim(now=now + timedelta(minutes=25))
    assert len(resent) == 1 and resent[0].id == original.id
    devices[0].last_seen_at += timedelta(seconds=1)
    await db_session.flush()
    await repo.finish(
        resent,
        [ExpoOutcome(status="error", details={"error": "DeviceNotRegistered"})],
        now=now + timedelta(minutes=25, seconds=1),
    )
    assert await db_session.scalar(select(DeviceToken.id).where(DeviceToken.id == devices[0].id))


async def test_overlapping_transactions_cannot_claim_the_same_device():
    settings = make_test_settings(
        **{name: os.environ[name] for name in ("DATABASE_URL",) if os.environ.get(name)}
    )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    user_ids, order_id, city_id = [], None, None
    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            pytest.skip("Disposable PostgreSQL is unavailable.")
        now = datetime(2000, 1, 1, tzinfo=UTC)
        async with factory() as session:
            customer, courier, order, devices, event = await setup_order(session)
            user_ids = [customer.id, courier.id]
            order_id, city_id = order.id, order.delivery_city_id
            await PushDeliveryRepository(session).enqueue([devices[0].token], event, now=now)
            await session.commit()
        async with factory() as first, factory() as second:
            await first.execute(text("SET LOCAL statement_timeout='3s'"))
            await second.execute(text("SET LOCAL statement_timeout='3s'"))
            claims = await PushDeliveryRepository(first).claim(now=now, limit=1)
            assert len(claims) == 1
            assert (
                await asyncio.wait_for(
                    PushDeliveryRepository(second).claim(now=now, limit=1),
                    timeout=4,
                )
                == []
            )
            await second.commit()
            await first.commit()
    finally:
        if user_ids:
            async with factory() as session:
                await session.execute(delete(Order).where(Order.id == order_id))
                await session.execute(delete(User).where(User.id.in_(user_ids)))
                await session.execute(delete(City).where(City.id == city_id))
                await session.commit()
        await engine.dispose()
