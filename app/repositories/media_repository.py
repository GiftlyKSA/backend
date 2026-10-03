"""Persistence for issued upload grants and atomic one-time attachment claims."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models import MediaUpload, User


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

    async def reserve_expired(self, *, before: datetime, limit: int) -> list[str]:
        """Fence a bounded batch against claims before removing its objects."""
        rows = (
            await self._session.scalars(
                select(MediaUpload)
                .where(
                    MediaUpload.created_at < before,
                    MediaUpload.attached_at.is_(None),
                    or_(
                        MediaUpload.deleting_at.is_(None),
                        MediaUpload.deleting_at < datetime.now(UTC) - timedelta(hours=1),
                    ),
                )
                .order_by(
                    MediaUpload.deleting_at.nulls_first(),
                    MediaUpload.created_at,
                    MediaUpload.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.deleting_at = now
        return [row.storage_key for row in rows]

    async def remove_reserved(self, storage_key: str) -> None:
        """Remove a fenced grant after its object was deleted or absent."""
        await self._session.execute(
            delete(MediaUpload).where(
                MediaUpload.storage_key == storage_key,
                MediaUpload.deleting_at.is_not(None),
                MediaUpload.attached_at.is_(None),
            )
        )
