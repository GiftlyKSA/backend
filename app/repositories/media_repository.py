"""Persistence for issued upload grants and atomic one-time attachment claims."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaUpload


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
    ) -> None:
        """Record a newly issued grant for an authenticated actor."""
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

    async def get(self, storage_key: str) -> MediaUpload | None:
        """Fetch a grant by its server-generated key."""
        grant: MediaUpload | None = await self._session.scalar(
            select(MediaUpload).where(MediaUpload.storage_key == storage_key)
        )
        return grant

    async def mark_confirmed(self, storage_key: str, actor_id: uuid.UUID) -> bool:
        """Confirm an unclaimed key if it still belongs to the actor."""
        updated_key = await self._session.scalar(
            update(MediaUpload)
            .where(
                MediaUpload.storage_key == storage_key,
                MediaUpload.owner_user_id == actor_id,
                MediaUpload.attached_at.is_(None),
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
            )
            .values(attached_at=datetime.now(UTC))
            .returning(MediaUpload.storage_key)
        )
        return updated_key is not None
