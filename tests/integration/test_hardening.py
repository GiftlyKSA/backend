"""Phase 14 hardening: rate limiting, body-size guard, and the readiness probe."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from app.core.config import Settings
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import DomainError
from app.core.jwt import create_access_token
from app.core.ratelimit import RateLimiter
from app.core.redis import build_redis
from app.main import create_app
from app.models import User
from sqlalchemy import select, text

from tests.conftest import make_test_settings


def _settings(**overrides: object) -> Settings:
    if os.environ.get("DATABASE_URL"):
        overrides.setdefault("DATABASE_URL", os.environ["DATABASE_URL"])
    if os.environ.get("REDIS_URL"):
        overrides.setdefault("REDIS_URL", os.environ["REDIS_URL"])
    return make_test_settings(**overrides)


async def _skip_unless_db(settings: Settings) -> None:
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as s:
            await s.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database unavailable: {exc}")
    finally:
        await engine.dispose()


class _BoomRedis:
    """A Redis stand-in whose every call raises, to exercise the outage path."""

    async def eval(self, *args: object) -> int:
        raise RuntimeError("redis down")


class _GuardRedis:
    """A Redis script stand-in for the combined WS guard."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.args: tuple[object, ...] = ()

    async def eval(self, *args: object) -> int:
        self.args = args
        return self.result


async def test_rate_limiter_allows_then_blocks() -> None:
    settings = _settings()
    redis = build_redis(settings)
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        await redis.aclose()
        pytest.skip(f"redis unavailable: {exc}")

    limiter = RateLimiter(redis, max_requests=3, window_seconds=60)
    identity = f"test:{uuid.uuid4()}"  # unique key so the shared Redis stays hermetic
    try:
        for _ in range(3):
            decision = await limiter.check(identity)
            assert decision.allowed is True
        blocked = await limiter.check(identity)
        assert blocked.allowed is False
        assert 0 < blocked.retry_after_seconds <= 60
    finally:
        await redis.delete(f"ratelimit:{identity}")
        await redis.aclose()


@pytest.mark.parametrize("guarded,limit", [(False, 100), (True, 120)])
async def test_rate_limiter_shares_quota_and_reports_subsecond_expiry(guarded, limit) -> None:
    settings = _settings()
    first = build_redis(settings)
    second = build_redis(settings)
    identity = f"test:{uuid.uuid4()}"
    counter = f"ratelimit:{identity}"
    blocked_key = f"test:deny:{uuid.uuid4()}"
    try:
        try:
            await first.ping()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Redis unavailable: {exc}")
        limiters = [
            RateLimiter(client, max_requests=limit, window_seconds=60) for client in (first, second)
        ]

        async def check(index: int):
            limiter = limiters[index % 2]
            if guarded:
                return await limiter.check_guarded(identity, blocked_key=blocked_key)
            return await limiter.check(identity)

        decisions = await asyncio.gather(*(check(index) for index in range(limit + 1)))
        assert sum(decision.allowed for decision in decisions) == limit
        assert all(not decision.unavailable for decision in decisions)
        await first.pexpire(counter, 400)
        blocked = await check(0)
        assert not blocked.allowed
        assert blocked.retry_after_seconds == 1
        await first.pexpire(counter, 1)
        await asyncio.sleep(0.01)
        assert (await check(1)).allowed
    finally:
        await first.aclose()
        await second.aclose()


async def test_rate_limiter_fails_closed_on_backend_error() -> None:
    limiter = RateLimiter(_BoomRedis(), max_requests=1, window_seconds=60)  # type: ignore[arg-type]
    decision = await limiter.check("whoever")
    assert decision.allowed is False
    assert decision.unavailable is True
    assert decision.retry_after_seconds == 0


async def test_guarded_rate_limiter_denies_revoked_sessions_and_repairs_expiry() -> None:
    redis = build_redis(_settings())
    identity = f"test:{uuid.uuid4()}"
    counter = f"ratelimit:{identity}"
    blocked_key = f"test:deny:{uuid.uuid4()}"
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        await redis.aclose()
        pytest.skip(f"Redis unavailable: {exc}")

    try:
        limiter = RateLimiter(redis, max_requests=3, window_seconds=60)
        await redis.set(blocked_key, "1", ex=60)
        denied = await limiter.check_guarded(identity, blocked_key=blocked_key)
        assert denied.blocked
        assert not denied.allowed
        assert await redis.get(counter) is None

        await redis.delete(blocked_key)
        await redis.set(counter, 3)
        limited = await limiter.check_guarded(identity, blocked_key=blocked_key)
        assert not limited.allowed
        assert not limited.blocked
        assert limited.retry_after_seconds == 60
        assert 0 < await redis.pttl(counter) <= 60_000
    finally:
        try:
            await redis.delete(counter, blocked_key)
        finally:
            await redis.aclose()


async def test_guarded_rate_limiter_checks_ban_and_window_in_one_round_trip() -> None:
    redis = _GuardRedis(-1)
    limiter = RateLimiter(redis, max_requests=3, window_seconds=60)  # type: ignore[arg-type]
    decision = await limiter.check_guarded("ws:user", blocked_key="auth:banned:user")

    assert decision.blocked is True
    assert decision.allowed is False
    assert redis.args[1:4] == (2, "ratelimit:ws:user", "auth:banned:user")


async def test_guarded_rate_limiter_fails_closed_on_backend_error() -> None:
    limiter = RateLimiter(_BoomRedis(), max_requests=1, window_seconds=60)  # type: ignore[arg-type]
    decision = await limiter.check_guarded("ws:user", blocked_key="auth:banned:user")

    assert decision.unavailable is True
    assert decision.blocked is False
    assert decision.allowed is False


async def test_rate_limit_middleware_returns_429_with_retry_after() -> None:
    from httpx import ASGITransport, AsyncClient

    settings = _settings(
        RATE_LIMIT_ENABLED=True, RATE_LIMIT_MAX_REQUESTS=3, RATE_LIMIT_WINDOW_SECONDS=60
    )
    await _skip_unless_db(settings)
    app = create_app(settings)
    # A token for a random id gives this test its own throttle bucket (user:<uuid>),
    # isolated from every other test sharing this Redis.
    token, _jti, _ttl = create_access_token(settings, user_id=uuid.uuid4(), role="CUSTOMER")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            statuses = [
                (await client.get("/api/users/me", headers=headers)).status_code for _ in range(4)
            ]
            assert statuses[:3].count(429) == 0
            last = await client.get("/api/users/me", headers=headers)
            assert last.status_code == 429
            assert int(last.headers["Retry-After"]) > 0
            assert last.json()["error"]["code"] == "RATE_LIMITED"
            # The security-header stamp still wraps a throttled response.
            assert last.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def test_body_size_guard_rejects_oversized_request() -> None:
    from httpx import ASGITransport, AsyncClient

    settings = _settings(MAX_REQUEST_BODY_BYTES=10)
    await _skip_unless_db(settings)
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post("/api/auth/send-otp", json={"phone": "+966500000000"})
            assert resp.status_code == 413
            assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def test_readiness_probe_reports_dependencies() -> None:
    from httpx import ASGITransport, AsyncClient

    settings = _settings()
    await _skip_unless_db(settings)
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/health/ready")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "ready"
            assert body["checks"] == {"database": "ok", "redis": "ok"}
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def test_readiness_probe_is_not_throttled() -> None:
    from httpx import ASGITransport, AsyncClient

    settings = _settings(
        RATE_LIMIT_ENABLED=True, RATE_LIMIT_MAX_REQUESTS=1, RATE_LIMIT_WINDOW_SECONDS=60
    )
    await _skip_unless_db(settings)
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(3):
                resp = await client.get("/api/health/ready")
                assert resp.status_code == 200
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def test_database_command_deadline_rolls_back_and_returns_connection() -> None:
    settings = _settings(DB_COMMAND_TIMEOUT_SECONDS=0.2, DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0)
    await _skip_unless_db(settings)
    engine = build_engine(settings)
    try:
        with pytest.raises(DomainError) as caught:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT pg_sleep(1)"))
        assert caught.value.code == "DATABASE_TIMEOUT"
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


async def test_database_lock_deadline_is_transaction_local() -> None:
    settings = _settings(DB_LOCK_TIMEOUT_MS=150, DB_COMMAND_TIMEOUT_SECONDS=3, DB_POOL_SIZE=1)
    await _skip_unless_db(settings)
    locker = build_engine(_settings())
    engine = build_engine(settings)
    lock = text("SELECT pg_advisory_xact_lock(:key)")
    params = {"key": uuid.uuid4().int % (2**63)}
    try:
        async with locker.begin() as holding:
            await holding.execute(lock, params)
            async with engine.connect() as waiting:
                driver = (await waiting.get_raw_connection()).driver_connection
                original = await driver.fetchval("SHOW lock_timeout")
                with pytest.raises(DomainError) as caught:
                    async with waiting.begin():
                        await waiting.execute(text("SET TRANSACTION READ ONLY"))
                        assert await waiting.scalar(text("SHOW lock_timeout")) == "150ms"
                        async with asyncio.timeout(1.5):
                            await waiting.execute(lock, params)
                assert caught.value.code == "DATABASE_TIMEOUT"
                assert await driver.fetchval("SHOW lock_timeout") == original
                async with waiting.begin():
                    assert await waiting.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
        await locker.dispose()
