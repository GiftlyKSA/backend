"""Verification resubmission must respect fresh moderation and locked profile state."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models import CourierProfile, User
from app.models.enums import UserRole, UserStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.user_repository import UserRepository
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.user_service import UserService


class ResubmissionUsers:
    def __init__(self, status: UserStatus | None) -> None:
        self.stale = User(id=uuid4(), role=UserRole.COURIER, status=UserStatus.REJECTED)
        self.current = (
            User(id=self.stale.id, role=UserRole.COURIER, status=status)
            if status is not None
            else None
        )
        self.profile = CourierProfile(
            user_id=self.stale.id,
            is_verified=False,
            verification_rejection_reason="Previous decision",
        )
        self.flush = AsyncMock()

    async def get_owned_with_courier(self, actor_id):
        assert actor_id == self.stale.id
        return self.stale, self.profile

    async def get_owned_with_courier_for_update(self, actor_id):
        assert actor_id == self.stale.id
        return (self.current, self.profile) if self.current is not None else None


def workflow(users: ResubmissionUsers):
    audit = SimpleNamespace(record=AsyncMock())
    service = UserService(
        users=cast(UserRepository, users),
        audit=cast(AuditRepository, audit),
        eligibility=cast(CourierEligibilityService, None),
    )
    return service, audit


@pytest.mark.parametrize(
    "status",
    [UserStatus.BANNED, UserStatus.DELETED, UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION],
)
async def test_resubmission_rejects_current_status_instead_of_stale_rejected(status):
    users = ResubmissionUsers(status)
    service, audit = workflow(users)
    with pytest.raises(ConflictError):
        await service.resubmit_courier_verification(actor_id=users.stale.id)
    assert users.current.status is status
    assert users.profile.verification_rejection_reason == "Previous decision"
    users.flush.assert_not_awaited()
    audit.record.assert_not_awaited()


async def test_resubmission_changes_only_current_locked_rejected_account():
    users = ResubmissionUsers(UserStatus.REJECTED)
    service, audit = workflow(users)
    user, profile = await service.resubmit_courier_verification(actor_id=users.stale.id)
    assert user is users.current
    assert user.status is UserStatus.PENDING_VERIFICATION
    assert users.stale.status is UserStatus.REJECTED
    assert profile.verification_rejection_reason is None
    assert profile.is_verified is False
    users.flush.assert_awaited_once()
    audit.record.assert_awaited_once()


async def test_resubmission_of_missing_current_account_is_not_found():
    users = ResubmissionUsers(None)
    service, audit = workflow(users)
    with pytest.raises(NotFoundError):
        await service.resubmit_courier_verification(actor_id=users.stale.id)
    audit.record.assert_not_awaited()


@pytest.mark.parametrize("missing_profile", [False, True])
async def test_resubmission_requires_current_courier_and_profile(missing_profile):
    users = ResubmissionUsers(UserStatus.REJECTED)
    if missing_profile:
        users.profile = None
    else:
        users.current.role = UserRole.CUSTOMER
    service, audit = workflow(users)
    with pytest.raises(ConflictError):
        await service.resubmit_courier_verification(actor_id=users.stale.id)
    audit.record.assert_not_awaited()


@pytest.mark.parametrize("status", [UserStatus.BANNED, UserStatus.DELETED])
async def test_profile_update_rejects_newly_restricted_account(status):
    users = ResubmissionUsers(status)
    service, audit = workflow(users)
    with pytest.raises(ForbiddenError):
        await service.update_me(
            users.stale.id,
            full_name="Synthetic name",
            email=None,
            dob=None,
            courier_city=None,
            courier_bio="Synthetic bio",
            supplied={"full_name", "courier_bio"},
        )
    users.flush.assert_not_awaited()
    audit.record.assert_not_awaited()


async def test_profile_update_mutates_locked_pair_instead_of_stale_snapshot():
    users = ResubmissionUsers(UserStatus.REJECTED)
    service, _ = workflow(users)
    user, profile = await service.update_me(
        users.stale.id,
        full_name="Synthetic name",
        email=None,
        dob=None,
        courier_city=None,
        courier_bio="Synthetic bio",
        supplied={"full_name", "courier_bio"},
    )
    assert user is users.current
    assert users.stale.full_name is None
    assert user.full_name == "Synthetic name"
    assert profile.bio == "Synthetic bio"
