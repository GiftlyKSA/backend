"""Real Redis OTP isolation and PostgreSQL races for sensitive account credentials."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import ConflictError, RateLimitedError, UnauthorizedError
from app.core.redis import build_redis
from app.models import AuditLog, User
from app.models.account_change import AccountChangeChallenge
from app.services.otp_service import OtpService
from sqlalchemy import delete, select

from tests.conftest import make_test_settings
from tests.integration.test_account_changes import proof, seed_user, service


def settings():
    return make_test_settings(
        **{name: os.environ[name] for name in ("DATABASE_URL", "REDIS_URL") if os.environ.get(name)}
    )


@pytest_asyncio.fixture
async def account_race_database():
    engine = build_engine(settings())
    factory = build_session_factory(engine)
    owner_ids = []
    try:
        async with factory() as session:
            first = await seed_user(session)
            second = await seed_user(session)
            owner_ids = [first.id, second.id]
            await session.commit()
        yield factory, owner_ids
    finally:
        if owner_ids:
            async with factory() as session:
                await session.execute(
                    delete(AccountChangeChallenge).where(
                        AccountChangeChallenge.user_id.in_(owner_ids)
                    )
                )
                await session.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(owner_ids)))
                await session.execute(delete(User).where(User.id.in_(owner_ids)))
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_current_phone_proof_consumed_once(account_race_database) -> None:
    factory, owner_ids = account_race_database
    actor_id = owner_ids[0]
    async with factory() as session:
        owner = await session.get(User, actor_id)
        token = await proof(session, owner, "PHONE_CHANGE")
        await session.commit()

    async def attempt(phone):
        async with factory() as session:
            try:
                challenge, _ = await service(session).request_phone(actor_id, phone, token)
                await session.commit()
                return challenge.id
            except UnauthorizedError:
                await session.rollback()
                return None

    results = await asyncio.gather(attempt("+966508111111"), attempt("+966508222222"))
    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_concurrent_different_accounts_cannot_claim_same_new_phone(
    account_race_database,
) -> None:
    factory, owner_ids = account_race_database
    target = f"+9665{uuid4().int % 100_000_000:08d}"
    challenge_ids = []
    async with factory() as session:
        for owner_id in owner_ids:
            row = AccountChangeChallenge(
                id=uuid4(),
                user_id=owner_id,
                phone=target,
                purpose="NEW_PHONE",
                auth_version=0,
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
            )
            session.add(row)
            challenge_ids.append(row.id)
        await session.commit()
    reached = 0
    barrier = asyncio.Event()

    async def attempt(owner_id, challenge_id):
        nonlocal reached
        async with factory() as session:
            workflow = service(session)
            original = workflow._users.get_by_phone

            async def lookup(phone):
                nonlocal reached
                result = await original(phone)
                reached += 1
                if reached == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 5)
                return result

            workflow._users.get_by_phone = lookup
            try:
                await workflow.confirm_phone(owner_id, challenge_id, "123456")
                await session.commit()
                return True
            except ConflictError:
                await session.rollback()
                return False

    results = await asyncio.gather(
        *(
            attempt(owner, challenge)
            for owner, challenge in zip(owner_ids, challenge_ids, strict=True)
        )
    )
    assert sorted(results) == [False, True]
    async with factory() as session:
        phones = list(await session.scalars(select(User.phone).where(User.id.in_(owner_ids))))
        assert phones.count(target) == 1


@pytest.mark.asyncio
async def test_real_redis_scoped_otp_replay_expiry_attempts_and_cooldown() -> None:
    redis = build_redis(settings())
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        pytest.skip("Redis unavailable; run OTP boundaries in service-backed CI.")
    phone = f"+9665{uuid4().int % 100_000_000:08d}"
    scope = f"account-change:{uuid4()}"
    sms = AsyncMock()
    otp = OtpService(redis, sms, settings())
    code_key = f"otp:code:{scope}:{phone}"
    attempts_key = f"otp:attempts:{scope}:{phone}"
    try:
        await otp.request_otp(phone, scope=scope, ttl_seconds=60)
        code = sms.send_otp.await_args.args[1]
        assert 0 < await redis.ttl(code_key) <= 60
        assert await redis.get(code_key) != code.encode()
        assert not await otp.verify_otp(phone, code)
        assert not await otp.verify_otp(phone, code, scope="foreign-scope")
        with pytest.raises(RateLimitedError):
            await otp.request_otp(phone, scope=f"account-change:{uuid4()}")
        assert await otp.verify_otp(phone, code, scope=scope)
        assert not await otp.verify_otp(phone, code, scope=scope)
        await redis.delete(f"otp:cooldown:{phone}")
        await otp.request_otp(phone, scope=scope, ttl_seconds=60)
        await redis.delete(code_key)
        assert not await otp.verify_otp(phone, sms.send_otp.await_args.args[1], scope=scope)
        await redis.delete(f"otp:cooldown:{phone}")
        await otp.request_otp(phone, scope=scope, ttl_seconds=60)
        code = sms.send_otp.await_args.args[1]
        wrong = "000000" if code != "000000" else "000001"
        for _ in range(5):
            assert not await otp.verify_otp(phone, wrong, scope=scope)
        with pytest.raises(RateLimitedError):
            await otp.verify_otp(phone, code, scope=scope)
        assert await redis.exists(code_key) == 0
    finally:
        await redis.delete(
            code_key,
            attempts_key,
            f"otp:cooldown:{phone}",
            f"otp:rate:{phone}",
            f"otp:block:{phone}",
        )
        await redis.aclose()
