"""Reminder materialization keeps bounded SQL and existing delivery decisions."""

from datetime import UTC, date, datetime, timedelta

import pytest
from app.models.notification_models import InboxNotification, OccasionReminder
from app.repositories.notification_reminder_repository import NotificationReminderRepository
from app.repositories.planning_repository import PlanningRepository
from app.services.occasion_service import OccasionService
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_mobile_notifications import participants


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
