"""Reminder materialization keeps bounded SQL and existing delivery decisions."""

from datetime import UTC, date, datetime, time, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import DeviceToken
from app.models.enums import DeviceOs, UserRole, UserStatus
from app.models.notification_models import InboxNotification, OccasionReminder
from app.repositories.notification_reminder_repository import NotificationReminderRepository
from app.repositories.planning_repository import PlanningRepository
from app.services.occasion_service import OccasionService
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_mobile_notifications import participants


@pytest.mark.parametrize("change", ["owner", "role", "status", "disabled", "deleted"])
async def test_reminder_claim_rechecks_current_recipient(
    db_session: AsyncSession, change: str
) -> None:
    customer, replacement = await participants(db_session)
    replacement.role = UserRole.CUSTOMER
    old_token = DeviceToken(user_id=customer.id, token=str(uuid4()), device_os=DeviceOs.IOS)
    new_token = DeviceToken(user_id=replacement.id, token=str(uuid4()), device_os=DeviceOs.IOS)
    db_session.add_all([old_token, new_token])
    now = datetime.now(UTC)
    scheduled_at = datetime.combine(now.date(), time(6), tzinfo=UTC)
    if scheduled_at > now:
        scheduled_at -= timedelta(days=1)
    occasion = await OccasionService(PlanningRepository(db_session)).create(
        customer.id,
        title="Reminder recipient",
        occasion_date=scheduled_at.date(),
        reminder_days_before=0,
        reminder_enabled=True,
    )
    repo = NotificationReminderRepository(db_session)
    assert await repo.materialize_due(now=now) == 1
    stale = await repo.claim(now=now)
    assert stale is not None
    assert await repo.token_page(stale) == [(old_token.id, old_token.token)]

    if change == "owner":
        occasion.user_id = replacement.id
    elif change == "role":
        customer.role = UserRole.COURIER
    elif change == "status":
        customer.status = UserStatus.BANNED
    elif change == "disabled":
        occasion.reminder_enabled = False
    else:
        await db_session.delete(occasion)
    await db_session.flush()
    assert await repo.token_page(stale) is None

    if change == "owner":
        await db_session.refresh(occasion)
        assert occasion.reminder_version == stale.version + 1
        await repo.finish(stale, now=now, cursor=None, complete=True)
        assert await repo.materialize_due(now=now) == 1
        fresh = await repo.claim(now=now)
        assert fresh is not None and fresh.user_id == replacement.id
        assert await repo.token_page(fresh) == [(new_token.id, new_token.token)]


async def test_owner_fence_migration_preserves_schedule_on_downgrade(db_session) -> None:
    migration = import_module("app.migrations.versions.0044_reminder_owner_fence")

    def run(session, action) -> None:
        with Operations.context(MigrationContext.configure(session.connection())):
            action()

    customer, replacement = await participants(db_session)
    replacement.role = UserRole.CUSTOMER
    occasion = await OccasionService(PlanningRepository(db_session)).create(
        customer.id,
        title="Existing schedule",
        occasion_date=date(2027, 3, 1),
        reminder_days_before=0,
        reminder_enabled=True,
    )
    version = occasion.reminder_version
    await db_session.run_sync(run, migration.upgrade)
    occasion.user_id = replacement.id
    await db_session.flush()
    await db_session.refresh(occasion)
    assert occasion.reminder_version == version + 1

    await db_session.run_sync(run, migration.downgrade)
    occasion.user_id = customer.id
    await db_session.flush()
    await db_session.refresh(occasion)
    assert occasion.reminder_version == version + 1
    reminder = await db_session.get(OccasionReminder, occasion.id)
    assert reminder is not None and reminder.version == version + 1


@pytest.mark.parametrize("batch_size", [1, 10, 100])
async def test_materialization_uses_one_insert_and_preserves_duplicate_and_expired_decisions(
    db_session: AsyncSession, batch_size: int
) -> None:
    customer, _ = await participants(db_session)
    service = OccasionService(PlanningRepository(db_session))
    now = datetime(2027, 2, 1, 6, tzinfo=UTC)
    schedules = []
    for index in range(batch_size):
        occasion = await service.create(
            customer.id,
            title=f"Event {index}",
            occasion_date=date(2027, 2, 1),
            reminder_days_before=0,
            reminder_enabled=True,
        )
        reminder = await db_session.get(OccasionReminder, occasion.id)
        assert reminder is not None
        schedules.append(reminder)
    duplicate = schedules[0]
    db_session.add(
        InboxNotification(
            user_id=customer.id,
            event_key=f"occasion:{duplicate.occasion_id}:{duplicate.occurrence_date.isoformat()}",
            notification_type="OCCASION_REMINDER",
            destination_kind="OCCASION",
            resource_id=duplicate.occasion_id,
            created_at=now,
        )
    )
    if batch_size > 1:
        schedules[-1].scheduled_at = now - timedelta(days=1)
    await db_session.flush()
    connection = await db_session.connection()
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO INBOX_NOTIFICATIONS"):
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        repo = NotificationReminderRepository(db_session)
        assert await repo.materialize_due(now=now, limit=batch_size) == batch_size
        assert len(statements) == 1
        assert await repo.materialize_due(now=now, limit=batch_size) == 0
        assert len(statements) == 1
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
    assert duplicate.push_completed_at == now
    if batch_size > 1:
        assert schedules[-1].push_completed_at == now
    for reminder in schedules:
        assert reminder.delivered_at == now and reminder.next_attempt_at == now
        if reminder not in (duplicate, schedules[-1]):
            assert reminder.push_completed_at is None
    inbox = list(
        await db_session.scalars(
            select(InboxNotification).where(InboxNotification.user_id == customer.id)
        )
    )
    assert len(inbox) == batch_size
