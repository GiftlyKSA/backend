"""Bounded DTO read caching; database versions and fresh authorization remain authoritative."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.repositories.read_cache_repository import ReadCacheRepository, VersionKey

logger = logging.getLogger(__name__)
T = TypeVar("T")
ReadDomain = Literal["catalog", "occasions", "orders"]
_MAX_BYTES = 262144
_MAX_ENTRIES = 256
_MAX_PENDING = 32
_TTLS = {"catalog": 300, "occasions": 3600, "orders": 30}
_ZERO = UUID(int=0)
_STORE = """
local clock=redis.call('TIME')
local now=tonumber(clock[1])+tonumber(clock[2])/1000000
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
redis.call('SET',KEYS[2],ARGV[1],'EX',ARGV[2])
redis.call('ZADD',KEYS[1],now+tonumber(ARGV[2]),KEYS[2])
local excess=redis.call('ZCARD',KEYS[1])-tonumber(ARGV[3])
if excess>0 then
 local oldest=redis.call('ZPOPMIN',KEYS[1],excess)
 for i=1,#oldest,2 do redis.call('DEL',oldest[i]) end
end
redis.call('EXPIRE',KEYS[1],7200)
return 1
"""


def _scopes(domain: ReadDomain, owner: UUID | None) -> list[VersionKey]:
    if domain == "catalog":
        return [("catalog", _ZERO)]
    if owner is None:
        raise ValueError("Private cached reads require an authenticated owner.")
    scopes = [(domain, owner), (f"{domain}_all", _ZERO)]
    if domain == "orders":
        scopes.append(("catalog", _ZERO))
    return scopes


class ResultCache:
    """Cache only serialized DTOs, with bounded miss coalescing and Redis budgets."""

    def __init__(self, redis: Redis) -> None:
        """Share no request sessions, ORM objects or authorization decisions."""
        self._redis = redis
        self._pending: dict[str, asyncio.Future[bytes | None]] = {}

    async def _read(self, key: str, adapter: TypeAdapter[T]) -> T | None:
        try:
            async with asyncio.timeout(0.1):
                value = await self._redis.getrange(key, 0, _MAX_BYTES)
            if isinstance(value, (str, bytes)) and 0 < len(value) <= _MAX_BYTES:
                return adapter.validate_json(value)
        except ValidationError, UnicodeError:
            pass
        except RedisError, TimeoutError:
            logger.warning("Read cache lookup unavailable.")
        return None

    async def read(
        self,
        *,
        repository: ReadCacheRepository,
        domain: ReadDomain,
        owner: UUID | None,
        role: str,
        auth_version: int,
        filters: dict[str, JsonValue],
        adapter: TypeAdapter[T],
        load: Callable[[], Awaitable[T]],
    ) -> T:
        """Check committed versions on every read; callers must authenticate first."""
        scopes = _scopes(domain, owner)
        versions = await repository.versions(scopes)
        identity = json.dumps(
            [domain, str(owner), role, auth_version, filters, versions],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        key = f"cache:read:v1:{domain}:{hashlib.sha256(identity.encode()).hexdigest()}"
        cached = await self._read(key, adapter)
        if cached is not None:
            return cached
        pending = self._pending.get(key)
        if pending is not None:
            try:
                value = await asyncio.wait_for(asyncio.shield(pending), timeout=0.25)
                if value is not None:
                    return adapter.validate_json(value)
            except TimeoutError, ValidationError:
                pass
            return await load()
        if len(self._pending) >= _MAX_PENDING:
            return await load()
        future: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            result = await load()
            value = adapter.dump_json(result)
            if len(value) <= _MAX_BYTES:
                if versions == await repository.versions(scopes):
                    try:
                        async with asyncio.timeout(0.1):
                            await self._redis.eval(
                                _STORE,
                                2,
                                "cache:read:v1:index",
                                key,
                                value,
                                _TTLS[domain],
                                _MAX_ENTRIES,
                            )
                    except RedisError, TimeoutError:
                        logger.warning("Read cache storage unavailable.")
                    future.set_result(value)
            return result
        finally:
            self._pending.pop(key, None)
            if not future.done():
                future.set_result(None)
