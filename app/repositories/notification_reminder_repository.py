"""Bounded transactional reminder materialization and fenced push leases."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Occasion, User
from app.models.enums import UserRole, UserStatus
from app.models.notification_models import InboxNotification, OccasionReminder
from app.repositories.device_token_repository import DeviceTokenRepository


@dataclass(frozen=True)
class ReminderClaim:
    """A version and lease token fence for one device page."""

    occasion_id: UUID
    user_id: UUID
    version: int
    lease_token: UUID
    cursor_token_id: UUID | None
    scheduled_at: datetime


class NotificationReminderRepository:
    """Keep network calls outside the owned occasion/outbox transactions."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the transaction."""
        self._session = session

    async def materialize_due(self, *, now: datetime, limit: int = 100) -> int:
        """Insert the in-app event even when there are no registered devices."""
        eligible = exists().where(
            User.id == Occasion.user_id,
            User.role == UserRole.CUSTOMER,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
        rows = (
            await self._session.execute(
                select(OccasionReminder, Occasion)
                .join(Occasion, Occasion.id == OccasionReminder.occasion_id)
                .where(
                    OccasionReminder.enabled.is_(True),
                    Occasion.reminder_enabled.is_(True),
                    OccasionReminder.version == Occasion.reminder_version,
                    OccasionReminder.delivered_at.is_(None),
                    OccasionReminder.scheduled_at <= now,
                    eligible,
                )
                .order_by(OccasionReminder.scheduled_at, OccasionReminder.occasion_id)
                .limit(limit)
                .with_for_update(skip_locked=True, of=(Occasion, OccasionReminder))
            )
        ).all()
        for reminder, occasion in rows:
            statement = (
                insert(InboxNotification)
                .values(
                    user_id=occasion.user_id,
                    event_key=f"occasion:{occasion.id}:{occasion.occasion_date.isoformat()}",
                    notification_type="OCCASION_REMINDER",
                    destination_kind="OCCASION",
                    resource_id=occasion.id,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_inbox_notification_event")
                .returning(InboxNotification.id)
            )
            inserted = await self._session.scalar(statement)
            reminder.delivered_at = now
            reminder.next_attempt_at = now
            if inserted is None or now >= reminder.scheduled_at + timedelta(days=1):
                reminder.push_completed_at = now
        await self._session.flush()
        return len(rows)

    async def retire_pending(self, *, now: datetime, limit: int = 100) -> None:
        """Retire a bounded expired/exhausted batch so stale intents do not block new ones."""
        active_customer = exists().where(
            Occasion.id == OccasionReminder.occasion_id,
            User.id == Occasion.user_id,
            User.role == UserRole.CUSTOMER,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
        expired = (
            (OccasionReminder.scheduled_at <= now - timedelta(days=1))
            | (OccasionReminder.push_attempts >= 5)
            | ~active_customer
        )
        identifiers = (
            select(OccasionReminder.occasion_id)
            .where(
                OccasionReminder.enabled.is_(True),
                OccasionReminder.delivered_at.is_not(None),
                OccasionReminder.push_completed_at.is_(None),
                expired,
            )
            .order_by(OccasionReminder.next_attempt_at, OccasionReminder.occasion_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        await self._session.execute(
            update(OccasionReminder)
            .where(
                OccasionReminder.occasion_id.in_(identifiers),
                expired,
                OccasionReminder.push_completed_at.is_(None),
            )
            .values(push_completed_at=now, lease_token=None, lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )

    async def claim(self, *, now: datetime) -> ReminderClaim | None:
        """Lease one eligible device page after the bounded retirement sweep."""
        active_customer = exists().where(
            User.id == Occasion.user_id,
            User.role == UserRole.CUSTOMER,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
        row = (
            await self._session.execute(
                select(OccasionReminder, Occasion)
                .join(Occasion, Occasion.id == OccasionReminder.occasion_id)
                .where(
                    active_customer,
                    OccasionReminder.push_attempts < 5,
                    OccasionReminder.scheduled_at > now - timedelta(days=1),
                    OccasionReminder.enabled.is_(True),
                    Occasion.reminder_enabled.is_(True),
                    OccasionReminder.version == Occasion.reminder_version,
                    OccasionReminder.delivered_at.is_not(None),
                    OccasionReminder.push_completed_at.is_(None),
                    OccasionReminder.next_attempt_at <= now,
                    (
                        OccasionReminder.lease_expires_at.is_(None)
                        | (OccasionReminder.lease_expires_at <= now)
                    ),
                )
                .order_by(OccasionReminder.next_attempt_at, OccasionReminder.occasion_id)
                .limit(1)
                .with_for_update(skip_locked=True, of=(Occasion, OccasionReminder))
            )
        ).first()
        if row is None:
            return None
        reminder, occasion = row
        lease = uuid4()
        reminder.lease_token = lease
        reminder.lease_expires_at = now + timedelta(seconds=60)
        # Count leased attempts too: process crashes must not cause unlimited retry.
        reminder.push_attempts += 1
        await self._session.flush()
        return ReminderClaim(
            occasion.id,
            occasion.user_id,
            reminder.version,
            lease,
            reminder.cursor_token_id,
            reminder.scheduled_at,
        )

    async def token_page(self, claim: ReminderClaim) -> list[tuple[UUID, str]] | None:
        """Recheck the current schedule fence and active customer before external delivery."""
        valid = await self._session.scalar(
            select(OccasionReminder.occasion_id)
            .join(Occasion, Occasion.id == OccasionReminder.occasion_id)
            .join(User, User.id == Occasion.user_id)
            .where(
                OccasionReminder.occasion_id == claim.occasion_id,
                OccasionReminder.version == claim.version,
                OccasionReminder.lease_token == claim.lease_token,
                OccasionReminder.lease_expires_at > datetime.now(UTC),
                OccasionReminder.enabled.is_(True),
                Occasion.reminder_enabled.is_(True),
                Occasion.reminder_version == claim.version,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
        if valid is None:
            return None
        return await DeviceTokenRepository(self._session).token_page(
            user_id=claim.user_id, after=claim.cursor_token_id, limit=500
        )

    async def finish(
        self,
        claim: ReminderClaim,
        *,
        now: datetime,
        cursor: UUID | None,
        complete: bool,
        failed: bool = False,
    ) -> None:
        """Only the current lease can advance delivery after an edit or another worker."""
        attempts = await self._session.scalar(
            select(OccasionReminder.push_attempts).where(
                OccasionReminder.occasion_id == claim.occasion_id,
                OccasionReminder.version == claim.version,
                OccasionReminder.lease_token == claim.lease_token,
            )
        )
        if attempts is None:
            return
        values: dict[str, object] = {
            "lease_token": None,
            "lease_expires_at": None,
            "next_attempt_at": now + timedelta(seconds=30 * 2 ** int(attempts)) if failed else now,
        }
        if not failed:
            values["cursor_token_id"] = cursor
            # Page successes reset failure/crash attempts; each page remains bounded.
            values["push_attempts"] = 0
        if complete:
            values["push_completed_at"] = now
        await self._session.execute(
            update(OccasionReminder)
            .where(
                OccasionReminder.occasion_id == claim.occasion_id,
                OccasionReminder.version == claim.version,
                OccasionReminder.lease_token == claim.lease_token,
                OccasionReminder.lease_expires_at > now,
            )
            .values(**values)
        )
