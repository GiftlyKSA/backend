"""Device-token persistence for push notifications.

A token is unique across users (``uq_device_tokens_token``): registering a token that
already exists re-points it at the current user and refreshes ``last_seen_at``, so a
handed-down device never keeps pushing to its previous owner.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, CourierProfile, DeviceToken, User
from app.models.enums import DeviceOs, UserRole, UserStatus


class DeviceTokenRepository:
    """Registers, removes, and looks up push tokens."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def register(self, *, user_id: uuid.UUID, token: str, device_os: DeviceOs) -> DeviceToken:
        """Upsert a token to this user, refreshing last_seen_at (idempotent)."""
        insert_statement = insert(DeviceToken).values(
            user_id=user_id, token=token, device_os=device_os
        )
        upsert_statement = insert_statement.on_conflict_do_update(
            constraint="uq_device_tokens_token",
            set_={
                "user_id": insert_statement.excluded.user_id,
                "device_os": insert_statement.excluded.device_os,
                "last_seen_at": datetime.now(UTC),
            },
        ).returning(DeviceToken)
        result = await self._session.execute(upsert_statement)
        return result.scalar_one()

    async def remove(self, *, user_id: uuid.UUID, token: str) -> None:
        """Delete a token, but only if it belongs to this user (ownership in query)."""
        await self._session.execute(
            delete(DeviceToken).where(DeviceToken.token == token, DeviceToken.user_id == user_id)
        )
        await self._session.flush()

    async def other_device_ids(
        self,
        *,
        user_id: uuid.UUID,
        token: str,
        limit: int,
    ) -> list[uuid.UUID]:
        """Read a bounded set of occupied slots after the caller locks its user row."""
        return list(
            await self._session.scalars(
                select(DeviceToken.id)
                .where(DeviceToken.user_id == user_id, DeviceToken.token != token)
                .limit(limit)
            )
        )

    async def tokens_for_user(self, user_id: uuid.UUID) -> list[str]:
        """Return one bounded page of push tokens registered to a user."""
        return [token for _, token in await self.token_page(user_id=user_id)]

    async def tokens_for_city_couriers(self, city: str) -> list[str]:
        """Return one bounded page of ACTIVE, verified city couriers' tokens."""
        return [token for _, token in await self.token_page(city=city)]

    async def token_page(
        self,
        *,
        user_id: uuid.UUID | None = None,
        city: str | None = None,
        after: uuid.UUID | None = None,
        limit: int = 500,
    ) -> list[tuple[uuid.UUID, str]]:
        """Page recipients with stable IDs, retaining courier eligibility in SQL."""
        if (user_id is None) == (city is None):
            raise ValueError("Exactly one recipient scope is required.")
        query = select(DeviceToken.id, DeviceToken.token)
        if user_id is not None:
            query = query.where(DeviceToken.user_id == user_id)
        else:
            query = (
                query.join(User, User.id == DeviceToken.user_id)
                .join(
                    CourierProfile,
                    CourierProfile.user_id == User.id,
                )
                .where(
                    User.role == UserRole.COURIER,
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                    CourierProfile.is_verified.is_(True),
                    CourierProfile.city_of_residence_id
                    == select(City.id).where(City.name == city).scalar_subquery(),
                )
            )
        if after is not None:
            query = query.where(DeviceToken.id > after)
        result = await self._session.execute(query.order_by(DeviceToken.id).limit(min(limit, 500)))
        return [(row.id, row.token) for row in result]
