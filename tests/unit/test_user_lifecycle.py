"""User lifecycle and identity policy regression checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import UnauthorizedError
from app.core.jwt import create_access_token, decode_access_token
from app.models import CourierProfile, User
from app.models.enums import UserStatus
from app.repositories.user_repository import UserRepository
from app.schemas.users import UserUpdateRequest
from app.services.auth_service import validate_access_claims

from tests.conftest import make_test_settings


def test_identity_numbers_remain_but_fingerprint_is_not_a_model_field():
    columns = CourierProfile.__table__.columns
    assert "national_id_encrypted" in columns
    assert "passport_id_encrypted" in columns
    assert "identity_fingerprint" not in columns


def test_gender_is_typed_and_not_a_mass_assignment_boundary():
    assert UserUpdateRequest(gender="FEMALE").gender.value == "FEMALE"
    assert "gender" in User.__table__.columns
    with pytest.raises(ValueError):
        UserUpdateRequest(gender="ADMIN")


async def test_deletion_records_explicit_status_and_reason():
    session = AsyncMock()
    user = User(id=uuid4(), status=UserStatus.ACTIVE)
    await UserRepository(session).soft_delete(user, reason="Account closed by its owner.")
    assert user.status.value == "DELETED"
    assert user.deleted_at.tzinfo is not None
    assert user.deletion_reason == "Account closed by its owner."


async def test_deleted_status_denies_access_even_without_timestamp():
    settings = make_test_settings()
    user_id = uuid4()
    token, _, _ = create_access_token(settings, user_id=user_id, role="CUSTOMER")
    users, redis = AsyncMock(), AsyncMock()
    redis.get.return_value = None
    users.get.return_value = SimpleNamespace(
        deleted_at=None,
        status=UserStatus("DELETED"),
        role=SimpleNamespace(value="CUSTOMER"),
        auth_version=0,
    )
    with pytest.raises(UnauthorizedError):
        await validate_access_claims(decode_access_token(settings, token), redis=redis, users=users)
