"""Bounded, fenced delivery of durable chat push intents."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatNotification, DeviceToken, User
from app.models.enums import UserStatus

_MAX_ATTEMPTS = 8
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedChatNotification:
    """A push intent with its lease and completed recipient-page cursor."""

    message_id: uuid.UUID
    recipient_id: uuid.UUID
    cursor_token_id: uuid.UUID | None
    lease_id: uuid.UUID
    attempts: int


@dataclass(frozen=True)
class ChatNotificationClaimBatch:
    """Separate retired rows from an empty delivery queue."""

    claims: list[ClaimedChatNotification]
    retired_count: int


class ChatNotificationRepository:
    """Persist short delivery claims without locks across external calls."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the delivery session."""
        self._session = session

    async def claim_pending(
        self, *, now: datetime, limit: int, lease_seconds: int
    ) -> ChatNotificationClaimBatch:
        """Lease ready rows, skipping overlapping workers and exhausted retries."""
        rows = await self._session.scalars(
            select(ChatNotification)
            .where(
                ChatNotification.completed_at.is_(None),
                ChatNotification.failed_at.is_(None),
                ChatNotification.available_at <= now,
                or_(
                    ChatNotification.leased_until.is_(None),
                    ChatNotification.leased_until <= now,
                ),
            )
            .order_by(ChatNotification.available_at, ChatNotification.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        claims = []
        retired_count = 0
        for row in rows:
            if row.attempts >= _MAX_ATTEMPTS:
                retired_count += 1
                row.failed_at = now
                row.lease_id = None
                row.leased_until = None
                row.updated_at = now
                _logger.error("chat notification exhausted retries for message %s", row.message_id)
                continue
            row.lease_id = uuid.uuid4()
            row.leased_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            claims.append(
                ClaimedChatNotification(
                    row.message_id,
                    row.recipient_id,
                    row.cursor_token_id,
                    row.lease_id,
                    row.attempts,
                )
            )
        await self._session.flush()
        return ChatNotificationClaimBatch(claims, retired_count)

    async def token_page(
        self, *, recipient_id: uuid.UUID, after: uuid.UUID | None, limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        """Read only currently owned devices of an active, nondeleted recipient."""
        query = (
            select(DeviceToken.id, DeviceToken.token)
            .join(User, User.id == DeviceToken.user_id)
            .where(
                DeviceToken.user_id == recipient_id,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
            .order_by(DeviceToken.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(DeviceToken.id > after)
        return [(row.id, row.token) for row in (await self._session.execute(query)).all()]

    async def advance(
        self,
        claim: ClaimedChatNotification,
        *,
        now: datetime,
        cursor: uuid.UUID | None,
        complete: bool,
    ) -> bool:
        """Record a successful page only while this claim owns a live lease."""
        result = await self._session.execute(
            update(ChatNotification)
            .where(
                ChatNotification.message_id == claim.message_id,
                ChatNotification.lease_id == claim.lease_id,
                ChatNotification.leased_until > now,
                ChatNotification.completed_at.is_(None),
                ChatNotification.failed_at.is_(None),
            )
            .values(
                cursor_token_id=cursor,
                completed_at=now if complete else None,
                available_at=now,
                attempts=0,
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def retry(self, claim: ClaimedChatNotification, *, now: datetime) -> None:
        """Retain the cursor, back off, and persist terminal failures for review."""
        exhausted = claim.attempts >= _MAX_ATTEMPTS
        await self._session.execute(
            update(ChatNotification)
            .where(
                ChatNotification.message_id == claim.message_id,
                ChatNotification.lease_id == claim.lease_id,
                ChatNotification.completed_at.is_(None),
                ChatNotification.failed_at.is_(None),
            )
            .values(
                available_at=now + timedelta(seconds=min(2 ** min(claim.attempts, 10), 3600)),
                failed_at=now if exhausted else None,
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )
        if exhausted:
            _logger.error("chat notification exhausted retries for message %s", claim.message_id)
