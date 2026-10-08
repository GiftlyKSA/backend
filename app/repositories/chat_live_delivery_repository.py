"""Bounded, fenced delivery of durable chat live fanout intents."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatLiveDelivery

_MAX_ATTEMPTS = 8
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedChatLiveDelivery:
    """A live fanout intent and its fenced attempt."""

    message_id: uuid.UUID
    lease_id: uuid.UUID
    attempts: int


@dataclass(frozen=True)
class ChatLiveDeliveryClaimBatch:
    """Separate retired rows from an empty delivery queue."""

    claims: list[ClaimedChatLiveDelivery]
    retired_count: int


class ChatLiveDeliveryRepository:
    """Persist short delivery claims without locks across external calls."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the delivery session."""
        self._session = session

    async def claim_pending(
        self, *, now: datetime, limit: int, lease_seconds: int, message_id: uuid.UUID | None = None
    ) -> ChatLiveDeliveryClaimBatch:
        """Lease ready rows, skipping overlapping workers and exhausted retries."""
        rows = await self._session.scalars(
            select(ChatLiveDelivery)
            .where(
                (ChatLiveDelivery.message_id == message_id) if message_id is not None else true(),
                ChatLiveDelivery.completed_at.is_(None),
                ChatLiveDelivery.failed_at.is_(None),
                ChatLiveDelivery.available_at <= now,
                or_(
                    ChatLiveDelivery.leased_until.is_(None),
                    ChatLiveDelivery.leased_until <= now,
                ),
            )
            .order_by(ChatLiveDelivery.available_at, ChatLiveDelivery.created_at)
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
                _logger.error("chat live delivery exhausted retries for message %s", row.message_id)
                continue
            row.lease_id = uuid.uuid4()
            row.leased_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            claims.append(
                ClaimedChatLiveDelivery(
                    row.message_id,
                    row.lease_id,
                    row.attempts,
                )
            )
        await self._session.flush()
        return ChatLiveDeliveryClaimBatch(claims, retired_count)

    async def advance(
        self,
        claim: ClaimedChatLiveDelivery,
        *,
        now: datetime,
    ) -> bool:
        """Record a successful publish only while this claim owns a live lease."""
        result = await self._session.execute(
            update(ChatLiveDelivery)
            .where(
                ChatLiveDelivery.message_id == claim.message_id,
                ChatLiveDelivery.lease_id == claim.lease_id,
                ChatLiveDelivery.leased_until > now,
                ChatLiveDelivery.completed_at.is_(None),
                ChatLiveDelivery.failed_at.is_(None),
            )
            .values(
                completed_at=now,
                available_at=now,
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def retry(self, claim: ClaimedChatLiveDelivery, *, now: datetime) -> None:
        """Back off, and persist terminal failures for review."""
        exhausted = claim.attempts >= _MAX_ATTEMPTS
        await self._session.execute(
            update(ChatLiveDelivery)
            .where(
                ChatLiveDelivery.message_id == claim.message_id,
                ChatLiveDelivery.lease_id == claim.lease_id,
                ChatLiveDelivery.leased_until > now,
                ChatLiveDelivery.completed_at.is_(None),
                ChatLiveDelivery.failed_at.is_(None),
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
            _logger.error("chat live delivery exhausted retries for message %s", claim.message_id)
