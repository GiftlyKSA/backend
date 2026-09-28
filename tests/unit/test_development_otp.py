"""Development OTP behavior without database or Redis services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.deps import get_db
from app.main import create_app
from app.routers import auth
from app.services.otp_service import OtpService
from fastapi.testclient import TestClient

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_development_otp_is_five_digits_and_verifies() -> None:
    settings = make_test_settings(ENVIRONMENT="development")
    assert settings.OTP_TTL_SECONDS == 60
    redis = AsyncMock()
    redis.eval.return_value = 1
    sms = AsyncMock()
    service = OtpService(redis, sms, settings)

    code = await service.request_otp("+966501234567")

    assert code is not None and code.isdigit() and len(code) == 5
    assert 10000 <= int(code) <= 99999
    sms.send_otp.assert_awaited_once_with("+966501234567", code)
    assert redis.eval.await_args_list[0].args[7] == 60
    assert await service.verify_otp("+966501234567", code)


@pytest.mark.asyncio
async def test_non_development_otp_stays_six_digits_and_is_not_returned() -> None:
    settings = make_test_settings()
    redis = AsyncMock()
    redis.eval.return_value = 1
    sms = AsyncMock()
    service = OtpService(redis, sms, settings)

    assert await service.request_otp("+966501234567") is None
    sent_code = sms.send_otp.await_args.args[1]
    assert sent_code.isdigit() and len(sent_code) == 6


@pytest.mark.parametrize(
    ("environment", "code", "expected"),
    [
        ("development", "12345", {"expires_in": 60, "otp_dev": 12345}),
        ("test", None, {"expires_in": 60}),
    ],
)
def test_send_otp_http_response_shape(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    code: str | None,
    expected: dict[str, int],
) -> None:
    app = create_app(make_test_settings(ENVIRONMENT=environment))
    app.dependency_overrides[get_db] = lambda: None
    send_otp = AsyncMock(return_value=(60, code))
    monkeypatch.setattr(auth, "_service", lambda request, db: SimpleNamespace(send_otp=send_otp))

    with TestClient(app) as client:
        response = client.post("/api/auth/send-otp", json={"phone": "0501234567"})

    assert response.status_code == 202
    assert response.json() == expected
