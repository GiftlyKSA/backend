"""Idempotent development seed (SPEC SECTION 9, README quickstart).

Ensures the four SYSTEM_* wallets exist. The baseline migration already seeds them,
so this is a safety net for databases created another way; it never duplicates a
system wallet thanks to the partial unique indexes.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
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
    WalletType.SYSTEM_TAX_PAYABLE,
)


async def seed_system_wallets() -> int:
    """Create any missing system wallets. Returns the number created."""
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    created = 0
    async with factory() as session:
        for wallet_type in _SYSTEM_WALLETS:
            existing = await session.scalar(select(Wallet).where(Wallet.type == wallet_type))
            if existing is None:
                session.add(Wallet(type=wallet_type, user_id=None))
                created += 1
        await session.commit()
    await engine.dispose()
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
    """Seed one transaction if no city has been created."""
    if await session.scalar(select(func.count()).select_from(City)):
        return 0
    session.add_all(
        City(name=name, shortcut=shortcut, is_active=True) for name, shortcut in DEFAULT_CITIES
    )
    await session.flush()
    return len(DEFAULT_CITIES)


def main() -> None:
    """Run the seed and report how many system wallets were created."""
    created = asyncio.run(seed_system_wallets())
    cities = asyncio.run(seed_cities())
    print(f"Seed complete. Created {created} system wallet(s) and {cities} cities.")


if __name__ == "__main__":
    main()
