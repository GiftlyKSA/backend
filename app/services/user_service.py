"""Privacy-scoped user profile reads and owner-only profile mutations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models import CourierProfile, User
from app.models.enums import UserRole, UserStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_repository import ParticipantProjection, UserRepository


@dataclass(frozen=True, slots=True)
class ParticipantView:
    """Safe participant values ready for HTTP serialization."""

    id: uuid.UUID
    display_name: str
    role: UserRole
    rating: Decimal
    rating_count: int
    initials: str
    courier_city: str | None
    courier_bio: str | None


class UserService:
    """Owns self-profile updates and relationship-scoped participant reads."""

    def __init__(self, *, users: UserRepository, audit: AuditRepository) -> None:
        """Wire persistence collaborators."""
        self._users = users
        self._audit = audit

    async def get_me(self, actor_id: uuid.UUID) -> tuple[User, CourierProfile | None]:
        """Return only the authenticated actor's own profile."""
        owned = await self._users.get_owned_with_courier(actor_id)
        if owned is None:
            raise NotFoundError("User not found.")
        return owned

    async def update_me(
        self,
        actor_id: uuid.UUID,
        *,
        full_name: str | None,
        email: str | None,
        dob: date | None,
        courier_city: str | None,
        courier_bio: str | None,
        supplied: set[str],
    ) -> tuple[User, CourierProfile | None]:
        """Update permitted fields on the verified actor's own rows."""
        user, courier = await self.get_me(actor_id)
        if "full_name" in supplied:
            user.full_name = full_name
        if "email" in supplied:
            user.email = email
        if "dob" in supplied:
            user.date_of_birth = dob
        courier_fields = {"courier_city", "courier_bio"} & supplied
        if courier_fields and (user.role is not UserRole.COURIER or courier is None):
            raise ForbiddenError("Courier profile fields require a courier account.")
        if courier is not None:
            if "courier_city" in supplied and courier_city is not None:
                courier.city_of_residence = courier_city
            if "courier_bio" in supplied:
                courier.bio = courier_bio
        await self._users.flush()
        return user, courier

    async def get_participant(
        self, *, actor_id: uuid.UUID, participant_id: uuid.UUID
    ) -> ParticipantView:
        """Return a compact profile after repository-level relationship proof."""
        projection = await self._users.get_participant_for_actor(actor_id, participant_id)
        if projection is None:
            raise NotFoundError("Participant not found.")
        return self._participant_view(projection)

    async def resubmit_courier_verification(
        self, *, actor_id: uuid.UUID
    ) -> tuple[User, CourierProfile]:
        """Move an owning rejected courier back to pending and audit the request."""
        user, courier = await self.get_me(actor_id)
        if (
            user.role is not UserRole.COURIER
            or courier is None
            or user.status is not UserStatus.REJECTED
        ):
            raise ConflictError("Only a rejected courier may resubmit verification.")
        user.status = UserStatus.PENDING_VERIFICATION
        courier.is_verified = False
        courier.verification_rejection_reason = None
        await self._users.flush()
        await self._audit.record(
            actor_user_id=actor_id,
            action="COURIER_VERIFICATION_RESUBMIT",
            entity_type="courier_profiles",
            entity_id=actor_id,
        )
        return user, courier

    @staticmethod
    def _participant_view(projection: ParticipantProjection) -> ParticipantView:
        display_name = (projection.full_name or "Giftly user").strip() or "Giftly user"
        words = [word for word in display_name.split() if word]
        initials = "".join(word[0].upper() for word in words[:2]) or "GU"
        return ParticipantView(
            id=projection.id,
            display_name=display_name,
            role=projection.role,
            rating=Decimal(str(projection.rating)),
            rating_count=projection.rating_count,
            initials=initials,
            courier_city=projection.courier_city,
            courier_bio=projection.courier_bio,
        )
