"""Create missing development couriers without changing existing accounts."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import set_audit_actor
from app.core.config import Environment, Settings, get_migration_settings, get_settings
from app.core.crypto import build_aad, build_cipher
from app.core.db import build_engine, build_session_factory
from app.core.logging import configure_logging
from app.models import City, CourierProfile, User, Wallet
from app.models.enums import UserRole, UserStatus, WalletType

DEVELOPMENT_COURIERS = (
    ("+966555555555", "RUH"),
    ("+966555555556", "JED"),
)
_SEED_LOCK = 749283781
_logger = logging.getLogger(__name__)


async def seed_couriers_in_session(session: AsyncSession, settings: Settings) -> int:
    """Insert missing verified courier accounts and wallets in one transaction."""
    if settings.ENVIRONMENT is not Environment.DEVELOPMENT:
        return 0
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SEED_LOCK})
    cities = {
        city.shortcut: city
        for city in await session.scalars(select(City).where(City.shortcut.in_(["RUH", "JED"])))
    }
    if len(cities) != 2:
        raise RuntimeError("Development courier seed requires the Riyadh and Jeddah city records.")
    await set_audit_actor(session, category="SYSTEM", actor_user_id=None)
    users = list(
        await session.scalars(
            insert(User)
            .values(
                [
                    {
                        "phone": phone,
                        "role": UserRole.COURIER,
                        "status": UserStatus.ACTIVE,
                        "full_name": f"Development Courier {cities[shortcut].name}",
                    }
                    for phone, shortcut in DEVELOPMENT_COURIERS
                ]
            )
            .on_conflict_do_nothing(index_elements=[User.phone])
            .returning(User)
        )
    )
    city_by_phone = dict(DEVELOPMENT_COURIERS)
    cipher = build_cipher(settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION)
    for user in users:
        session.add(Wallet(user_id=user.id, type=WalletType.COURIER))
        session.add(
            CourierProfile(
                user_id=user.id,
                city_of_residence_id=cities[city_by_phone[user.phone]].id,
                passport_id_encrypted=cipher.encrypt(
                    "DEVELOPMENT-ONLY-NOT-AN-IDENTITY-DOCUMENT",
                    build_aad("courier_profiles", "passport_id", str(user.id)),
                ),
                is_verified=True,
                verified_at=datetime.now(UTC),
            )
        )
    await session.flush()
    return len(users)


async def seed_development_couriers() -> int:
    """Skip all other environments before constructing database or API clients."""
    if get_migration_settings().ENVIRONMENT is not Environment.DEVELOPMENT:
        return 0
    settings = get_settings()
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session, session.begin():
            created = await seed_couriers_in_session(session, settings)
        _logger.info("Development courier seed completed; created %d accounts.", created)
        return created
    finally:
        await engine.dispose()


def main() -> None:
    """Run the development-only seed explicitly."""
    configure_logging()
    asyncio.run(seed_development_couriers())


if __name__ == "__main__":
    main()
