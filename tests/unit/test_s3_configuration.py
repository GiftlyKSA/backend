"""Explicit private storage selection and public HTTPS origin validation."""

from unittest.mock import patch

import pytest
from app.integrations.factory import build_clients

from tests.conftest import make_test_settings


def storage_values():
    return dict(
        STORAGE_PROVIDER="s3",
        S3_ENDPOINT="https://objects.example.com",
        S3_BUCKET="private-media",
        S3_ACCESS_KEY_ID="test-access",
        S3_SECRET_ACCESS_KEY="test-secret",
    )


async def test_explicit_s3_in_development_keeps_other_fakes():
    clients = build_clients(make_test_settings(ENVIRONMENT="development", **storage_values()))
    assert type(clients.storage).__name__ == "S3StorageClient"
    assert type(clients.sms).__name__ == "FakeSmsClient"
    assert type(clients.push).__name__ == "FakePushClient"
    await clients.storage.aclose()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://objects.example.com",
        "https://localhost",
        "https://localhost.localdomain",
        "https://169.254.169.254",
        "https://192.168.1.1",
        "https://objects.exa\nmple.com",
        "https://objects.example.com?",
        "https://objects.example.com#",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://[::1]",
        "https://storage.internal",
        "https://storage.local",
        "https://storage",
        "https://user@objects.example.com",
        "https://objects.example.com/bucket",
        "https://objects.example.com?x=y",
        "https://objects.example.com#fragment",
        "https://objects.example.com:invalid",
    ],
)
def test_s3_rejects_nonpublic_or_nonorigin_endpoint(endpoint):
    values = storage_values()
    values["S3_ENDPOINT"] = endpoint
    with pytest.raises(ValueError, match="S3_ENDPOINT"):
        make_test_settings(**values)


@pytest.mark.parametrize(
    "name", ["S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
)
def test_explicit_s3_never_falls_back_when_missing_configuration(name):
    values = storage_values()
    values[name] = None
    with pytest.raises(ValueError, match=name):
        make_test_settings(**values)


async def test_explicit_expo_in_development_owns_only_one_database_connection():
    from app.core.db import build_engine
    from app.integrations.factory import build_push_client
    from app.integrations.push.expo import ExpoPushClient

    settings = make_test_settings(PUSH_PROVIDER="expo", DB_POOL_SIZE=5, DB_MAX_OVERFLOW=10)
    with patch("app.core.db.build_engine", wraps=build_engine) as constructor:
        client = build_push_client(settings)
    receipt_settings = constructor.call_args.args[0]
    assert receipt_settings.DB_POOL_SIZE == 1
    assert receipt_settings.DB_MAX_OVERFLOW == 0
    assert type(receipt_settings) is type(settings)
    assert settings.DB_POOL_SIZE == 5
    assert settings.DB_MAX_OVERFLOW == 10
    assert isinstance(client, ExpoPushClient)
    await client.aclose()


def test_auto_storage_and_push_remain_fake_in_test():
    settings = make_test_settings()
    assert settings.storage_provider == "fake"
    assert settings.push_provider == "fake"
