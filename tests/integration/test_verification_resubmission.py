"""Real PostgreSQL moderation locks and duplicate verification resubmission."""

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from app.core.exceptions import ConflictError
from app.models import AuditLog, CourierProfile, User, Wallet
from app.models.enums import UserRole, UserStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_repository import UserRepository
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.user_service import UserService
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.conftest import city_by_name
from tests.integration.test_orders_api import _make_stack


@pytest_asyncio.fixture
async def resubmission_database() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], object]]:
    _, engine, factory = await _make_stack()
    owner_id = None
    try:
        async with factory() as session:
            owner = User(
                phone=f"+9665{uuid4().int % 100_000_000:08d}",
                role=UserRole.COURIER,
                status=UserStatus.REJECTED,
            )
            session.add(owner)
            await session.flush()
            owner_id = owner.id
            session.add(
                CourierProfile(
                    user_id=owner_id,
                    city=await city_by_name(session, "Jeddah"),
                    national_id_encrypted="synthetic-test-ciphertext",
                    is_verified=False,
                    verification_rejection_reason="Synthetic previous decision",
                )
            )
            await session.commit()
        yield factory, owner_id
    finally:
        if owner_id is not None:
            async with factory() as session:
                await session.execute(delete(AuditLog).where(AuditLog.actor_user_id == owner_id))
                await session.execute(delete(Wallet).where(Wallet.user_id == owner_id))
                await session.execute(delete(User).where(User.id == owner_id))
                await session.commit()
        await engine.dispose()


def workflow(session, users=None):
    return UserService(
        users=users if users is not None else UserRepository(session),
        audit=AuditRepository(session),
        eligibility=cast(CourierEligibilityService, None),
    )


@pytest.mark.parametrize("status", [UserStatus.BANNED, UserStatus.DELETED])
async def test_resubmission_refreshes_previously_loaded_account(resubmission_database, status):
    factory, actor_id = resubmission_database
    async with factory() as stale, factory() as moderator:
        user, profile = await workflow(stale).get_me(actor_id)
        assert user.status is UserStatus.REJECTED
        current = await UserRepository(moderator).get_for_update(actor_id)
        current.status = status
        current.auth_version += 1
        await moderator.commit()
        with pytest.raises(ConflictError):
            await workflow(stale).resubmit_courier_verification(actor_id=actor_id)
        await stale.rollback()
    async with factory() as check:
        assert (await check.get(User, actor_id)).status is status
        assert (await check.get(CourierProfile, actor_id)).verification_rejection_reason == (
            "Synthetic previous decision"
        )


async def test_resubmission_waits_for_moderation_commit(resubmission_database):
    factory, actor_id = resubmission_database
    attempting_lock = asyncio.Event()

    class ObservedUsers(UserRepository):
        async def get_for_update(self, user_id):
            attempting_lock.set()
            return await super().get_for_update(user_id)

    async with factory() as requester, factory() as moderator:
        await workflow(requester).get_me(actor_id)
        current = await UserRepository(moderator).get_for_update(actor_id)
        current.status = UserStatus.BANNED
        current.auth_version += 1
        await moderator.flush()
        attempt = asyncio.create_task(
            workflow(requester, ObservedUsers(requester)).resubmit_courier_verification(
                actor_id=actor_id
            )
        )
        observed = asyncio.create_task(attempting_lock.wait())
        try:
            async with asyncio.timeout(10):
                await asyncio.wait({attempt, observed}, return_when=asyncio.FIRST_COMPLETED)
                assert attempting_lock.is_set(), "Resubmission bypassed the moderation lock"
                assert not attempt.done(), "Resubmission did not wait for moderation"
                await moderator.commit()
                with pytest.raises(ConflictError):
                    await attempt
        finally:
            await moderator.rollback()
            for pending in (attempt, observed):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(attempt, observed, return_exceptions=True)
            await requester.rollback()


async def test_simultaneous_resubmissions_commit_only_one_transition(resubmission_database):
    factory, actor_id = resubmission_database
    start = asyncio.Event()

    async def attempt():
        await start.wait()
        async with factory() as session:
            try:
                await workflow(session).resubmit_courier_verification(actor_id=actor_id)
                await session.commit()
                return True
            except ConflictError:
                await session.rollback()
                return False

    requests = [asyncio.create_task(attempt()) for _ in range(2)]
    start.set()
    async with asyncio.timeout(10):
        results = await asyncio.gather(*requests)
    assert sorted(results) == [False, True]
    async with factory() as check:
        assert (await check.get(User, actor_id)).status is UserStatus.PENDING_VERIFICATION
        assert (
            await check.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.actor_user_id == actor_id,
                    AuditLog.action == "COURIER_VERIFICATION_RESUBMIT",
                )
            )
            == 1
        )


async def test_profile_edit_and_resubmission_share_user_first_lock(resubmission_database):
    factory, actor_id = resubmission_database
    start = asyncio.Event()
    locks = []

    class ObservedUsers(UserRepository):
        async def get_for_update(self, user_id):
            locks.append(user_id)
            return await super().get_for_update(user_id)

    async def edit_profile():
        await start.wait()
        async with factory() as session:
            await workflow(session, ObservedUsers(session)).update_me(
                actor_id,
                full_name="Synthetic updated name",
                email=None,
                dob=None,
                courier_city=None,
                courier_bio="Synthetic updated bio",
                supplied={"full_name", "courier_bio"},
            )
            await session.commit()

    async def resubmit():
        await start.wait()
        async with factory() as session:
            await workflow(session, ObservedUsers(session)).resubmit_courier_verification(
                actor_id=actor_id
            )
            await session.commit()

    pending = [asyncio.create_task(edit_profile()), asyncio.create_task(resubmit())]
    start.set()
    async with asyncio.timeout(10):
        await asyncio.gather(*pending)
    assert locks == [actor_id, actor_id]
    async with factory() as session:
        owner, profile = await workflow(session).get_me(actor_id)
        assert owner.status is UserStatus.PENDING_VERIFICATION
        assert owner.full_name == "Synthetic updated name"
        assert profile.bio == "Synthetic updated bio"
