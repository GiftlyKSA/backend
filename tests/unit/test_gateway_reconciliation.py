from unittest.mock import Mock

from app.core.config import Environment

from tests.conftest import make_test_settings


async def test_default_and_production_reconciliation_never_contact_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: make_test_settings())
    from app.workers.gateway_reconciliation import reconcile_gateway_payments

    build_client = Mock()
    monkeypatch.setattr("app.workers.gateway_reconciliation.build_payment_client", build_client)
    assert await reconcile_gateway_payments(settings=make_test_settings()) == 0
    settings = make_test_settings().model_copy(
        update={"ENVIRONMENT": Environment.PRODUCTION, "PAYMENT_PROVIDER": "dhamen"}
    )
    assert await reconcile_gateway_payments(settings=settings) == 0
    build_client.assert_not_called()
