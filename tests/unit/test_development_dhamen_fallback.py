"""Development Dhamen configuration safely falls back to fake payments."""

from __future__ import annotations

import pytest
from app.integrations.factory import build_payment_client
from app.integrations.payments.fake import FakePaymentClient

from tests.conftest import make_test_settings


@pytest.mark.parametrize(
    "missing_setting",
    [
        "DHAMEN_APP_ID",
        "DHAMEN_APP_KEY",
        "DHAMEN_CLIENT_ID",
        "DHAMEN_CHECKOUT_HOSTS",
        "DHAMEN_RETURN_URL",
        "DHAMEN_WEBHOOK_URL",
    ],
)
def test_development_with_incomplete_dhamen_configuration_uses_fake_payment_client(
    missing_setting: str,
) -> None:
    config = {
        "DHAMEN_APP_ID": "test-app-id",
        "DHAMEN_APP_KEY": "test-app-key",
        "DHAMEN_CLIENT_ID": "test-client-id",
        "DHAMEN_CHECKOUT_HOSTS": "pay.example.test",
        "DHAMEN_RETURN_URL": "https://giftly.example.test/payment-return",
        "DHAMEN_WEBHOOK_URL": "https://api.giftly.example.test/webhooks/dhamen",
    }
    del config[missing_setting]
    settings = make_test_settings(ENVIRONMENT="development", PAYMENT_PROVIDER="dhamen", **config)

    assert isinstance(build_payment_client(settings), FakePaymentClient)


def test_development_with_complete_dhamen_configuration_uses_dhamen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_test_settings(
        ENVIRONMENT="development",
        PAYMENT_PROVIDER="dhamen",
        DHAMEN_APP_ID="test-app-id",
        DHAMEN_APP_KEY="test-app-key",
        DHAMEN_CLIENT_ID="test-client-id",
        DHAMEN_CHECKOUT_HOSTS="pay.example.test",
        DHAMEN_RETURN_URL="https://giftly.example.test/payment-return",
        DHAMEN_WEBHOOK_URL="https://api.giftly.example.test/webhooks/dhamen",
    )
    expected_client = object()
    monkeypatch.setattr(
        "app.integrations.factory.DhamenPaymentClient",
        lambda **kwargs: expected_client,
    )

    client = build_payment_client(settings)
    assert client is expected_client
