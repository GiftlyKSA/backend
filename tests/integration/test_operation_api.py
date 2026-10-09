"""Authenticated operation recovery, replay, validation and cross-account boundaries."""

from datetime import date, timedelta
from uuid import uuid4

from app.main import create_app
from httpx import ASGITransport, AsyncClient

from tests.integration.test_orders_api import _phone, _register, _settings


async def test_owned_order_occasion_and_topup_replay():
    settings = _settings()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            first = await _register(client, app, _phone(), "CUSTOMER")
            second = await _register(client, app, _phone(), "CUSTOMER")
            first_headers = {"Authorization": f"Bearer {first['access_token']}"}
            second_headers = {"Authorization": f"Bearer {second['access_token']}"}
            for path, operation, body in [
                (
                    "/api/orders",
                    "order.create",
                    {
                        "description": "Gift",
                        "delivery_city": "Jeddah",
                        "delivery_date": (date.today() + timedelta(days=3)).isoformat(),
                    },
                ),
                (
                    "/api/occasions",
                    "occasion.create",
                    {
                        "title": "Birthday",
                        "occasion_date": (date.today() + timedelta(days=3)).isoformat(),
                    },
                ),
                ("/api/wallets/topup", "wallet.topup", {"amount": "100.00"}),
            ]:
                key = str(uuid4())
                headers = {**first_headers, "Idempotency-Key": key}
                created = await client.post(path, json=body, headers=headers)
                assert created.status_code == 201, created.text
                replay = await client.post(path, json=body, headers=headers)
                assert replay.status_code == 201 and replay.json() == created.json()
                recovery = f"/api/operations/{key}?operation={operation}"
                result = await client.get(recovery, headers=first_headers)
                assert result.status_code == 200, result.text
                assert result.json()["status"] == "COMPLETED"
                assert result.json()["result"] == created.json()
                assert result.headers["cache-control"] == "private, no-store"
                assert (await client.get(recovery, headers=second_headers)).status_code == 404
                assert (await client.get(recovery)).status_code == 401
                changed = dict(body)
                changed[next(iter(body))] = "101.00" if operation == "wallet.topup" else "Changed"
                conflict = await client.post(path, json=changed, headers=headers)
                assert conflict.status_code == 409, conflict.text
                assert conflict.json()["error"]["code"] == "CONFLICT"
                bad = await client.post(
                    path, json=body, headers={**first_headers, "Idempotency-Key": "invalid"}
                )
                assert bad.status_code == 422
                if operation == "occasion.create":
                    patch_key = str(uuid4())
                    patch_headers = {**first_headers, "Idempotency-Key": patch_key}
                    target = path + "/" + created.json()["id"]
                    updated = await client.patch(
                        target, json={"title": "Updated"}, headers=patch_headers
                    )
                    assert updated.status_code == 200, updated.text
                    assert (
                        await client.patch(target, json={"title": "Updated"}, headers=patch_headers)
                    ).json() == updated.json()
                    assert (
                        await client.patch(
                            target, json={"title": "Updated"}, headers=second_headers
                        )
                    ).status_code == 404
                    assert (await client.delete(target, headers=first_headers)).status_code == 204
                    assert (await client.get(recovery, headers=first_headers)).status_code == 404


async def test_retry_that_observed_missing_operation_then_consumed_media_replays(monkeypatch):
    import asyncio

    from app.services.operation_service import OperationService

    app = create_app(_settings())
    ready, committed = asyncio.Event(), asyncio.Event()
    original_find = OperationService.find
    pause = True

    async def pausing_find(self, owner, operation, key, payload=None):
        nonlocal pause
        row = await original_find(self, owner, operation, key, payload)
        if row is None and operation == "order.create" and pause:
            pause = False
            ready.set()
            await committed.wait()
        return row

    monkeypatch.setattr(OperationService, "find", pausing_find)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            account = await _register(client, app, _phone(), "CUSTOMER")
            headers = {
                "Authorization": f"Bearer {account['access_token']}",
                "Idempotency-Key": str(uuid4()),
            }
            grant = await client.post(
                "/api/media/upload-urls",
                headers=headers,
                json={"purpose": "ORDER_REQUEST", "content_type": "image/jpeg", "byte_size": 1000},
            )
            assert grant.status_code == 201, grant.text
            key = grant.json()["storage_key"]
            confirmed = await client.post(
                "/api/media/confirm", headers=headers, json={"storage_key": key}
            )
            assert confirmed.status_code == 200, confirmed.text
            body = {
                "description": "Gift",
                "delivery_city": "Jeddah",
                "delivery_date": (date.today() + timedelta(days=3)).isoformat(),
                "request_media_keys": [key],
            }
            retry = asyncio.create_task(client.post("/api/orders", headers=headers, json=body))
            try:
                await asyncio.wait_for(ready.wait(), 5)
                first = await client.post("/api/orders", headers=headers, json=body)
                assert first.status_code == 201, first.text
            finally:
                committed.set()
            response = await asyncio.wait_for(retry, 5)
            assert response.status_code == 201, response.text
            assert response.json() == first.json()
