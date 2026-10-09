"""Hosted checkout checkpoints preserve retry identity after ambiguous provider failure."""

from dataclasses import replace
from uuid import uuid4

from app.core.exceptions import PaymentProviderUnavailableError
from app.main import create_app
from app.models import PaymentIntent, WriteOperation
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.integration.test_hosted_payment_sessions import gateway
from tests.integration.test_orders_api import _phone, _register, _settings


async def test_hosted_timeout_commits_owned_recovery_and_suppresses_repeat():
    app = create_app(_settings())
    client_gateway = gateway()
    client_gateway.create_checkout.side_effect = PaymentProviderUnavailableError()
    async with app.router.lifespan_context(app):
        app.state.clients = replace(app.state.clients, gateway=client_gateway)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            user = await _register(client, app, _phone(), "CUSTOMER")
            key = str(uuid4())
            headers = {"Authorization": f"Bearer {user['access_token']}", "Idempotency-Key": key}
            response = await client.post(
                "/api/wallets/topup", json={"amount": "100.00"}, headers=headers
            )
            assert response.status_code == 503, response.text
            recovery = await client.get(
                f"/api/operations/{key}?operation=wallet.topup", headers=headers
            )
            assert recovery.status_code == 200, recovery.text
            result = recovery.json()
            assert result["status"] == "OUTCOME_UNKNOWN" and result["result"] is None
            assert result["resource_id"] is not None
            replay = await client.post(
                "/api/wallets/topup", json={"amount": "100.00"}, headers=headers
            )
            assert (
                replay.status_code == 503 and replay.json()["error"]["code"] == "OPERATION_PENDING"
            )
            assert client_gateway.create_checkout.await_count == 1
            async with app.state.session_factory() as session:
                row = await session.scalar(
                    select(WriteOperation).where(WriteOperation.operation_key == key)
                )
                intent = await session.get(PaymentIntent, row.resource_id)
                assert (
                    str(intent.id) == result["resource_id"] and intent.checkout_state == "CREATING"
                )
