import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from app.core.exceptions import UnauthorizedError
from app.core.jwt import create_access_token, decode_access_token
from app.core.security import hmac_hex
from app.models.enums import UserRole, UserStatus
from app.repositories.auth_repository import AuthRepository
from app.routers.chat import _authenticate_ws
from app.services.admin_auth_service import AdminAuthService
from app.services.admin_service import AdminService
from app.services.otp_service import OtpService
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


async def test_refresh_read_locks_and_refreshes_existing_state():
    session = AsyncMock()
    await AuthRepository(session).get_refresh_token("hashed-token")
    query = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(query.compile(dialect=postgresql.dialect()))
    assert query.get_execution_options().get("populate_existing") is True


def test_access_token_carries_security_version():
    settings = make_test_settings()
    token, _, _ = create_access_token(settings, user_id=uuid4(), role="CUSTOMER", auth_version=7)
    assert decode_access_token(settings, token).auth_version == 7


@pytest.mark.parametrize("changed", [True, False])
async def test_admin_phone_change_invalidates_only_actual_identity_changes(changed):
    user = SimpleNamespace(id=uuid4(), phone="+966501111111")
    users, auth = AsyncMock(), AsyncMock()
    users.get.return_value = users.get_for_update.return_value = user
    users.get_by_phone.return_value = None
    service = AdminService(
        reads=Mock(),
        users=users,
        couriers=Mock(),
        orders=Mock(),
        promos=Mock(),
        audit=AsyncMock(),
        auth_repo=auth,
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    await service.update_user_profile(
        admin_id=uuid4(),
        user_id=user.id,
        phone="+966502222222" if changed else user.phone,
        full_name="Name",
        email=None,
        ip=None,
    )
    assert auth.invalidate_user_credentials.await_count == int(changed)


async def test_deleted_administrator_cannot_login():
    users, sessions, redis = AsyncMock(), AsyncMock(), AsyncMock()
    users.ensure_dashboard_admin.return_value = SimpleNamespace(
        id=uuid4(), role=UserRole.ADMIN, status=UserStatus.ACTIVE, deleted_at=datetime.now(UTC)
    )
    service = AdminAuthService(
        users=users,
        sessions=sessions,
        redis=redis,
        settings=make_test_settings(ADMIN_USERNAME="admin", ADMIN_PASSWORD="test-password"),
    )
    with pytest.raises(UnauthorizedError):
        await service.complete_login(
            username="admin", password="test-password", ip=None, user_agent=None
        )
    sessions.create.assert_not_awaited()


async def test_same_otp_cannot_authenticate_concurrent_requests():
    settings = make_test_settings()
    digest = hmac_hex("123456", settings.JWT_SECRET.get_secret_value())
    redis = AsyncMock()
    redis.incr.return_value = 1
    redis.ttl.return_value = 60

    async def stale_read(key):
        await asyncio.sleep(0)
        return digest

    redis.get.side_effect = stale_read
    # Redis scripts execute without interleaving; only the first can consume it.
    redis.eval.side_effect = [1, 0]
    service = OtpService(redis, AsyncMock(), settings)
    results = await asyncio.gather(
        service.verify_otp("+966501111111", "123456"),
        service.verify_otp("+966501111111", "123456"),
    )
    assert results.count(True) == 1


async def test_banned_customer_cannot_open_chat_socket():
    settings = make_test_settings()
    user_id = uuid4()
    token, _, _ = create_access_token(settings, user_id=user_id, role="CUSTOMER")
    redis = AsyncMock()
    redis.get.return_value = None
    session = AsyncMock()
    factory = Mock(return_value=session)
    websocket = SimpleNamespace(
        query_params={"token": token},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                redis=redis,
                session_factory=factory,
            )
        ),
    )
    user = SimpleNamespace(
        status=UserStatus.BANNED,
        deleted_at=None,
        role=UserRole.CUSTOMER,
        auth_version=0,
    )
    with patch("app.repositories.user_repository.UserRepository.get", AsyncMock(return_value=user)):
        assert await _authenticate_ws(websocket) is None
