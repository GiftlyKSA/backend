"""Bound device registration while preserving refresh and ownership transfer."""

import uuid

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models import DeviceToken
from app.models.enums import DeviceOs, UserRole, UserStatus
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.user_repository import UserRepository

_MAX_DEVICES_PER_USER = 10


class DeviceLimitReachedError(ConflictError):
    """The caller must unregister a device before adding another."""

    code = "DEVICE_LIMIT_REACHED"
    message = "You can register up to 10 devices. Remove a device before adding another."


class DeviceService:
    """Serialize quota checks with registration in the caller's transaction."""

    def __init__(self, *, devices: DeviceTokenRepository, users: UserRepository) -> None:
        """Bind repositories sharing one transaction."""
        self._devices = devices
        self._users = users

    async def register(
        self,
        *,
        user_id: uuid.UUID,
        token: str,
        device_os: DeviceOs,
    ) -> DeviceToken:
        """Reserve a slot under the user's row lock, then atomically reassign the token."""
        user = await self._users.get_for_update(user_id)
        if user is None:
            raise NotFoundError()
        if user.deleted_at is not None or user.status in {UserStatus.BANNED, UserStatus.DELETED}:
            raise UnauthorizedError("This account is no longer available.")
        if user.role not in {UserRole.CUSTOMER, UserRole.COURIER}:
            raise ForbiddenError("Your role may not perform this action.")
        occupied = await self._devices.other_device_ids(
            user_id=user_id,
            token=token,
            limit=_MAX_DEVICES_PER_USER,
        )
        if len(occupied) >= _MAX_DEVICES_PER_USER:
            raise DeviceLimitReachedError()
        return await self._devices.register(user_id=user_id, token=token, device_os=device_os)

    async def remove(self, *, user_id: uuid.UUID, token: str) -> None:
        """Revoke only the caller's device."""
        await self._devices.remove(user_id=user_id, token=token)
