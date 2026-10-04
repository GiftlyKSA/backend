"""Tests for settings boot validation and the production interlock."""

from __future__ import annotations

import base64
import logging

import pytest
from app.core.config import Settings, get_settings

from tests.conftest import make_test_settings

_ZERO_KEY_B64 = base64.b64encode(b"\x00" * 32).decode()


def test_rsa_auth_without_hmac_secret_refuses_boot() -> None:
    with pytest.raises(ValueError, match="OTP_HMAC_KEY is required"):
        make_test_settings(
            JWT_ALGORITHM="RS256",
            JWT_SECRET=None,
            JWT_PRIVATE_KEY="test-only",
            JWT_PUBLIC_KEY="test-only",
            OTP_HMAC_KEY=None,
        )


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "ENVIRONMENT": "test",
        "DEBUG": "false",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET": "x" * 40,
        "JWT_ALGORITHM": "HS256",
        "FIELD_ENCRYPTION_KEYS": f'{{"1":"{_ZERO_KEY_B64}"}}',
        "FIELD_ENCRYPTION_KEY_VERSION": "1",
        "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
    }
    env.update(overrides)
    return env


def test_valid_test_settings_boot() -> None:
    settings = Settings(_env_file=None, **_base_env())  # type: ignore[call-arg]
    assert settings.ENVIRONMENT.value == "test"
    assert not settings.docs_enabled


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DB_POOL_SIZE", "0"),
        ("DB_POOL_SIZE", "101"),
        ("DB_MAX_OVERFLOW", "-1"),
        ("DB_MAX_OVERFLOW", "101"),
        ("DB_POOL_TIMEOUT_SECONDS", "0"),
        ("DB_POOL_TIMEOUT_SECONDS", "61"),
        ("INTEGRATION_HTTP_TIMEOUT_SECONDS", "0"),
        ("INTEGRATION_HTTP_TIMEOUT_SECONDS", "16"),
        ("INTEGRATION_HTTP_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_invalid_operational_settings_refuse_boot(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings(_env_file=None, **_base_env(**{name: value}))  # type: ignore[call-arg]


def test_startup_logs_real_validation_reason_without_environment_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name, value in _base_env(FIELD_ENCRYPTION_KEYS="not-json").items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.ERROR, logger="app.core.config"):
            with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEYS is not valid JSON"):
                get_settings()
        assert "FIELD_ENCRYPTION_KEYS is not valid JSON" in caplog.text
        assert "not-json" not in caplog.text
        assert "postgresql+asyncpg" not in caplog.text
    finally:
        get_settings.cache_clear()


def test_missing_environment_refuses_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear the ambient value so the absence is real (CI sets ENVIRONMENT=test).
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    env = _base_env()
    del env["ENVIRONMENT"]
    with pytest.raises(ValueError):
        Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_short_jwt_secret_refuses_boot() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(_env_file=None, **_base_env(JWT_SECRET="short"))  # type: ignore[call-arg]


@pytest.mark.parametrize("key", ["", "short", "é" * 16])
def test_short_dedicated_otp_key_refuses_boot(key: str) -> None:
    with pytest.raises(ValueError, match="OTP_HMAC_KEY"):
        Settings(_env_file=None, **_base_env(OTP_HMAC_KEY=key))  # type: ignore[call-arg]


@pytest.mark.parametrize("name", ["SUPABASE_URL", "SNDR_BASE_URL"])
def test_production_provider_urls_require_https(name: str) -> None:
    production = _base_env(
        ENVIRONMENT="production",
        AWS_REGION="test-region",
        AWS_ACCESS_KEY_ID="test-key",
        AWS_SECRET_ACCESS_KEY="test-secret",
        S3_BUCKET_NAME="test-bucket",
        CLOUDFRONT_DOMAIN="media.example.test",
        CLOUDFRONT_KEY_PAIR_ID="test-id",
        CLOUDFRONT_PRIVATE_KEY="test-key",
        SMS_PROVIDER_KEY="test-key",
        SUPABASE_URL="https://api.example.test",
        SUPABASE_SERVICE_KEY="test-key",
        SNDR_API_KEY="test-key",
        SNDR_BASE_URL="https://mail.example.test",
        SNDR_FROM_EMAIL="test@example.test",
        SNDR_FROM_NAME="Giftly",
        SNDR_INVOICE_PAID_TEMPLATE_KEY="test-template",
    )
    production[name] = "http://insecure.example.test"
    with pytest.raises(ValueError, match=name):
        Settings(_env_file=None, **production)  # type: ignore[call-arg]


def test_bad_encryption_key_length_refuses_boot() -> None:
    short = base64.b64encode(b"\x00" * 16).decode()
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_KEYS"):
        Settings(  # type: ignore[call-arg]
            _env_file=None, **_base_env(FIELD_ENCRYPTION_KEYS=f'{{"1":"{short}"}}')
        )


def test_production_with_debug_refuses_boot() -> None:
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(ENVIRONMENT="production", DEBUG="true"),
        )


def test_production_missing_storage_config_refuses_boot() -> None:
    with pytest.raises(ValueError, match="AWS_REGION"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(ENVIRONMENT="production", DEBUG="false"),
        )


def test_production_wildcard_cors_refuses_boot() -> None:
    with pytest.raises(ValueError, match="CORS"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(ENVIRONMENT="production", CORS_ALLOWED_ORIGINS="*"),
        )


def test_invalid_withdrawal_range_refuses_boot() -> None:
    with pytest.raises(ValueError, match="MAX_WITHDRAWAL_AMOUNT"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(MIN_WITHDRAWAL_AMOUNT="100.00", MAX_WITHDRAWAL_AMOUNT="50.00"),
        )


def test_enabled_admin_dashboard_requires_credentials() -> None:
    with pytest.raises(ValueError, match="ADMIN_USERNAME"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(
                ADMIN_DASHBOARD_ENABLED="true",
                ADMIN_SESSION_SECRET="s" * 40,
            ),
        )


def test_production_rejects_development_admin_credentials() -> None:
    with pytest.raises(ValueError, match="Production admin credentials"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            **_base_env(
                ENVIRONMENT="production",
                ADMIN_DASHBOARD_ENABLED="true",
                ADMIN_USERNAME="admin",
                ADMIN_PASSWORD="admin",
                ADMIN_SESSION_SECRET="s" * 40,
            ),
        )
