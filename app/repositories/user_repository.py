"""User persistence used by the admin dashboard and auth (SPEC SECTION 10)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, CourierProfile, Order, User
from app.models.enums import UserRole, UserStatus

_DASHBOARD_ADMIN_NAMESPACE = uuid.UUID("48c72a54-78e4-4a0e-a20f-54378ed7f950")


@dataclass(frozen=True, slots=True)
class ParticipantProjection:
    """The complete privacy-scoped participant projection selected by SQL."""

    id: uuid.UUID
    full_name: str | None
    role: UserRole
    rating: Decimal
    rating_count: int
    courier_city: str | None
    courier_bio: str | None


class UserRepository:
    """Reads and status-mutates user rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        """Return a user by id, or None."""
        return await self._session.get(User, user_id)

    async def get_for_update(self, user_id: uuid.UUID) -> User | None:
        """Lock and return a user so competing status transitions serialize."""
        result: User | None = await self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def get_by_phone(self, phone: str) -> User | None:
        """Return a user by exact phone, or None."""
        result: User | None = await self._session.scalar(select(User).where(User.phone == phone))
        return result

    async def get_by_email(self, email: str) -> User | None:
        """Return a user by exact email, or None."""
        result: User | None = await self._session.scalar(select(User).where(User.email == email))
        return result

    async def actor_shares_participant(
        self, actor_id: uuid.UUID, participant_id: uuid.UUID
    ) -> bool:
        """Return whether two users share an order or conversation, entirely in SQL."""
        if actor_id == participant_id:
            return bool(await self._session.scalar(select(exists().where(User.id == actor_id))))
        shared_order = exists().where(
            ((Order.customer_id == actor_id) & (Order.courier_id == participant_id))
            | ((Order.customer_id == participant_id) & (Order.courier_id == actor_id))
        )
        shared_conversation = exists().where(
            ((Conversation.customer_id == actor_id) & (Conversation.courier_id == participant_id))
            | ((Conversation.customer_id == participant_id) & (Conversation.courier_id == actor_id))
        )
        return bool(await self._session.scalar(select(shared_order | shared_conversation)))

    async def get_participant_for_actor(
        self, actor_id: uuid.UUID, participant_id: uuid.UUID
    ) -> ParticipantProjection | None:
        """Select a compact profile only when SQL proves a shared relationship."""
        shared_order = exists().where(
            ((Order.customer_id == actor_id) & (Order.courier_id == participant_id))
            | ((Order.customer_id == participant_id) & (Order.courier_id == actor_id))
        )
        shared_conversation = exists().where(
            ((Conversation.customer_id == actor_id) & (Conversation.courier_id == participant_id))
            | ((Conversation.customer_id == participant_id) & (Conversation.courier_id == actor_id))
        )
        relationship = shared_order | shared_conversation
        row = (
            await self._session.execute(
                select(
                    User.id,
                    User.full_name,
                    User.role,
                    User.rating,
                    User.rating_count,
                    CourierProfile.city_of_residence,
                    CourierProfile.bio,
                )
                .outerjoin(CourierProfile, CourierProfile.user_id == User.id)
                .where(User.id == participant_id, relationship)
            )
        ).one_or_none()
        if row is None:
            return None
        return ParticipantProjection(
            id=row.id,
            full_name=row.full_name,
            role=row.role,
            rating=row.rating,
            rating_count=row.rating_count,
            courier_city=row.city_of_residence,
            courier_bio=row.bio,
        )

    async def get_owned_with_courier(
        self, actor_id: uuid.UUID
    ) -> tuple[User, CourierProfile | None] | None:
        """Return the actor's own user row and optional courier profile in one query."""
        row = (
            await self._session.execute(
                select(User, CourierProfile)
                .outerjoin(CourierProfile, CourierProfile.user_id == User.id)
                .where(User.id == actor_id)
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def flush(self) -> None:
        """Flush owned-profile mutations performed by the user service."""
        await self._session.flush()

    async def create_admin_user(
        self, *, phone: str, full_name: str | None, email: str | None, role: UserRole
    ) -> User:
        """Create a customer or courier account from the dashboard."""
        user = User(phone=phone, full_name=full_name, email=email, role=role)
        self._session.add(user)
        await self._session.flush()
        return user

    async def ensure_dashboard_admin(self, username: str) -> User | None:
        """Return the stable DB actor used by environment-authenticated dashboard sessions.

        The credential remains environment-only. A reserved internal user gives sessions,
        foreign keys, and audit rows a durable actor without inventing a customer phone.
        PostgreSQL's conflict handling keeps simultaneous first logins idempotent.
        """
        admin_id = uuid.uuid5(_DASHBOARD_ADMIN_NAMESPACE, username)
        internal_phone = f"admin:{hashlib.sha256(username.encode()).hexdigest()[:14]}"
        await self._session.execute(
            insert(User)
            .values(
                id=admin_id,
                phone=internal_phone,
                full_name="Dashboard administrator",
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
            .on_conflict_do_nothing()
        )
        await self._session.flush()
        return await self.get_for_update(admin_id)

    async def update_admin_profile(
        self, user: User, *, phone: str | None, full_name: str | None, email: str | None
    ) -> None:
        """Persist dashboard profile fields; the service invalidates changed login identities."""
        if phone is not None:
            user.phone = phone
        user.full_name = full_name
        user.email = email
        await self._session.flush()

    async def soft_delete(self, user: User) -> None:
        """Disable a user while retaining rows required for financial/audit history."""
        user.status = UserStatus.BANNED
        user.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def set_status(self, user: User, status: UserStatus) -> None:
        """Update a user's account status (ban/unban)."""
        user.status = status
        await self._session.flush()
