"""Credential races against actual PostgreSQL and Redis, without starting services."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from app.admin.deps import require_admin
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import ConflictError, ForbiddenError, RateLimitedError, UnauthorizedError
from app.core.jwt import create_access_token, decode_access_token
from app.core.redis import build_redis
from app.core.security import sha256_hex
from app.models import AdminSession, AuditLog, RefreshToken, User
from app.models.enums import UserRole
from app.repositories.auth_repository import AuthRepository
from app.repositories.user_repository import UserRepository
from app.services import otp_service
from app.services.auth_service import AuthService, validate_access_claims
from app.services.otp_service import OtpService
from sqlalchemy import delete, select, text
from starlette.requests import Request

from tests.conftest import make_test_settings


def settings():
    return make_test_settings(
        **{name: os.environ[name] for name in ("DATABASE_URL", "REDIS_URL") if os.environ.get(name)}
    )


@pytest_asyncio.fixture
async def redis_client():
    redis = build_redis(settings())
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis unavailable; run this regression in service-backed CI.")
    try:
        yield redis
    finally:
        await redis.aclose()


@pytest_asyncio.fixture
async def auth_database():
    engine = build_engine(settings())
    factory = build_session_factory(engine)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip("PostgreSQL unavailable; run this regression in service-backed CI.")
    user_id, family_id = uuid4(), uuid4()
    raw = uuid4().hex
    try:
        async with factory() as session:
            session.add(User(id=user_id, phone=f"test:{uuid4().hex[:14]}", role=UserRole.CUSTOMER))
            await session.flush()
            await AuthRepository(session).add_refresh_token(
                user_id=user_id,
                token_hash=sha256_hex(raw),
                family_id=family_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            await session.commit()
        yield factory, user_id, raw
    finally:
        async with factory() as session:
            await session.execute(delete(AuditLog).where(AuditLog.actor_user_id == user_id))
            await session.execute(delete(AdminSession).where(AdminSession.admin_user_id == user_id))
            await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


def auth_service(session):
    return AuthService(
        settings=settings(),
        redis=AsyncMock(),
        otp=AsyncMock(),
        users=UserRepository(session),
        auth_repo=AuthRepository(session),
        session=session,
    )


async def test_concurrent_admin_identity_edits_do_not_invert_session_user_locks(auth_database):
    factory, user_id, _ = auth_database
    cookies = [uuid4().hex, uuid4().hex]
    async with factory() as session:
        user = await session.get(User, user_id)
        user.role = UserRole.ADMIN
        session.add_all(
            [
                AdminSession(
                    admin_user_id=user_id,
                    session_token_hash=sha256_hex(cookie),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
                for cookie in cookies
            ]
        )
        await session.commit()

    authenticated = asyncio.Barrier(2)

    async def edit(cookie):
        async with factory() as session:
            await session.execute(text("SET LOCAL statement_timeout = '8s'"))
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "headers": [(b"cookie", f"admin_session={cookie}".encode())],
                    "path_params": {"table_name": "users"},
                    "app": SimpleNamespace(
                        state=SimpleNamespace(settings=settings(), redis=AsyncMock())
                    ),
                }
            )
            ctx = await require_admin(request, session)
            form = await ctx.tables.form("users", user_id)
            # With the old sliding policy, each request holds a different session
            # UPDATE lock here; neither may acquire one before locking the user.
            await authenticated.wait()
            try:
                await ctx.tables.save(
                    "users",
                    {"phone": f"new:{uuid4().hex[:15]}"},
                    admin_id=user_id,
                    session_id=ctx.session_row.id,
                    record_id=user_id,
                    revision=form.revision,
                )
                await session.commit()
                return "saved"
            except (ConflictError, ForbiddenError):
                await session.rollback()
                return "rejected"

    results = await asyncio.wait_for(
        asyncio.gather(*(edit(cookie) for cookie in cookies), return_exceptions=True), timeout=10
    )
    assert results.count("saved") == 1
    assert results.count("rejected") == 1, results
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user.auth_version == 1
        sessions = (
            await session.scalars(select(AdminSession).where(AdminSession.admin_user_id == user_id))
        ).all()
        assert all(row.revoked_at is not None for row in sessions)


async def test_concurrent_refresh_has_one_successor_and_replay_revocation_survives_rollback(
    auth_database,
):
    factory, user_id, raw = auth_database
    barrier = asyncio.Barrier(2)

    async def rotate():
        async with factory() as session:
            await barrier.wait()
            try:
                pair = await auth_service(session).refresh(raw)
                await session.commit()
                return pair
            except UnauthorizedError:
                await session.rollback()
                return None

    outcomes = await asyncio.wait_for(asyncio.gather(rotate(), rotate()), timeout=10)
    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1
    async with factory() as session:
        rows = list(
            (
                await session.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id))
            ).all()
        )
        assert len(rows) == 2
        assert all(row.revoked_at is not None for row in rows)
        with pytest.raises(UnauthorizedError):
            await auth_service(session).refresh(winners[0].refresh_token)


async def test_failed_rotation_rolls_back_consumption_and_can_be_retried(auth_database):
    factory, _, raw = auth_database
    async with factory() as session:
        abandoned = await auth_service(session).refresh(raw)
        await session.rollback()
    async with factory() as session:
        pair = await auth_service(session).refresh(raw)
        await session.commit()
        assert pair.refresh_token != abandoned.refresh_token
        assert (
            await AuthRepository(session).get_refresh_token(sha256_hex(abandoned.refresh_token))
            is None
        )


async def test_identity_change_revokes_all_credentials_and_new_identity_can_login(auth_database):
    factory, user_id, raw = auth_database
    access, _, _ = create_access_token(settings(), user_id=user_id, role="CUSTOMER")
    async with factory() as session:
        user = await UserRepository(session).get_for_update(user_id)
        user.phone = f"new:{uuid4().hex[:15]}"
        new_phone = user.phone
        await AuthRepository(session).invalidate_user_credentials(user_id, datetime.now(UTC))
        await session.commit()
    async with factory() as session:
        redis = AsyncMock()
        redis.get.return_value = None
        with pytest.raises(UnauthorizedError):
            await validate_access_claims(
                decode_access_token(settings(), access), redis=redis, users=UserRepository(session)
            )
        with pytest.raises(UnauthorizedError):
            await auth_service(session).refresh(raw)
        service = auth_service(session)
        service._otp.verify_otp.return_value = True
        result = await service.verify_otp(new_phone, "123456")
        assert result.tokens is not None
        claims = decode_access_token(settings(), result.tokens.access_token)
        assert claims.auth_version == 1
        await validate_access_claims(claims, redis=redis, users=UserRepository(session))


async def test_redis_otp_allows_exactly_one_concurrent_success(redis_client, monkeypatch):
    phone = f"test:{uuid4()}"
    monkeypatch.setattr(otp_service, "generate_otp", lambda: "123456")
    service = OtpService(redis_client, AsyncMock(), settings())
    try:
        await service.request_otp(phone)
        results = await asyncio.gather(*(service.verify_otp(phone, "123456") for _ in range(20)))
        assert results.count(True) == 1
    finally:
        await redis_client.delete(
            *(f"otp:{kind}:{phone}" for kind in ("code", "attempts", "rate", "block"))
        )


async def test_redis_otp_replacement_survives_stale_verification(redis_client, monkeypatch):
    phone = f"test:{uuid4()}"
    codes = iter(("123456", "654321"))
    monkeypatch.setattr(otp_service, "generate_otp", lambda: next(codes))
    sms = AsyncMock()
    service = OtpService(redis_client, sms, settings())
    try:
        await service.request_otp(phone)
        delivered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_delivery(phone, code):
            delivered.set()
            await release.wait()

        sms.send_otp.side_effect = delayed_delivery
        issuing = asyncio.create_task(service.request_otp(phone))
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
            assert await service.verify_otp(phone, "123456") is False
            assert await service.verify_otp(phone, "654321") is True
        finally:
            release.set()
            await issuing
    finally:
        await redis_client.delete(
            *(f"otp:{kind}:{phone}" for kind in ("code", "attempts", "rate", "block"))
        )


async def test_redis_otp_attempt_limit_is_atomic(redis_client, monkeypatch):
    phone = f"test:{uuid4()}"
    monkeypatch.setattr(otp_service, "generate_otp", lambda: "123456")
    service = OtpService(redis_client, AsyncMock(), settings())
    try:
        await service.request_otp(phone)
        results = await asyncio.gather(
            *(service.verify_otp(phone, "000000") for _ in range(6)), return_exceptions=True
        )
        assert sum(isinstance(result, RateLimitedError) for result in results) == 1
        assert await service.verify_otp(phone, "123456") is False
    finally:
        await redis_client.delete(
            *(f"otp:{kind}:{phone}" for kind in ("code", "attempts", "rate", "block"))
        )
