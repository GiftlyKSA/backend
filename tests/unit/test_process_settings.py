"""Each deployment validates its own required settings without weakening the API."""

import base64

import pytest
from app.core.config import MigrationSettings, SchedulerSettings, Settings, WorkerSettings
from app.main import create_app
from pydantic import ValidationError


def worker_values():
    return {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/db",
        "REDIS_URL": "redis://localhost/0",
        "JWT_SECRET": None,
        "OTP_HMAC_KEY": None,
        "FIELD_ENCRYPTION_KEYS": '{"1":"' + base64.b64encode(b"a" * 32).decode() + '"}',
        "FIELD_ENCRYPTION_KEY_VERSION": 1,
        "S3_REGION": "test",
        "S3_ENDPOINT": "https://objects.example.com",
        "S3_ACCESS_KEY_ID": "test",
        "S3_SECRET_ACCESS_KEY": "test",
        "S3_BUCKET": "test",
        "SNDR_API_KEY": "test",
        "SNDR_BASE_URL": "https://api.sndr.sh",
        "SNDR_FROM_EMAIL": "hello@example.com",
        "SNDR_FROM_NAME": "Giftly",
        "SUPABASE_URL": "https://push.example.com",
        "SUPABASE_SERVICE_KEY": "test",
        "CORS_ALLOWED_ORIGINS": "",
        "SMS_PROVIDER_KEY": None,
        "PAYMENT_PROVIDER": "disabled",
    }


def test_production_worker_does_not_need_api_auth_sms_or_cors():
    settings = WorkerSettings(_env_file=None, **worker_values())
    assert settings.JWT_SECRET is None
    assert settings.is_production


@pytest.mark.parametrize("key", ["SNDR_API_KEY", "S3_SECRET_ACCESS_KEY"])
def test_worker_requires_credentials_for_enabled_jobs(key):
    values = worker_values()
    values[key] = None
    with pytest.raises(ValidationError, match=key):
        WorkerSettings(_env_file=None, **values)


def test_production_scheduler_requires_only_shared_queue_configuration():
    settings = SchedulerSettings(
        _env_file=None, ENVIRONMENT="production", REDIS_URL="redis://localhost/0"
    )
    assert settings.is_production
    assert "JWT_SECRET" not in type(settings).model_fields


def test_migration_configuration_does_not_require_providers():
    settings = MigrationSettings(
        _env_file=None, ENVIRONMENT="production", DATABASE_URL="postgresql+asyncpg://localhost/db"
    )
    assert settings.is_production


def test_api_cannot_accept_worker_settings_to_skip_security_checks():
    with pytest.raises(ValueError, match="API"):
        create_app(WorkerSettings(_env_file=None, **worker_values()))


def test_api_validation_still_requires_authentication_settings():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, **worker_values())


def test_production_worker_cannot_select_simulated_payments():
    values = worker_values()
    values["PAYMENT_PROVIDER"] = "simulated"
    with pytest.raises(ValidationError, match="simulated"):
        WorkerSettings(_env_file=None, **values)


def test_scheduler_imports_task_registry_without_worker_secrets(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    environment = {
        "ENVIRONMENT": "production",
        "REDIS_URL": "redis://localhost/0",
        "GIFTLY_TASKIQ_COMPONENT": "scheduler",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.workers.scheduler import scheduler; print('registry-ready')",
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "registry-ready" in result.stdout


def test_scheduler_component_cannot_weaken_api_getter(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GIFTLY_TASKIQ_COMPONENT", "scheduler")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("value", ["", "   "])
def test_production_worker_rejects_empty_secret_credentials(value):
    values = worker_values()
    values["SNDR_API_KEY"] = value
    with pytest.raises(ValidationError, match="SNDR_API_KEY"):
        WorkerSettings(_env_file=None, **values)


def test_expo_worker_needs_no_supabase_settings():
    values = worker_values()
    values["SUPABASE_URL"] = None
    values["SUPABASE_SERVICE_KEY"] = None
    assert WorkerSettings(_env_file=None, **values).push_provider == "expo"


@pytest.mark.parametrize("name", ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])
def test_explicit_legacy_push_worker_requires_its_credentials(name):
    values = worker_values()
    values["PUSH_PROVIDER"] = "supabase"
    values[name] = None
    with pytest.raises(ValidationError, match=name):
        WorkerSettings(_env_file=None, **values)


@pytest.mark.parametrize("provider_field", ["STORAGE_PROVIDER", "PUSH_PROVIDER"])
def test_production_worker_cannot_select_fakes(provider_field):
    values = worker_values()
    values[provider_field] = "fake"
    with pytest.raises(ValidationError, match="Production must not select fake"):
        WorkerSettings(_env_file=None, **values)
