"""Persistence for issued upload grants and atomic one-time attachment claims."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import zip_longest

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models import MediaUpload, User

_MAX_CLEANUP_BATCH = 100


@dataclass(frozen=True)
class MediaCleanupReservation:
    """One detached grant with the reservation timestamp needed to finalize it."""

    grant_id: uuid.UUID
    storage_key: str
    reserved_at: datetime


class MediaRepository:
    """Store upload grants in the same transaction as their eventual attachment."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind grants to the caller's transaction."""
        self._session = session

    async def issue(
        self,
        *,
        storage_key: str,
        actor_id: uuid.UUID,
        purpose: str,
        content_type: str,
        byte_size: int,
        max_count: int,
        max_bytes: int,
    ) -> None:
        """Record a newly issued grant for an authenticated actor."""
        await self._session.scalar(select(User.id).where(User.id == actor_id).with_for_update())
        count, total_bytes = (
            await self._session.execute(
                select(func.count(), func.coalesce(func.sum(MediaUpload.byte_size), 0)).where(
                    MediaUpload.owner_user_id == actor_id,
                    MediaUpload.attached_at.is_(None),
                )
            )
        ).one()
        if count >= max_count or total_bytes + byte_size > max_bytes:
            raise ConflictError("Outstanding media upload quota exceeded.")
        self._session.add(
            MediaUpload(
                storage_key=storage_key,
                owner_user_id=actor_id,
                purpose=purpose,
                content_type=content_type,
                byte_size=byte_size,
            )
        )
        await self._session.flush()

    async def get(self, storage_key: str, *, for_update: bool = False) -> MediaUpload | None:
        """Fetch a grant by its server-generated key."""
        query = select(MediaUpload).where(MediaUpload.storage_key == storage_key)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        grant: MediaUpload | None = await self._session.scalar(query)
        return grant

    async def mark_confirmed(self, storage_key: str, actor_id: uuid.UUID) -> bool:
        """Confirm an unclaimed key if it still belongs to the actor."""
        updated_key = await self._session.scalar(
            update(MediaUpload)
            .where(
                MediaUpload.storage_key == storage_key,
                MediaUpload.owner_user_id == actor_id,
                MediaUpload.attached_at.is_(None),
                MediaUpload.deleting_at.is_(None),
            )
            .values(confirmed_at=datetime.now(UTC))
            .returning(MediaUpload.storage_key)
        )
        return updated_key is not None

    async def get_many(
        self, storage_keys: list[str], *, for_update: bool = False
    ) -> list[MediaUpload]:
        """Fetch a bounded batch, acquiring grant locks in stable key order."""
        query = (
            select(MediaUpload)
            .where(MediaUpload.storage_key.in_(storage_keys))
            .order_by(MediaUpload.storage_key)
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(await self._session.scalars(query))

    async def confirm_and_claim_many(
        self, storage_keys: list[str], actor_id: uuid.UUID, purpose: str
    ) -> set[str]:
        """Consume validated, locked grants; callers must reject partial claims."""
        now = datetime.now(UTC)
        return set(
            await self._session.scalars(
                update(MediaUpload)
                .where(
                    MediaUpload.storage_key.in_(storage_keys),
                    MediaUpload.owner_user_id == actor_id,
                    MediaUpload.purpose == purpose,
                    MediaUpload.attached_at.is_(None),
                    MediaUpload.deleting_at.is_(None),
                )
                .values(confirmed_at=now, attached_at=now)
                .returning(MediaUpload.storage_key)
            )
        )

    async def claim_confirmed_many(
        self, storage_keys: list[str], actor_id: uuid.UUID, purpose: str
    ) -> set[str]:
        """Consume only confirmed, owned, unclaimed grants under caller-held ordered locks."""
        return set(
            await self._session.scalars(
                update(MediaUpload)
                .where(
                    MediaUpload.storage_key.in_(storage_keys),
                    MediaUpload.owner_user_id == actor_id,
                    MediaUpload.purpose == purpose,
                    MediaUpload.confirmed_at.is_not(None),
                    MediaUpload.attached_at.is_(None),
                    MediaUpload.deleting_at.is_(None),
                )
                .values(attached_at=datetime.now(UTC))
                .returning(MediaUpload.storage_key)
            )
        )

    async def claim(self, storage_key: str, actor_id: uuid.UUID, purpose: str) -> bool:
        """Atomically consume a confirmed grant once for its issued purpose."""
        updated_key = await self._session.scalar(
            update(MediaUpload)
            .where(
                MediaUpload.storage_key == storage_key,
                MediaUpload.owner_user_id == actor_id,
                MediaUpload.purpose == purpose,
                MediaUpload.confirmed_at.is_not(None),
                MediaUpload.attached_at.is_(None),
                MediaUpload.deleting_at.is_(None),
            )
            .values(attached_at=datetime.now(UTC))
            .returning(MediaUpload.storage_key)
        )
        return updated_key is not None

    async def reserve_expired(
        self, *, before: datetime, limit: int
    ) -> list[MediaCleanupReservation]:
        """Reserve a bounded mixture of fresh and retryable cleanup work."""
        limit = min(max(limit, 1), _MAX_CLEANUP_BATCH)
        now = datetime.now(UTC)
        candidates = (
            select(MediaUpload.id)
            .where(MediaUpload.created_at < before, MediaUpload.attached_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        retries = candidates.where(MediaUpload.deleting_at <= now - timedelta(hours=1)).order_by(
            MediaUpload.deleting_at, MediaUpload.id
        )
        retry_limit = max(1, limit // 4)
        retry_ids = list(await self._session.scalars(retries.limit(retry_limit)))
        fresh_ids = list(
            await self._session.scalars(
                candidates.where(MediaUpload.deleting_at.is_(None))
                .order_by(MediaUpload.created_at, MediaUpload.id)
                .limit(limit - len(retry_ids))
            )
        )
        remaining = limit - len(retry_ids) - len(fresh_ids)
        if remaining and len(retry_ids) == retry_limit:
            retry_ids.extend(
                await self._session.scalars(
                    retries.where(MediaUpload.id.not_in(retry_ids)).limit(remaining)
                )
            )
        if not retry_ids and not fresh_ids:
            return []
        rows = await self._session.execute(
            update(MediaUpload)
            .where(MediaUpload.id.in_(retry_ids + fresh_ids))
            .values(deleting_at=now)
            .returning(MediaUpload.id, MediaUpload.storage_key)
            .execution_options(synchronize_session=False)
        )
        reserved = {row.id: MediaCleanupReservation(row.id, row.storage_key, now) for row in rows}
        return [
            reserved[grant_id]
            for pair in zip_longest(retry_ids, fresh_ids)
            for grant_id in pair
            if grant_id is not None
        ]

    async def remove_reserved(self, reservations: list[MediaCleanupReservation]) -> int:
        """Finalize only successful deletions owned by these exact reservations."""
        if not reservations:
            return 0
        if len(reservations) > _MAX_CLEANUP_BATCH:
            raise ValueError("Media cleanup finalization batch exceeds 100 grants.")
        removed = await self._session.scalars(
            delete(MediaUpload)
            .where(
                tuple_(MediaUpload.id, MediaUpload.deleting_at).in_(
                    [
                        (reservation.grant_id, reservation.reserved_at)
                        for reservation in reservations
                    ]
                ),
                MediaUpload.attached_at.is_(None),
            )
            .returning(MediaUpload.id)
            .execution_options(synchronize_session=False)
        )
        return len(removed.all())
