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
from itertools import count
from time import perf_counter

from redis.asyncio import Redis

from app.core.middleware import current_request_id

_logger = logging.getLogger("app.ratelimit")
_decision_samples = count()
_SAMPLE_EVERY = 256

_WINDOW_LUA = """
local count = redis.call('INCR', KEYS[1])
local window = tonumber(ARGV[1]) * 1000
local ttl = -1
if count > 1 then
    ttl = redis.call('PTTL', KEYS[1])
end
if ttl < 0 then
    redis.call('PEXPIRE', KEYS[1], window)
    ttl = window
end
if count > tonumber(ARGV[2]) then
    return math.max(1, math.ceil(ttl / 1000))
end
return 0
"""

# The counter and denylist check share one script on the current Redis instance.
_GUARDED_WINDOW_LUA = (
    """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return -1
end
"""
    + _WINDOW_LUA
)


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

    def __init__(
        self, redis: Redis, *, max_requests: int, window_seconds: int, scope: str = "shared"
    ) -> None:
        """Hold the Redis client and the window tuning."""
        self._redis = redis
        self._max = max_requests
        self._window = window_seconds
        self._scope = scope

    def _log_failure(self, exc: Exception, started: float) -> None:
        _logger.warning(
            "Rate-limit backend unavailable (%s); denying request",
            type(exc).__name__,
            extra={
                "request_id": current_request_id(),
                "extra_fields": {
                    "scope": self._scope,
                    "outcome": "unavailable",
                    "exception_type": type(exc).__name__,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                },
            },
        )

    def _sample_decision(self, retry_after: int, started: float) -> None:
        if next(_decision_samples) % _SAMPLE_EVERY != 0:
            return
        _logger.info(
            "Sampled rate-limit decision",
            extra={
                "request_id": current_request_id(),
                "extra_fields": {
                    "scope": self._scope,
                    "outcome": "limited" if retry_after > 0 else "allowed",
                    "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                    "sample_every": _SAMPLE_EVERY,
                },
            },
        )

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
        started = perf_counter()
        try:
            retry_after = int(await self._redis.eval(_WINDOW_LUA, 1, key, self._window, self._max))
            self._sample_decision(retry_after, started)
            if retry_after > 0:
                return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)
            return RateLimitDecision(allowed=True, retry_after_seconds=0)
        except Exception as exc:  # noqa: BLE001 - dependency failures must not disable protection.
            self._log_failure(exc, started)
            return RateLimitDecision(allowed=False, retry_after_seconds=0, unavailable=True)

    async def check_guarded(self, identity: str, *, blocked_key: str) -> RateLimitDecision:
        """Check a Redis security flag and throttle in one atomic round trip.

        If Redis cannot confirm that a live WebSocket identity is permitted, the
        caller should close the socket rather than accept messages from a potentially
        revoked session.
        """
        key = self._key(identity)
        started = perf_counter()
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
        except Exception as exc:  # noqa: BLE001 - security guard deliberately fails closed.
            self._log_failure(exc, started)
            return RateLimitDecision(allowed=False, retry_after_seconds=0, unavailable=True)
        if result == -1:
            return RateLimitDecision(allowed=False, retry_after_seconds=0, blocked=True)
        self._sample_decision(result, started)
        if result > 0:
            return RateLimitDecision(allowed=False, retry_after_seconds=result)
        return RateLimitDecision(allowed=True, retry_after_seconds=0)
