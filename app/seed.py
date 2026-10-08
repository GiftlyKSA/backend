"""Idempotent city and system-wallet seeds for fresh database initialization."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.city_seeds import DEFAULT_CITIES
from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.models import City, Wallet
from app.models.enums import WalletType

_SYSTEM_WALLETS = (
    WalletType.SYSTEM_ESCROW,
    WalletType.SYSTEM_REVENUE,
    WalletType.SYSTEM_GATEWAY,
)


async def seed_system_wallets() -> int:
    """Create any missing system wallets. Returns the number created."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    async with factory() as session:
        created = await seed_system_wallets_in_session(session)
        await session.commit()
    await engine.dispose()
    return created


async def seed_system_wallets_in_session(session: AsyncSession) -> int:
    """Create missing system wallets within the caller's transaction."""
    created = 0
    for wallet_type in _SYSTEM_WALLETS:
        existing = await session.scalar(select(Wallet).where(Wallet.type == wallet_type))
        if existing is None:
            session.add(Wallet(type=wallet_type, user_id=None))
            created += 1
    await session.flush()
    return created


async def seed_cities() -> int:
    """Add 20 active Saudi cities only when the catalog is empty."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            async with session.begin():
                return await seed_cities_in_session(session)
    finally:
        await engine.dispose()


async def seed_cities_in_session(session: AsyncSession) -> int:
    """Create missing cities and refresh Arabic names without changing activity."""
    existing = {city.shortcut: city for city in await session.scalars(select(City))}
    created = 0
    for name, name_ar, shortcut in DEFAULT_CITIES:
        city = existing.get(shortcut)
        if city is None:
            session.add(City(name=name, name_ar=name_ar, shortcut=shortcut, is_active=True))
            created += 1
        else:
            city.name_ar = name_ar
    await session.flush()
    return created


def main() -> None:
    """Run the seed and report how many system wallets were created."""
    created = asyncio.run(seed_system_wallets())
    cities = asyncio.run(seed_cities())
    print(f"Seed complete. Created {created} system wallet(s) and {cities} cities.")


if __name__ == "__main__":
    main()
