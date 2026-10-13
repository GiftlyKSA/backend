"""Persistence and concurrency fences for Expo receipt delivery."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    String,
    case,
    column,
    delete,
    or_,
    select,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.push.expo import DeviceSnapshot
from app.models import CourierProfile, DeviceToken, Order, OrderNotificationRecipient, User
from app.models.enums import OrderStatus, UserRole, UserStatus
from app.models.expo_push_receipt import ExpoPushReceipt


@dataclass(frozen=True)
class ReceiptClaim:
    """Detached receipt identity with its current worker lease."""

    ticket_id: str
    snapshot: DeviceSnapshot
    lease_id: uuid.UUID
    attempts: int
    expires_at: datetime


_logger = logging.getLogger(__name__)


class ExpoReceiptRepository:
    """Keep database transactions short and fence every worker completion."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind a caller-owned transaction."""
        self._session = session

    async def snapshot_devices(
        self, tokens: list[str], *, order_id: uuid.UUID | None = None
    ) -> dict[str, DeviceSnapshot]:
        """Read only one send chunk of current device registrations."""
        if len(tokens) > 100:
            raise ValueError("Device snapshot chunk exceeds 100.")
        query = select(DeviceToken.id, DeviceToken.token, DeviceToken.last_seen_at).where(
            DeviceToken.token.in_(tokens)
        )
        if order_id is not None:
            query = (
                query.join(User, User.id == DeviceToken.user_id)
                .join(CourierProfile, CourierProfile.user_id == User.id)
                .join(
                    OrderNotificationRecipient,
                    (OrderNotificationRecipient.token_id == DeviceToken.id)
                    & (OrderNotificationRecipient.user_id == DeviceToken.user_id),
                )
                .join(Order, Order.id == OrderNotificationRecipient.order_id)
                .where(
                    Order.id == order_id,
                    Order.status == OrderStatus.NEW,
                    CourierProfile.city_of_residence_id == Order.delivery_city_id,
                    CourierProfile.is_verified.is_(True),
                    User.role == UserRole.COURIER,
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        rows = await self._session.execute(query)
        return {row.token: DeviceSnapshot(row.id, row.token, row.last_seen_at) for row in rows}

    async def save_tickets(
        self, tickets: list[tuple[str, DeviceSnapshot]], *, now: datetime
    ) -> None:
        """Insert accepted tickets idempotently without retaining notification content."""
        if not tickets:
            return
        await self._session.execute(
            insert(ExpoPushReceipt)
            .values(
                [
                    {
                        "ticket_id": ticket_id,
                        "device_id": snapshot.device_id,
                        "token": snapshot.token,
                        "device_last_seen_at": snapshot.last_seen_at,
                        "available_at": now + timedelta(minutes=15),
                        "expires_at": now + timedelta(hours=24),
                    }
                    for ticket_id, snapshot in tickets
                ]
            )
            .on_conflict_do_nothing(index_elements=["ticket_id"])
        )

    async def remove_invalid(self, snapshot: DeviceSnapshot) -> None:
        """Do not delete a device refreshed or reassigned after the send snapshot."""
        await self._session.execute(
            delete(DeviceToken).where(
                DeviceToken.id == snapshot.device_id,
                DeviceToken.token == snapshot.token,
                DeviceToken.last_seen_at == snapshot.last_seen_at,
            )
        )

    async def claim_pending(self, *, now: datetime, limit: int = 100) -> list[ReceiptClaim]:
        """Lease a bounded batch and retire expired or exhausted tickets."""
        rows = await self._session.scalars(
            select(ExpoPushReceipt)
            .where(
                ExpoPushReceipt.completed_at.is_(None),
                ExpoPushReceipt.available_at <= now,
                or_(ExpoPushReceipt.leased_until.is_(None), ExpoPushReceipt.leased_until <= now),
            )
            .order_by(ExpoPushReceipt.available_at)
            .limit(min(limit, 100))
            .with_for_update(skip_locked=True)
        )
        claims = []
        for row in rows:
            if row.expires_at <= now or row.attempts >= 32:
                row.completed_at = now
                row.error_code = "ReceiptExpired" if row.expires_at <= now else "RetryExhausted"
                row.token = ""
                _logger.warning("Expo receipt recovery ended (%s)", row.error_code)
                continue
            row.attempts += 1
            row.lease_id = uuid.uuid4()
            row.leased_until = now + timedelta(seconds=60)
            claims.append(
                ReceiptClaim(
                    row.ticket_id,
                    DeviceSnapshot(row.device_id, row.token, row.device_last_seen_at),
                    row.lease_id,
                    row.attempts,
                    row.expires_at,
                )
            )
        await self._session.flush()
        return claims

    async def finish(
        self,
        claim: ReceiptClaim,
        *,
        now: datetime,
        error_code: str | None,
        retry: bool = False,
        invalid_device: bool = False,
    ) -> None:
        """Finish one claim through the same fenced batch path."""
        await self.finish_batch(
            [(claim, error_code, retry, invalid_device)],
            now=now,
        )

    async def finish_batch(
        self,
        outcomes: list[tuple[ReceiptClaim, str | None, bool, bool]],
        *,
        now: datetime,
    ) -> None:
        """Update a bounded batch in one statement, deleting only matched invalid devices."""
        if not outcomes:
            return
        if len(outcomes) > 100:
            raise ValueError("Receipt completion batch exceeds 100.")
        updates = values(
            column("ticket_id", String(256)),
            column("lease_id", UUID(as_uuid=True)),
            column("retry", Boolean()),
            column("error_code", String(64)),
            column("available_at", DateTime(timezone=True)),
            name="receipt_updates",
        ).data(
            [
                (
                    claim.ticket_id,
                    claim.lease_id,
                    retry,
                    code,
                    min(
                        now + timedelta(seconds=min(3600, 60 * 2**claim.attempts)), claim.expires_at
                    ),
                )
                for claim, code, retry, _invalid in outcomes
            ]
        )
        matched = set(
            await self._session.scalars(
                update(ExpoPushReceipt)
                .where(
                    ExpoPushReceipt.ticket_id == updates.c.ticket_id,
                    ExpoPushReceipt.lease_id == updates.c.lease_id,
                    ExpoPushReceipt.completed_at.is_(None),
                    ExpoPushReceipt.leased_until > now,
                )
                .values(
                    lease_id=None,
                    leased_until=None,
                    updated_at=now,
                    completed_at=case((updates.c.retry, None), else_=now),
                    token=case((updates.c.retry, ExpoPushReceipt.token), else_=""),
                    error_code=updates.c.error_code,
                    available_at=updates.c.available_at,
                )
                .returning(ExpoPushReceipt.ticket_id)
                .execution_options(synchronize_session=False)
            )
        )
        invalid = [
            claim.snapshot
            for claim, _code, _retry, is_invalid in outcomes
            if is_invalid and claim.ticket_id in matched
        ]
        await self.remove_invalid_batch(invalid)

    async def remove_invalid_batch(self, snapshots: list[DeviceSnapshot]) -> None:
        """Delete invalid registrations with every original snapshot fence in one query."""
        if not snapshots:
            return
        if len(snapshots) > 100:
            raise ValueError("Invalid device batch exceeds 100.")
        await self._session.execute(
            delete(DeviceToken)
            .where(
                tuple_(DeviceToken.id, DeviceToken.token, DeviceToken.last_seen_at).in_(
                    [
                        (snapshot.device_id, snapshot.token, snapshot.last_seen_at)
                        for snapshot in snapshots
                    ]
                )
            )
            .execution_options(synchronize_session=False)
        )
