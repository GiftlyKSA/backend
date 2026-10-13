"""Native PostgreSQL annual calendar and edit-fenced reminder regressions."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError, NotFoundError, ValidationDomainError
from app.models import User
from app.models.enums import UserRole
from app.models.notification_models import InboxNotification, OccasionReminder
from app.repositories.notification_reminder_repository import NotificationReminderRepository
from app.repositories.occasion_occurrence_repository import OccasionOccurrenceRepository
from app.repositories.planning_repository import PlanningRepository
from app.services.occasion_occurrence_service import OccasionOccurrenceService
from app.services.occasion_service import OccasionService
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession


async def owner(session):
    row = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    session.add(row)
    await session.flush()
    return row


async def occasion(session, user, title, day, recurrence="ANNUAL", enabled=False):
    return await OccasionService(PlanningRepository(session)).create(
        user.id,
        title=title,
        occasion_date=day,
        reminder_days_before=7,
        reminder_enabled=enabled,
        recurrence=recurrence,
    )


async def test_owned_occurrences_date_bounds_pages_and_leap_semantics(db_session: AsyncSession):
    first = await owner(db_session)
    foreign = await owner(db_session)
    annual = await occasion(db_session, first, "Leap", date(2020, 2, 29))
    oneoff = await occasion(db_session, first, "Once", date(2028, 2, 29), "NONE")
    await occasion(db_session, foreign, "Other", date(2020, 2, 29))
    service = OccasionOccurrenceService(OccasionOccurrenceRepository(db_session))
    page = await service.list(
        first.id, start=date(2028, 2, 29), end=date(2028, 2, 29), limit=1, cursor=None
    )
    assert len(page.items) == 1 and page.next_cursor
    next_page = await service.list(
        first.id, start=date(2028, 2, 29), end=date(2028, 2, 29), limit=1, cursor=page.next_cursor
    )
    assert len(next_page.items) == 1 and next_page.next_cursor is None
    assert {page.items[0].occasion_id, next_page.items[0].occasion_id} == {annual.id, oneoff.id}
    assert page.items[0].occurrence_date == date(2028, 2, 29)
    with pytest.raises(NotFoundError):
        await service.list(
            foreign.id,
            start=date(2028, 2, 29),
            end=date(2028, 2, 29),
            limit=1,
            cursor=page.next_cursor,
        )
    with pytest.raises(NotFoundError):
        await service.list(
            first.id, start=date(2029, 2, 1), end=date(2029, 3, 1), limit=1, cursor=page.next_cursor
        )
    empty = await service.list(
        first.id, start=date(2026, 1, 1), end=date(2026, 12, 31), limit=25, cursor=None
    )
    assert empty.items == [] and empty.next_cursor is None


async def test_calendar_rejects_invalid_cursor_and_unbounded_range(db_session: AsyncSession):
    service = OccasionOccurrenceService(OccasionOccurrenceRepository(db_session))
    with pytest.raises(BadRequestError):
        await service.list(
            uuid4(), start=date(2026, 1, 1), end=date(2026, 1, 2), limit=25, cursor="junk!"
        )
    with pytest.raises(ValidationDomainError):
        await service.list(
            uuid4(), start=date(2025, 1, 1), end=date(2026, 1, 2), limit=25, cursor=None
        )


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), date(2028, 2, 29)),
        (datetime(2099, 1, 1, tzinfo=UTC), date(2104, 2, 29)),
        (datetime(2028, 2, 22, 6, tzinfo=UTC), date(2028, 2, 29)),
        (datetime(2028, 2, 22, 6, 0, 1, tzinfo=UTC), date(2032, 2, 29)),
    ],
)
async def test_next_annual_schedule_uses_riyadh_and_leap_years(db_session, reference, expected):
    result = await db_session.scalar(
        text("SELECT giftly_next_annual_date(CAST(:anchor AS date),7,CAST(:now AS timestamptz))"),
        {"anchor": date(2020, 2, 29), "now": reference},
    )
    assert result == expected


async def test_one_off_dates_unchanged_and_annual_edits_reset_lease(db_session):
    user = await owner(db_session)
    single = await occasion(db_session, user, "Once", date(2027, 1, 20), "NONE", True)
    reminder = await db_session.get(OccasionReminder, single.id)
    assert reminder.occurrence_date == date(2027, 1, 20)
    assert reminder.scheduled_at == datetime(2027, 1, 13, 6, tzinfo=UTC)
    annual = await occasion(db_session, user, "Annual", date(2020, 2, 29), enabled=True)
    record = await db_session.get(OccasionReminder, annual.id)
    assert record.occurrence_date.year % 4 == 0 and record.version == 1
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == annual.id)
        .values(lease_token=uuid4(), lease_expires_at=datetime.now(UTC) + timedelta(minutes=1))
    )
    await OccasionService(PlanningRepository(db_session)).update(
        user.id,
        annual.id,
        title=None,
        occasion_date=None,
        reminder_days_before=None,
        reminder_enabled=False,
    )
    await db_session.refresh(record)
    assert record.version == 2 and not record.enabled and record.lease_token is None
    with pytest.raises(NotFoundError):
        await OccasionService(PlanningRepository(db_session)).delete(uuid4(), annual.id)


async def test_annual_reminder_dedup_and_next_year_progress(db_session):
    user = await owner(db_session)
    saved = await occasion(db_session, user, "Annual", date(2020, 10, 13), enabled=True)
    now = datetime(2026, 10, 6, 6, tzinfo=UTC)
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == saved.id)
        .values(
            occurrence_date=date(2026, 10, 13),
            scheduled_at=now,
            enabled=True,
            delivered_at=None,
            push_completed_at=None,
        )
    )
    repo = NotificationReminderRepository(db_session)
    await repo.materialize_due(now=now)
    await repo.materialize_due(now=now)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InboxNotification)
            .where(InboxNotification.user_id == user.id)
        )
        == 1
    )
    await db_session.execute(
        update(OccasionReminder)
        .where(OccasionReminder.occasion_id == saved.id)
        .values(push_completed_at=now)
    )
    await repo.advance_annual(now=now)
    record = await db_session.get(OccasionReminder, saved.id, populate_existing=True)
    assert record.occurrence_date == date(2027, 10, 13)
    assert record.scheduled_at == datetime(2027, 10, 6, 6, tzinfo=UTC)
    assert record.delivered_at is None and record.push_attempts == 0


async def test_delete_cascades_future_reminder(db_session):
    user = await owner(db_session)
    saved = await occasion(db_session, user, "Annual", date(2020, 10, 13), enabled=True)
    key = saved.id
    await OccasionService(PlanningRepository(db_session)).delete(user.id, key)
    assert (
        await db_session.scalar(
            select(OccasionReminder.occasion_id).where(OccasionReminder.occasion_id == key)
        )
        is None
    )


async def test_future_anchor_and_century_year_are_not_exposed(db_session):
    user = await owner(db_session)
    await occasion(db_session, user, "Future", date(2104, 2, 29))
    await occasion(db_session, user, "Leap", date(2020, 2, 29))
    page = await OccasionOccurrenceService(OccasionOccurrenceRepository(db_session)).list(
        user.id, start=date(2100, 1, 1), end=date(2100, 12, 31), limit=25, cursor=None
    )
    assert page.items == []
