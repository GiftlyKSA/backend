"""Invalid client input must not escape as an internal server error."""

import json
import logging
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from app.core.deps import Actor, get_db
from app.core.exceptions import UnauthorizedError
from app.core.jwt import JwtError, decode_registration_token
from app.core.logging import ScrubbingJsonFormatter
from app.main import create_app
from app.models.enums import UserRole
from app.routers import orders
from app.services.auth_service import AuthService
from fastapi.testclient import TestClient

from tests.conftest import make_test_settings


@pytest.mark.parametrize("query", ["status=INVALID", "cursor=invalid"])
def test_order_list_rejects_invalid_filter_before_service(query: str) -> None:
    app = create_app(make_test_settings())
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[orders._eligible_participant] = lambda: Actor(
        id=uuid4(), role=UserRole.CUSTOMER, jti="test"
    )
    with TestClient(app) as client:
        response = client.get(f"/api/orders?{query}")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_registration_token_is_unauthorized() -> None:
    service = AuthService(
        settings=make_test_settings(),
        redis=AsyncMock(),
        otp=AsyncMock(),
        users=AsyncMock(),
        auth_repo=AsyncMock(),
        session=AsyncMock(),
    )
    with pytest.raises(UnauthorizedError):
        await service.register(
            registration_token="invalid",
            role=UserRole.CUSTOMER,
            full_name=None,
            email=None,
            dob=None,
            city=None,
            national_id=None,
            passport_id=None,
        )


def test_exception_log_has_safe_type_and_location() -> None:
    try:
        raise ValueError("Bearer super-secret-token")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 50, "failed", (), sys.exc_info()
        )
    payload = json.loads(ScrubbingJsonFormatter().format(record))
    assert payload["exception_type"] == "ValueError"
    assert "test_exception_log_has_safe_type_and_location" in payload["exception_location"]
    assert "super-secret-token" not in json.dumps(payload)


def test_signed_registration_token_missing_subject_is_rejected() -> None:
    settings = make_test_settings()
    assert settings.JWT_SECRET is not None
    token = jwt.encode(
        {
            "purpose": "registration",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        settings.JWT_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(JwtError):
        decode_registration_token(settings, token)
