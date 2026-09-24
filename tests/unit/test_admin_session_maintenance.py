from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.admin import deps
from app.core.exceptions import UnauthorizedError
from app.models.enums import UserRole, UserStatus
from app.services.admin_auth_service import AdminAuthService
from starlette.requests import Request

from tests.conftest import make_test_settings


@pytest.mark.parametrize("extend_expiry", [True, False])
async def test_session_maintenance_can_authenticate_without_mutating_the_target(extend_expiry):
    now = datetime.now(UTC)
    session = SimpleNamespace(admin_user_id="admin", created_at=now)
    sessions = SimpleNamespace(get_active=AsyncMock(return_value=session), touch=AsyncMock())
    users = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                role=UserRole.ADMIN, status=UserStatus.ACTIVE, deleted_at=None
            )
        )
    )
    service = AdminAuthService(
        users=users, sessions=sessions, redis=Mock(), settings=make_test_settings()
    )
    await service.load_session("test-session", extend_expiry=extend_expiry)
    assert sessions.touch.await_count == int(extend_expiry)


async def test_no_touch_cannot_bypass_absolute_session_expiry():
    session = SimpleNamespace(
        admin_user_id="admin", created_at=datetime.now(UTC) - timedelta(hours=13)
    )
    sessions = SimpleNamespace(get_active=AsyncMock(return_value=session), touch=AsyncMock())
    users = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                role=UserRole.ADMIN, status=UserStatus.ACTIVE, deleted_at=None
            )
        )
    )
    service = AdminAuthService(
        users=users, sessions=sessions, redis=Mock(), settings=make_test_settings()
    )
    with pytest.raises(UnauthorizedError):
        await service.load_session("test-session", extend_expiry=False)
    sessions.touch.assert_not_awaited()


@pytest.mark.parametrize(
    "method,table,slides",
    [
        ("GET", "users", True),
        ("GET", None, True),
        ("GET", "admin_sessions", False),
        ("POST", "users", False),
        ("POST", "admin_sessions", False),
        ("POST", None, False),
        ("PATCH", "users", False),
        ("DELETE", "users", False),
    ],
)
async def test_admin_request_sliding_never_locks_a_session_before_a_mutation(
    monkeypatch, method, table, slides
):
    settings = make_test_settings()
    row = SimpleNamespace(session_token_hash="test-session-hash")
    auth = SimpleNamespace(
        load_session=AsyncMock(return_value=(row, SimpleNamespace())),
        csrf_token_for=Mock(),
    )
    monkeypatch.setattr(deps, "build_auth_service", Mock(return_value=auth))
    request = Request(
        {
            "type": "http",
            "method": method,
            "headers": [(b"cookie", b"admin_session=test-session")],
            "path_params": {"table_name": table} if table else {},
            "app": SimpleNamespace(state=SimpleNamespace(settings=settings, redis=Mock())),
        }
    )
    await deps.require_admin(request, AsyncMock())
    auth.load_session.assert_awaited_once_with("test-session", extend_expiry=slides)
