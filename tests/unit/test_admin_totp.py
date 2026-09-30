"""Production dashboard TOTP login regressions."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import Environment
from app.core.exceptions import UnauthorizedError
from app.models.enums import UserRole, UserStatus
from app.services.admin_auth_service import AdminAuthService
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from pydantic import SecretStr

from tests.conftest import make_test_settings

_KEY = b"0123456789abcdefghij"
_SECRET = base64.b32encode(_KEY).decode("ascii")


def _service() -> tuple[AdminAuthService, AsyncMock, AsyncMock]:
    settings = make_test_settings(
        ADMIN_DASHBOARD_ENABLED=True,
        ADMIN_USERNAME="dashboard-user",
        ADMIN_PASSWORD="strong-password-example",
    ).model_copy(
        update={"ENVIRONMENT": Environment.PRODUCTION, "ADMIN_TOTP_SECRET": SecretStr(_SECRET)}
    )
    user = SimpleNamespace(
        id="admin-id", role=UserRole.ADMIN, status=UserStatus.ACTIVE, deleted_at=None
    )
    users = SimpleNamespace(ensure_dashboard_admin=AsyncMock(return_value=user))
    sessions = SimpleNamespace(create=AsyncMock())
    redis = AsyncMock()
    redis.eval.return_value = 1
    redis.set.return_value = True
    return (
        AdminAuthService(users=users, sessions=sessions, redis=redis, settings=settings),
        sessions.create,
        redis.set,
    )


async def test_production_login_requires_totp_before_session() -> None:
    service, create, _ = _service()
    with pytest.raises(UnauthorizedError):
        await service.complete_login(
            username="dashboard-user",
            password="strong-password-example",
            totp_code="",
            ip="127.0.0.1",
            user_agent=None,
        )
    create.assert_not_awaited()
    service._users.ensure_dashboard_admin.assert_not_awaited()


async def test_production_login_accepts_valid_totp_once(monkeypatch: pytest.MonkeyPatch) -> None:
    service, create, replay_set = _service()
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(service, "_now", lambda: now)
    code = TOTP(_KEY, 6, hashes.SHA1(), 30).generate(int(now.timestamp())).decode("ascii")
    await service.complete_login(
        username="dashboard-user",
        password="strong-password-example",
        totp_code=code,
        ip="127.0.0.1",
        user_agent=None,
    )
    create.assert_awaited_once()
    assert replay_set.await_args.kwargs == {"nx": True, "ex": 90}


async def test_production_login_rejects_replayed_totp() -> None:
    service, create, replay_set = _service()
    replay_set.return_value = False
    code = TOTP(_KEY, 6, hashes.SHA1(), 30).generate(int(datetime.now(UTC).timestamp()))
    with pytest.raises(UnauthorizedError):
        await service.complete_login(
            username="dashboard-user",
            password="strong-password-example",
            totp_code=code.decode("ascii"),
            ip="127.0.0.1",
            user_agent=None,
        )
    create.assert_not_awaited()


@pytest.mark.parametrize("secret", ["", "not-base32!", "MZXW6===", "A" * 16])
def test_bad_production_totp_secret_refuses_boot(secret: str) -> None:
    settings = make_test_settings(
        ADMIN_DASHBOARD_ENABLED=True,
        ADMIN_USERNAME="dashboard-user",
        ADMIN_PASSWORD="strong-password-example",
    )
    copied = settings.model_copy(
        update={"ENVIRONMENT": Environment.PRODUCTION, "ADMIN_TOTP_SECRET": SecretStr(secret)}
    )
    with pytest.raises(ValueError, match="ADMIN_TOTP_SECRET"):
        copied._validate_admin()
