"""Shared, expiring WebSocket connection leases."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

ACCOUNT_LIMIT = 8
GLOBAL_LIMIT = 10_000
LEASE_SECONDS = 30
_GLOBAL_KEY = "ws:connections:all"

_ACQUIRE = """
local now = redis.call('TIME')
local current = now[1] * 1000 + math.floor(now[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', current)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', current)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 1 end
local expires = current + tonumber(ARGV[4])
redis.call('ZADD', KEYS[1], expires, ARGV[1])
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[4]))
return 2
"""

_RENEW = """
local now = redis.call('TIME')
local current = now[1] * 1000 + math.floor(now[2] / 1000)
local a = redis.call('ZSCORE', KEYS[1], ARGV[1])
local g = redis.call('ZSCORE', KEYS[2], ARGV[1])
if not a or not g or tonumber(a) <= current or tonumber(g) <= current then
    redis.call('ZREM', KEYS[1], ARGV[1])
    redis.call('ZREM', KEYS[2], ARGV[1])
    return 0
end
local expires = current + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], expires, ARGV[1])
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]))
return 1
"""

_RELEASE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
"""


class WebSocketLease:
    """A Redis lease counted against per-account and global socket ceilings."""

    def __init__(self, redis: Redis, account_id: uuid.UUID) -> None:
        """Create a unique lease for one account's connection."""
        self.redis = redis
        self.keys = (f"ws:connections:account:{account_id}", _GLOBAL_KEY)
        self.token = str(uuid.uuid4())

    async def acquire(self) -> bool:
        """Return true only when both shared ceilings have room."""
        result = await self.redis.eval(
            _ACQUIRE,
            2,
            *self.keys,
            self.token,
            ACCOUNT_LIMIT,
            GLOBAL_LIMIT,
            LEASE_SECONDS * 1000,
        )
        return bool(result == 2)

    async def renew(self) -> bool:
        """Extend an existing lease, never recreate an expired one."""
        result = await self.redis.eval(_RENEW, 2, *self.keys, self.token, LEASE_SECONDS * 1000)
        return bool(result == 1)

    async def release(self) -> None:
        """Remove this socket from both counts without affecting other sockets."""
        await self.redis.eval(_RELEASE, 2, *self.keys, self.token)
