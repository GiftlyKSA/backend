from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ForbiddenError
from app.models.enums import UserRole, UserStatus
from app.services.courier_eligibility_service import CourierEligibilityService


@pytest.mark.parametrize(
    "role,status,deleted",
    [
        (UserRole.CUSTOMER, UserStatus.PENDING_VERIFICATION, None),
        (UserRole.CUSTOMER, UserStatus.REJECTED, None),
        (UserRole.CUSTOMER, UserStatus.DELETED, None),
        (UserRole.CUSTOMER, UserStatus.ACTIVE, "deleted"),
        (UserRole.ADMIN, UserStatus.ACTIVE, None),
    ],
)
async def test_new_mobile_reads_reject_ineligible_account_states(role, status, deleted):
    users, couriers = AsyncMock(), AsyncMock()
    user = SimpleNamespace(id=uuid4(), role=role, status=status, deleted_at=deleted)
    users.get.return_value = user
    with pytest.raises(ForbiddenError):
        await CourierEligibilityService(users=users, couriers=couriers).require_marketplace_actor(
            user.id
        )


async def test_new_mobile_reads_require_verified_courier():
    users, couriers = AsyncMock(), AsyncMock()
    user = SimpleNamespace(
        id=uuid4(), role=UserRole.COURIER, status=UserStatus.ACTIVE, deleted_at=None
    )
    users.get.return_value = user
    couriers.get.return_value = SimpleNamespace(is_verified=False)
    with pytest.raises(ForbiddenError):
        await CourierEligibilityService(users=users, couriers=couriers).require_marketplace_actor(
            user.id
        )


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER])
async def test_active_marketplace_accounts_can_read(role):
    users, couriers = AsyncMock(), AsyncMock()
    user = SimpleNamespace(id=uuid4(), role=role, status=UserStatus.ACTIVE, deleted_at=None)
    users.get.return_value = user
    couriers.get.return_value = SimpleNamespace(is_verified=True)
    await CourierEligibilityService(users=users, couriers=couriers).require_marketplace_actor(
        user.id
    )
    assert couriers.get.await_count == (1 if role is UserRole.COURIER else 0)
