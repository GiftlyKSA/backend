"""Shared current-state eligibility boundary for courier operations."""

from __future__ import annotations

import uuid

from app.core.exceptions import ForbiddenError
from app.models.enums import UserRole, UserStatus
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository


class CourierEligibilityService:
    """Reject operational access unless a courier is currently active and verified."""

    def __init__(self, *, users: UserRepository, couriers: CourierRepository) -> None:
        """Wire the current user and courier-profile repositories."""
        self._users = users
        self._couriers = couriers

    async def require_eligible_actor(self, actor_id: uuid.UUID) -> None:
        """Allow non-couriers, but apply the courier boundary to courier accounts."""
        user = await self._users.get(actor_id)
        if user is None:
            raise ForbiddenError("This account is not eligible for this action.")
        if user.role is UserRole.COURIER:
            await self._require_active_verified(user.id, user.status)

    async def require_courier(self, courier_id: uuid.UUID) -> None:
        """Require an existing active and verified courier account."""
        user = await self._users.get(courier_id)
        if user is None or user.role is not UserRole.COURIER:
            raise ForbiddenError("This courier account is not eligible for this action.")
        await self._require_active_verified(user.id, user.status)

    async def require_marketplace_actor(self, actor_id: uuid.UUID) -> None:
        """Require an active customer or a currently verified, active courier."""
        user = await self._users.get(actor_id)
        if (
            user is None
            or user.role not in {UserRole.CUSTOMER, UserRole.COURIER}
            or user.status is not UserStatus.ACTIVE
            or user.deleted_at is not None
        ):
            raise ForbiddenError("This account is not eligible for this action.")
        if user.role is UserRole.COURIER:
            await self._require_active_verified(user.id, user.status)

    async def require_customer(self, customer_id: uuid.UUID) -> None:
        """Require an active, non-erased customer for financial mutations."""
        user = await self._users.get(customer_id)
        if (
            user is None
            or user.role is not UserRole.CUSTOMER
            or user.status is not UserStatus.ACTIVE
            or user.deleted_at is not None
        ):
            raise ForbiddenError("This customer account is not eligible for this action.")

    async def _require_active_verified(self, courier_id: uuid.UUID, status: UserStatus) -> None:
        if status is not UserStatus.ACTIVE:
            raise ForbiddenError("This courier account is not eligible for this action.")
        profile = await self._couriers.get(courier_id)
        if profile is None or not profile.is_verified:
            raise ForbiddenError("This courier account is not eligible for this action.")
