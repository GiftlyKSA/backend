"""Device registration quota and ownership boundaries."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from app.core.db import build_engine, build_session_factory
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.main import create_app
from app.models import DeviceToken, User
from app.models.enums import DeviceOs, UserRole, UserStatus
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.device_service import DeviceLimitReachedError, DeviceService
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_test_settings


async def test_device_quota_rejects_growth_but_allows_refresh(db_session: AsyncSession) -> None:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    app = create_app(make_test_settings())
    app.dependency_overrides[require_auth] = lambda: Actor(user.id, UserRole.CUSTOMER, "test")

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for index in range(10):
                response = await client.post(
                    "/api/devices", json={"token": f"quota-{index}", "device_os": "IOS"}
                )
                assert response.status_code == 201, response.text
            response = await client.post(
                "/api/devices", json={"token": "quota-0", "device_os": "ANDROID"}
            )
            assert response.status_code == 201
            response = await client.post(
                "/api/devices", json={"token": "quota-extra", "device_os": "IOS"}
            )
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "DEVICE_LIMIT_REACHED"
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


async def test_quota_reassignment_does_not_steal_token_when_full(db_session: AsyncSession) -> None:
    users = [
        User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
        for _ in range(2)
    ]
    db_session.add_all(users)
    await db_session.flush()
    repo = DeviceTokenRepository(db_session)
    service = DeviceService(devices=repo, users=UserRepository(db_session))
    await service.register(user_id=users[0].id, token="handed-down", device_os=DeviceOs.IOS)
    for index in range(10):
        await service.register(user_id=users[1].id, token=f"device-{index}", device_os=DeviceOs.IOS)
    with pytest.raises(DeviceLimitReachedError):
        await service.register(user_id=users[1].id, token="handed-down", device_os=DeviceOs.IOS)
    assert await repo.tokens_for_user(users[0].id) == ["handed-down"]
    await service.remove(user_id=users[1].id, token="device-0")
    await service.register(user_id=users[1].id, token="handed-down", device_os=DeviceOs.ANDROID)
    await service.remove(user_id=users[0].id, token="handed-down")
    assert await repo.tokens_for_user(users[0].id) == []
    assert "handed-down" in await repo.tokens_for_user(users[1].id)


@pytest.mark.parametrize("change", ["banned", "deleted", "erased", "admin"])
async def test_device_registration_rechecks_locked_account(
    db_session: AsyncSession,
    change: str,
) -> None:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    repo = DeviceTokenRepository(db_session)
    service = DeviceService(devices=repo, users=UserRepository(db_session))
    await service.register(user_id=user.id, token="fresh-account", device_os=DeviceOs.IOS)
    if change == "banned":
        user.status = UserStatus.BANNED
    elif change == "deleted":
        user.status = UserStatus.DELETED
    elif change == "erased":
        user.deleted_at = datetime.now(UTC)
    else:
        user.role = UserRole.ADMIN
    await db_session.flush()
    with pytest.raises(ForbiddenError if change == "admin" else UnauthorizedError):
        await service.register(user_id=user.id, token="fresh-account", device_os=DeviceOs.ANDROID)
    stored = await db_session.scalar(select(DeviceToken).where(DeviceToken.user_id == user.id))
    assert stored.device_os is DeviceOs.IOS


async def test_pending_courier_can_register_for_account_notifications(
    db_session: AsyncSession,
) -> None:
    user = User(
        phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
        role=UserRole.COURIER,
        status=UserStatus.PENDING_VERIFICATION,
    )
    db_session.add(user)
    await db_session.flush()
    service = DeviceService(
        devices=DeviceTokenRepository(db_session), users=UserRepository(db_session)
    )
    token = await service.register(user_id=user.id, token="pending-device", device_os=DeviceOs.IOS)
    assert token.user_id == user.id


async def test_device_registration_waiting_for_lock_observes_committed_ban() -> None:
    settings = make_test_settings(
        DATABASE_URL=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
        )
    )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    user_id = uuid.uuid4()
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")
    try:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
                    role=UserRole.CUSTOMER,
                )
            )
            await session.commit()
        started = asyncio.Event()

        async def register() -> None:
            async with factory() as session:
                service = DeviceService(
                    devices=DeviceTokenRepository(session), users=UserRepository(session)
                )
                started.set()
                await service.register(
                    user_id=user_id, token=f"blocked-{user_id}", device_os=DeviceOs.IOS
                )
                await session.commit()

        async with factory() as locker:
            user = await UserRepository(locker).get_for_update(user_id)
            assert user is not None
            user.status = UserStatus.BANNED
            await locker.flush()
            task = asyncio.create_task(register())
            await started.wait()
            await asyncio.sleep(0.05)
            assert not task.done()
            await locker.commit()
        with pytest.raises(UnauthorizedError):
            await asyncio.wait_for(task, timeout=5)
        async with factory() as session:
            assert await DeviceTokenRepository(session).tokens_for_user(user_id) == []
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


async def test_concurrent_device_registrations_cannot_overfill_last_slot() -> None:
    settings = make_test_settings(
        DATABASE_URL=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
        )
    )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    user_id = uuid.uuid4()
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")
    try:
        async with factory() as session:
            user = User(
                id=user_id,
                phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
                role=UserRole.CUSTOMER,
            )
            session.add(user)
            await session.flush()
            for index in range(9):
                await DeviceTokenRepository(session).register(
                    user_id=user_id, token=f"{user_id}-initial-{index}", device_os=DeviceOs.IOS
                )
            await session.commit()

        async def register(index: int) -> bool:
            async with factory() as session:
                service = DeviceService(
                    devices=DeviceTokenRepository(session), users=UserRepository(session)
                )
                try:
                    await service.register(
                        user_id=user_id, token=f"{user_id}-race-{index}", device_os=DeviceOs.IOS
                    )
                    await session.commit()
                    return True
                except DeviceLimitReachedError:
                    await session.rollback()
                    return False

        results = await asyncio.gather(*(register(index) for index in range(12)))
        assert sum(results) == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DeviceToken)
                    .where(DeviceToken.user_id == user_id)
                )
                == 10
            )
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()
