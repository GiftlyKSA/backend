"""Read transactional invalidation tokens without loading cached business records."""

from uuid import UUID

from sqlalchemy import Column, MetaData, String, Table, Uuid, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

_VERSIONS = Table(
    "read_cache_versions",
    MetaData(),
    Column("scope", String(32), primary_key=True),
    Column("owner_id", Uuid, primary_key=True),
    Column("version", Uuid, nullable=False),
)
VersionKey = tuple[str, UUID]


class ReadCacheRepository:
    """Read only a fixed number of indexed cache-version rows per request."""

    def __init__(self, session: AsyncSession) -> None:
        """Use the caller's current transaction."""
        self._session = session

    async def versions(self, keys: list[VersionKey]) -> tuple[str, ...]:
        """Missing rows mean unchanged since the version migration, never user data."""
        if not 1 <= len(keys) <= 4:
            raise ValueError("Read cache versions require one to four scopes.")
        rows = await self._session.execute(
            select(_VERSIONS).where(tuple_(_VERSIONS.c.scope, _VERSIONS.c.owner_id).in_(keys))
        )
        versions = {(scope, owner): str(version) for scope, owner, version in rows}
        return tuple(versions.get(key, "0") for key in keys)
