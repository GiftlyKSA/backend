"""Persistence for one private courier avatar."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaUpload, User
from app.models.courier_avatar import CourierAvatar


class CourierAvatarRepository:
    """Serialize avatar replacements on the user row."""

    def __init__(self, session: AsyncSession) -> None:
        """Use the request transaction."""
        self.session = session

    async def lock_owner(self, owner: uuid.UUID) -> None:
        """Acquire before claiming an upload."""
        await self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    async def get_key(self, owner: uuid.UUID) -> str | None:
        """Read one avatar after caller authorization."""
        key: str | None = await self.session.scalar(
            select(CourierAvatar.storage_key).where(CourierAvatar.user_id == owner)
        )

        return key

    async def set_avatar(self, owner: uuid.UUID, key: str | None) -> None:
        """Replace the pointer and release the old upload for cleanup."""
        current = await self.session.get(CourierAvatar, owner)
        previous = current.storage_key if current else None
        if current is not None:
            await self.session.delete(current)
            await self.session.flush()
        if key is not None:
            self.session.add(CourierAvatar(user_id=owner, storage_key=key))
        await self.session.flush()
        if previous is not None:
            grant = await self.session.scalar(
                select(MediaUpload).where(MediaUpload.storage_key == previous).with_for_update()
            )
            if grant is not None:
                grant.attached_at = None
        await self.session.flush()
