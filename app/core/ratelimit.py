"""Fixed-window request rate limiting backed by Redis.

A single Redis counter per identity per window, advanced by one atomic Lua eval —
``INCR``, TTL set (or repair), and ceiling check in one round trip, so a crash can
never strand a counter without an expiry. The design mirrors the OTP throttle so
there is one throttling idiom in the codebase. Redis outages fail closed with an
explicit unavailable decision; HTTP callers render a stable 503 while health probes
remain exempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis

_logger = logging.getLogger("app.ratelimit")

# One atomic round trip (audit SEC-6/PERF-5): INCR, set/repair the TTL (a TTL of -1
# means a crash stranded the counter without an expiry), and return the retry-after
# seconds when over the ceiling, else 0.
_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if count > tonumber(ARGV[2]) then
    local ttl = redis.call('TTL', KEYS[1])
    if ttl > 0 then return ttl end
    return tonumber(ARGV[1])
end
return 0
"""

# WebSocket frames need both a ban check and a rate-limit decision. Keeping the ban
# key as KEYS[2] makes the script Redis Cluster-safe while collapsing both guards into
# one round trip.
_GUARDED_WINDOW_LUA = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return -1
end
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if count > tonumber(ARGV[2]) then
    local ttl = redis.call('TTL', KEYS[1])
    if ttl > 0 then return ttl end
    return tonumber(ARGV[1])
end
return 0
"""


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of a rate-limit check.

    Attributes:
        allowed: Whether the request may proceed.
        retry_after_seconds: Seconds until the window resets (0 when allowed).
        blocked: Whether a separate security guard denied the identity.
        unavailable: Whether the shared throttle could not validate the request.
    """

    allowed: bool
    retry_after_seconds: int
    blocked: bool = False
    unavailable: bool = False


class RateLimiter:
    """A fixed-window per-identity request throttle over Redis."""

    def __init__(self, redis: Redis, *, max_requests: int, window_seconds: int) -> None:
        """Hold the Redis client and the window tuning."""
        self._redis = redis
        self._max = max_requests
        self._window = window_seconds

    def _key(self, identity: str) -> str:
        return f"ratelimit:{identity}"

    async def check(self, identity: str) -> RateLimitDecision:
        """Count this request against ``identity`` and decide whether to allow it.

        Returns:
            An allow decision under the ceiling, otherwise a deny carrying the
            seconds until the current window expires. A backend error denies the
            request with an explicit unavailable decision.
        """
        key = self._key(identity)
        try:
            retry_after = int(await self._redis.eval(_WINDOW_LUA, 1, key, self._window, self._max))
            if retry_after > 0:
                return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)
            return RateLimitDecision(allowed=True, retry_after_seconds=0)
        except Exception:  # noqa: BLE001 - dependency failures must not disable protection.
            _logger.warning("Rate-limit backend unavailable; denying request")
            return RateLimitDecision(allowed=False, retry_after_seconds=0, unavailable=True)

    async def check_guarded(self, identity: str, *, blocked_key: str) -> RateLimitDecision:
        """Check a Redis security flag and throttle in one atomic round trip.

        If Redis cannot confirm that a live WebSocket identity is permitted, the
        caller should close the socket rather than accept messages from a potentially
        revoked session.
        """
        key = self._key(identity)
        try:
            result = int(
                await self._redis.eval(
                    _GUARDED_WINDOW_LUA,
                    2,
                    key,
                    blocked_key,
                    self._window,
                    self._max,
                )
            )
        except Exception:  # noqa: BLE001 - security guard deliberately fails closed.
            _logger.warning("Guarded rate-limit backend unavailable; denying", exc_info=True)
            return RateLimitDecision(allowed=False, retry_after_seconds=0, blocked=True)
        if result == -1:
            return RateLimitDecision(allowed=False, retry_after_seconds=0, blocked=True)
        if result > 0:
            return RateLimitDecision(allowed=False, retry_after_seconds=result)
        return RateLimitDecision(allowed=True, retry_after_seconds=0)
