"""Durable inbox, real status history and one-off schedules against disposable PostgreSQL."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.audit_context import set_audit_actor
from app.core.exceptions import NotFoundError
from app.models import Order, User
from app.models.enums import OrderStatus, UserRole
from app.models.notification_models import (
    InboxNotification,
    OccasionReminder,
    OrderTimelineEvent,
    OrderTimelineOrigin,
)
from app.repositories.notification_inbox_repository import NotificationInboxRepository
from app.repositories.notification_reminder_repository import NotificationReminderRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.order_timeline_repository import OrderTimelineRepository
from app.repositories.planning_repository import PlanningRepository
from app.services.notification_inbox_service import NotificationInboxService
from app.services.occasion_service import OccasionService
from app.services.order_timeline_service import OrderTimelineService
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import city_by_name


async def participants(session: AsyncSession) -> tuple[User, User]:
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96651{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    session.add_all([customer, courier])
    await session.flush()
    return customer, courier


async def order_for(session: AsyncSession, customer: User) -> Order:
    city = await city_by_name(session, "Riyadh")
    order = Order(customer_id=customer.id, city=city, delivery_date=date.today())
    session.add(order)
    await session.flush()
    return order


async def test_actual_status_capture_privacy_dedup_and_rollback(db_session: AsyncSession) -> None:
    customer, courier = await participants(db_session)
    await set_audit_actor(db_session, category="CUSTOMER", actor_user_id=customer.id)
    order = await order_for(db_session, customer)
    order_id = order.id
    await set_audit_actor(db_session, category="COURIER", actor_user_id=courier.id)
    order.courier_id = courier.id
    order.status = OrderStatus.ASSIGNED
    await db_session.flush()
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(status=OrderStatus.ASSIGNED)
    )
    events = list(
        await db_session.scalars(
            select(OrderTimelineEvent)
            .where(OrderTimelineEvent.order_id == order_id)
            .order_by(OrderTimelineEvent.created_at, OrderTimelineEvent.id)
        )
    )
    assert [(event.status, event.actor_role) for event in events] == [
        ("NEW", "CUSTOMER"),
        ("ASSIGNED", "COURIER"),
    ]
    assert not hasattr(events[0], "audit_metadata")
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InboxNotification)
            .where(InboxNotification.user_id == customer.id)
        )
        == 2
    )
    nested = await db_session.begin_nested()
    await set_audit_actor(db_session, category="ADMIN", actor_user_id=customer.id)
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(status=OrderStatus.CANCELLED)
    )
    await nested.rollback()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OrderTimelineEvent)
            .where(OrderTimelineEvent.order_id == order_id)
        )
        == 2
    )
    await set_audit_actor(db_session, category="SYSTEM", actor_user_id=None)
    await db_session.execute(
        update(Order).where(Order.id == order_id).values(status=OrderStatus.CANCELLED)
    )
    event = await db_session.scalar(
        select(OrderTimelineEvent).where(
            OrderTimelineEvent.order_id == order_id, OrderTimelineEvent.status == "CANCELLED"
        )
    )
    assert event is not None and event.actor_role is None


async def test_inbox_owned_paging_idempotent_reads_and_new_arrivals(
    db_session: AsyncSession,
) -> None:
    customer, _ = await participants(db_session)
    first = await order_for(db_session, customer)
    second = await order_for(db_session, customer)
    service = NotificationInboxService(NotificationInboxRepository(db_session), AsyncMock())
    page = await service.list(customer.id, unread_only=True, limit=1, cursor=None)
    assert len(page.items) == 1 and page.next_cursor and page.unread_count == 2
    assert page.items[0].navigation.order_id == second.id
    assert page.items[0].title_key == "notification.order_status_changed.title"
    assert page.items[0].parameters.status == "NEW"
    read = await service.read(customer.id, page.items[0].id)
    repeated = await service.read(customer.id, page.items[0].id)
    assert read.read_at == repeated.read_at
    after = await service.list(customer.id, unread_only=True, limit=1, cursor=page.next_cursor)
    assert len(after.items) == 1 and after.items[0].navigation.order_id == first.id
    with pytest.raises(NotFoundError):
        await service.read(uuid4(), page.items[0].id)
    with pytest.raises(NotFoundError):
        await service.list(uuid4(), unread_only=False, limit=1, cursor=page.next_cursor)
    marked = await service.read_all(customer.id)
    assert marked.marked_count == 1
    await order_for(db_session, customer)
    assert (await service.unread_count(customer.id)).unread_count == 1


async def test_timeline_participants_scoped_cursor_and_historical_flag(
    db_session: AsyncSession,
) -> None:
    customer, courier = await participants(db_session)
    order = await order_for(db_session, customer)
    order.courier_id = courier.id
    order.status = OrderStatus.ASSIGNED
    await db_session.flush()
    service = OrderTimelineService(
        OrderTimelineRepository(db_session), OrderRepository(db_session), AsyncMock()
    )
    page = await service.list(customer.id, order.id, limit=1, cursor=None)
    assert page.history_complete and page.items[0].status == "NEW" and page.next_cursor
    after = await service.list(courier.id, order.id, limit=1, cursor=page.next_cursor)
    assert after.items[0].status == "ASSIGNED" and not after.next_cursor
    with pytest.raises(NotFoundError):
        await service.list(uuid4(), order.id, limit=1, cursor=None)
    other = await order_for(db_session, customer)
    with pytest.raises(NotFoundError):
        await service.list(customer.id, other.id, limit=1, cursor=page.next_cursor)
    await db_session.execute(
        delete(OrderTimelineOrigin).where(OrderTimelineOrigin.order_id == order.id)
    )
    assert (
        await service.list(customer.id, order.id, limit=25, cursor=None)
    ).history_complete is False
    await db_session.execute(
        update(Order).where(Order.id == order.id).values(courier_id=None, status=OrderStatus.NEW)
    )
    with pytest.raises(NotFoundError):
        await service.list(courier.id, order.id, limit=25, cursor=None)


async def test_one_off_reminders_opt_in_schedule_edit_dedup_and_delete(
    db_session: AsyncSession,
) -> None:
    customer, _ = await participants(db_session)
    service = OccasionService(PlanningRepository(db_session))
    day = date(2027, 2, 1)
    occasion = await service.create(
        customer.id, title="Private anniversary", occasion_date=day, reminder_days_before=7
    )
    schedule = await db_session.get(OccasionReminder, occasion.id)
    assert schedule is not None and schedule.enabled is False
    assert schedule.scheduled_at == datetime(2027, 1, 25, 6, tzinfo=UTC)
    repo = NotificationReminderRepository(db_session)
    due = datetime(2027, 1, 25, 6, tzinfo=UTC)
    assert await repo.materialize_due(now=due) == 0
    await service.update(
        customer.id,
        occasion.id,
        title=None,
        occasion_date=None,
        reminder_days_before=None,
        reminder_enabled=True,
    )
    await db_session.refresh(schedule)
    assert schedule.version == 2 and schedule.enabled
    assert await repo.materialize_due(now=due - timedelta(seconds=1)) == 0
    assert await repo.materialize_due(now=due) == 1
    assert await repo.materialize_due(now=due) == 0
    inbox = await db_session.scalar(
        select(InboxNotification).where(InboxNotification.user_id == customer.id)
    )
    assert inbox is not None and inbox.notification_type == "OCCASION_REMINDER"
    assert not hasattr(inbox, "title")
    await service.update(
        customer.id,
        occasion.id,
        title=None,
        occasion_date=None,
        reminder_days_before=None,
        reminder_enabled=False,
    )
    await service.update(
        customer.id,
        occasion.id,
        title=None,
        occasion_date=None,
        reminder_days_before=0,
        reminder_enabled=True,
    )
    assert await repo.materialize_due(now=datetime(2027, 2, 1, 6, tzinfo=UTC)) == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InboxNotification)
            .where(InboxNotification.user_id == customer.id)
        )
        == 1
    )
    identifier = occasion.id
    await service.delete(customer.id, identifier)
    db_session.expire_all()
    assert await db_session.get(OccasionReminder, identifier) is None


async def test_reminder_retry_limits_lease_fencing_and_disabled_cancel(
    db_session: AsyncSession,
) -> None:
    customer, _ = await participants(db_session)
    service = OccasionService(PlanningRepository(db_session))
    now = datetime.now(UTC)
    occasion = await service.create(
        customer.id,
        title="Private",
        occasion_date=now.date(),
        reminder_days_before=0,
        reminder_enabled=True,
    )
    schedule = await db_session.get(OccasionReminder, occasion.id)
    assert schedule is not None
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == occasion.id)
        .values(scheduled_at=now - timedelta(minutes=1))
    )
    repo = NotificationReminderRepository(db_session)
    assert await repo.materialize_due(now=now) == 1
    claim = await repo.claim(now=now)
    assert claim is not None
    assert await repo.claim(now=now) is None
    await repo.finish(claim, now=now, cursor=None, complete=False, failed=True)
    await db_session.refresh(schedule)
    assert schedule.push_attempts == 1 and schedule.next_attempt_at == now + timedelta(seconds=60)
    for attempt in range(2, 6):
        now += timedelta(seconds=30 * 2 ** (attempt - 1))
        claim = await repo.claim(now=now)
        assert claim is not None
        await repo.finish(claim, now=now, cursor=None, complete=False, failed=True)
    now += timedelta(seconds=960)
    await repo.retire_pending(now=now)
    assert await repo.claim(now=now) is None
    await db_session.refresh(schedule)
    assert schedule.push_attempts == 5 and schedule.push_completed_at is not None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InboxNotification)
            .where(InboxNotification.user_id == customer.id)
        )
        == 1
    )


async def test_origin_trigger_delivery_dedup_function(db_session: AsyncSession) -> None:
    customer, _ = await participants(db_session)
    resource = uuid4()
    for _ in range(2):
        await db_session.execute(
            text(
                "SELECT giftly_inbox_event(:user,'same-event',"
                "'WALLET_TOPUP_COMPLETED','WALLET',:resource)"
            ),
            {"user": customer.id, "resource": resource},
        )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InboxNotification)
            .where(InboxNotification.user_id == customer.id)
        )
        == 1
    )


async def test_financial_event_triggers_are_neutral_and_idempotent(
    db_session: AsyncSession,
) -> None:
    from decimal import Decimal

    from app.models import Invoice, PaymentIntent, Wallet, Withdrawal
    from app.models.enums import (
        InvoiceStatus,
        PaymentIntentStatus,
        PaymentPurpose,
        WalletType,
        WithdrawalStatus,
    )

    customer, courier = await participants(db_session)
    order = await order_for(db_session, customer)
    order.courier_id = courier.id
    order.status = OrderStatus.ASSIGNED
    invoice = Invoice(
        order_id=order.id,
        issued_by_courier_id=courier.id,
        items_net_amount=Decimal("50.00"),
        net_after_discount_amount=Decimal("50.00"),
        total_amount=Decimal("50.00"),
        status=InvoiceStatus.ISSUED,
    )
    topup = PaymentIntent(
        user_id=customer.id,
        purpose=PaymentPurpose.WALLET_TOPUP,
        amount=Decimal("100.00"),
        status=PaymentIntentStatus.PAID,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    wallet = Wallet(user_id=courier.id, type=WalletType.COURIER)
    db_session.add_all([invoice, topup, wallet])
    await db_session.flush()
    withdrawal = Withdrawal(
        courier_id=courier.id,
        wallet_id=wallet.id,
        amount=Decimal("50.00"),
        iban_encrypted="test-fake-no-personal-data",
        iban_last4="0000",
    )
    db_session.add(withdrawal)
    await db_session.flush()
    withdrawal.status = WithdrawalStatus.SUBMITTED
    await db_session.flush()
    await db_session.execute(
        update(Invoice).where(Invoice.id == invoice.id).values(status=InvoiceStatus.ISSUED)
    )
    await db_session.execute(
        update(PaymentIntent)
        .where(PaymentIntent.id == topup.id)
        .values(status=PaymentIntentStatus.PAID)
    )
    notifications = list(
        await db_session.scalars(
            select(InboxNotification).where(InboxNotification.user_id == customer.id)
        )
    )
    assert [n.notification_type for n in notifications].count("INVOICE_ISSUED") == 1
    assert [n.notification_type for n in notifications].count("WALLET_TOPUP_COMPLETED") == 1
    courier_notifications = list(
        await db_session.scalars(
            select(InboxNotification).where(
                InboxNotification.user_id == courier.id,
                InboxNotification.notification_type == "WITHDRAWAL_STATUS_CHANGED",
            )
        )
    )
    assert sorted(n.status for n in courier_notifications) == ["REQUESTED", "SUBMITTED"]
    assert all(n.destination_kind == "WALLET" for n in courier_notifications)


async def test_api_owned_inbox_no_store_and_current_eligibility(db_session: AsyncSession) -> None:
    from app.core.deps import Actor, get_db, require_auth
    from app.main import create_app
    from app.models.enums import UserStatus
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import make_test_settings

    customer, courier = await participants(db_session)
    order = await order_for(db_session, customer)
    app = create_app(make_test_settings())
    actor = Actor(id=customer.id, role=UserRole.CUSTOMER, jti="test")

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_auth] = lambda: actor
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        timeline = await client.get(f"/api/orders/{order.id}/timeline?limit=1")
        assert timeline.status_code == 200
        assert timeline.headers["cache-control"] == "private, no-store"
        assert timeline.json()["items"][0]["status"] == "NEW"
        assert timeline.json()["history_complete"] is True
        assert (await client.get(f"/api/orders/{order.id}/timeline?cursor=bad")).status_code == 422
        page = await client.get("/api/notifications?unread_only=true&limit=1")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "private, no-store"
        identifier = page.json()["items"][0]["id"]
        assert (await client.post(f"/api/notifications/{identifier}/read")).status_code == 200
        assert (await client.get("/api/notifications/unread-count")).json() == {"unread_count": 0}
        assert (await client.post("/api/notifications/read-all")).json()["marked_count"] == 0
        assert (await client.get("/api/notifications?limit=101")).status_code == 422
        assert (await client.get("/api/notifications?cursor=bad")).status_code == 422
        actor = Actor(id=courier.id, role=UserRole.COURIER, jti="test")
        assert (await client.get("/api/notifications")).status_code == 403
        actor = Actor(id=customer.id, role=UserRole.ADMIN, jti="test")
        assert (await client.get("/api/notifications")).status_code == 403
        actor = Actor(id=customer.id, role=UserRole.CUSTOMER, jti="test")
        customer.status = UserStatus.BANNED
        await db_session.flush()
        assert (await client.get("/api/notifications")).status_code == 403


async def test_reminder_edit_invalidates_claim(db_session: AsyncSession) -> None:
    customer, _ = await participants(db_session)
    service = OccasionService(PlanningRepository(db_session))
    now = datetime.now(UTC)
    occasion = await service.create(
        customer.id,
        title="Private",
        occasion_date=now.date(),
        reminder_days_before=0,
        reminder_enabled=True,
    )
    identifier = occasion.id
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == identifier)
        .values(scheduled_at=now - timedelta(minutes=1))
    )
    repo = NotificationReminderRepository(db_session)
    await repo.materialize_due(now=now)
    claim = await repo.claim(now=now)
    assert claim is not None
    await service.update(
        customer.id,
        identifier,
        title=None,
        occasion_date=None,
        reminder_days_before=None,
        reminder_enabled=False,
    )
    assert await repo.token_page(claim) is None
    await repo.finish(claim, now=now, cursor=None, complete=True)
    db_session.expire_all()
    schedule = await db_session.get(OccasionReminder, identifier)
    assert schedule is not None and not schedule.enabled
    assert schedule.push_completed_at is None and schedule.version == claim.version + 1


async def test_worker_no_device_and_push_failure_preserve_inbox(db_session: AsyncSession) -> None:
    from app.core.config import Environment
    from app.integrations.push.fake import FakePushClient
    from app.models import DeviceToken
    from app.models.enums import DeviceOs
    from app.services.notification_reminder_service import send_occasion_reminders
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.conftest import make_test_settings

    customer, _ = await participants(db_session)
    now = datetime.now(UTC)
    occasion = await OccasionService(PlanningRepository(db_session)).create(
        customer.id,
        title="Never sent as text",
        occasion_date=now.date(),
        reminder_days_before=0,
        reminder_enabled=True,
    )
    identifier = occasion.id
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == identifier)
        .values(scheduled_at=now - timedelta(minutes=1))
    )
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    push = FakePushClient(Environment.TEST)
    assert (
        await send_occasion_reminders(factory=factory, push=push, settings=make_test_settings())
        == 1
    )
    assert not push.sent
    db_session.add(
        DeviceToken(user_id=customer.id, device_os=DeviceOs.IOS, token="fake-test-token")
    )
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == identifier)
        .values(push_completed_at=None, next_attempt_at=now)
    )
    await db_session.flush()
    failing = AsyncMock()
    failing.send_push.side_effect = TimeoutError("test failure")
    assert (
        await send_occasion_reminders(factory=factory, push=failing, settings=make_test_settings())
        == 0
    )
    inbox_count = await db_session.scalar(
        select(func.count())
        .select_from(InboxNotification)
        .where(InboxNotification.user_id == customer.id)
    )
    assert inbox_count == 1
    schedule = await db_session.get(OccasionReminder, identifier)
    await db_session.refresh(schedule)
    assert schedule.push_attempts == 1 and schedule.push_completed_at is None
    assert schedule.next_attempt_at > now
    args = failing.send_push.await_args.args
    assert "Never sent as text" not in repr(args)


async def test_concurrent_workers_create_one_inbox_and_one_push(db_connection) -> None:
    import asyncio

    from app.core.config import Environment
    from app.integrations.push.fake import FakePushClient
    from app.models import DeviceToken
    from app.models.enums import DeviceOs
    from app.services.notification_reminder_service import send_occasion_reminders
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.conftest import make_test_settings

    factory = async_sessionmaker(bind=db_connection.engine, expire_on_commit=False)
    async with factory() as session:
        customer, _ = await participants(session)
        owner_id = customer.id
        other_user_id = _.id
        now = datetime.now(UTC)
        occasion = await OccasionService(PlanningRepository(session)).create(
            owner_id,
            title="Private",
            occasion_date=now.date(),
            reminder_days_before=0,
            reminder_enabled=True,
        )
        session.add(DeviceToken(user_id=owner_id, device_os=DeviceOs.IOS, token=f"test-{uuid4()}"))
        await session.execute(
            update(OccasionReminder)
            .where(OccasionReminder.occasion_id == occasion.id)
            .values(scheduled_at=now - timedelta(minutes=1))
        )
        await session.commit()

    push = FakePushClient(Environment.TEST)
    try:
        await asyncio.gather(
            *[
                send_occasion_reminders(factory=factory, push=push, settings=make_test_settings())
                for _ in range(2)
            ]
        )
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(InboxNotification)
                    .where(InboxNotification.user_id == owner_id)
                )
                == 1
            )
        assert len(push.sent) == 1
        assert push.sent[0].data == {
            "type": "OCCASION_REMINDER",
            "kind": "OCCASION",
            "occasion_id": str(occasion.id),
        }
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id.in_([owner_id, other_user_id])))
            await session.commit()


async def test_read_all_cutoff_excludes_later_events(db_session: AsyncSession) -> None:
    customer, _ = await participants(db_session)
    first = await order_for(db_session, customer)
    later = InboxNotification(
        user_id=customer.id,
        event_key="later-event",
        notification_type="ORDER_STATUS_CHANGED",
        destination_kind="ORDER",
        resource_id=first.id,
        status="NEW",
        created_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    db_session.add(later)
    await db_session.flush()
    count, cutoff = await NotificationInboxRepository(db_session).mark_all(customer.id)
    assert count == 1 and cutoff < later.created_at
    await db_session.refresh(later)
    assert later.read_at is None


async def test_inbox_page_query_count_does_not_grow_with_items(db_session: AsyncSession) -> None:
    from app.repositories.courier_repository import CourierRepository
    from app.repositories.user_repository import UserRepository
    from app.services.courier_eligibility_service import CourierEligibilityService
    from sqlalchemy import event

    customer, _ = await participants(db_session)
    for _ in range(30):
        await order_for(db_session, customer)
    service = NotificationInboxService(
        NotificationInboxRepository(db_session),
        CourierEligibilityService(
            users=UserRepository(db_session), couriers=CourierRepository(db_session)
        ),
    )
    statements = []

    def count_selects(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db_session.sync_session.get_bind().engine
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        single = await service.list(customer.id, unread_only=False, limit=1, cursor=None)
        one_count = len(statements)
        statements.clear()
        many = await service.list(customer.id, unread_only=False, limit=25, cursor=None)
        assert len(single.items) == 1 and len(many.items) == 25
        assert len(statements) == one_count == 3
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
