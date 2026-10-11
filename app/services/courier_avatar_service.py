"""Courier-only avatar mutations through owned upload grants."""

import uuid

from app.repositories.courier_avatar_repository import CourierAvatarRepository
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_service import MediaService


class CourierAvatarService:
    """Enforce role and one-time attachment ownership."""

    def __init__(
        self, repository: CourierAvatarRepository, eligibility: CourierEligibilityService
    ) -> None:
        """Wire persistence and existing account eligibility."""
        self.repository = repository
        self.eligibility = eligibility

    async def set_avatar(self, owner: uuid.UUID, key: str, media: MediaService) -> None:
        """Attach an already validated upload in this transaction."""
        await self.eligibility.require_courier(owner)
        await self.repository.lock_owner(owner)
        await media.claim(key, actor_id=owner, purpose="COURIER_AVATAR")
        await self.repository.set_avatar(owner, key)

    async def remove_avatar(self, owner: uuid.UUID) -> None:
        """Remove the pointer; abandoned-upload cleanup removes the bytes."""
        await self.eligibility.require_courier(owner)
        await self.repository.lock_owner(owner)
        await self.repository.set_avatar(owner, None)
