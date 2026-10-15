"""Single-connection PostgreSQL pool remains free during an authorized PDF render."""

from types import SimpleNamespace

from app.main import create_app
from httpx import ASGITransport, AsyncClient

from tests.integration.test_invoices_api import _GOLDEN_ITEMS, _assigned_order, _make_stack
from tests.integration.test_orders_api import _phone, _register


async def test_invoice_pdf_releases_pool_and_preserves_fresh_access_checks():
    settings, engine, factory = await _make_stack()
    settings = settings.model_copy(update={"DB_POOL_SIZE": 1, "DB_MAX_OVERFLOW": 0})
    app = create_app(settings)
    statuses = []

    async def render(snapshot, lines):
        assert app.state.engine.pool.checkedout() == 0, "PDF still holds the DB connection"
        assert isinstance(lines, tuple)
        statuses.append(str(snapshot.status))
        return b"%PDF-1.4 private"

    app.state.invoice_pdf_cache = SimpleNamespace(render=render)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            customer, courier, order_id = await _assigned_order(client, app, factory)
            created = await client.post(
                f"/api/orders/{order_id}/invoices",
                headers=courier,
                json={"items": _GOLDEN_ITEMS, "courier_fee_amount": "100.00"},
            )
            assert created.status_code == 201, created.text
            invoice_id = created.json()["id"]
            path = f"/api/invoices/{invoice_id}/pdf"
            for actor in (customer, courier):
                response = await client.get(path, headers=actor)
                assert response.status_code == 200, response.text
                assert response.content.startswith(b"%PDF-")
                assert response.headers["cache-control"] == "private, no-store"
            stranger = await _register(client, app, _phone(), "CUSTOMER")
            denied = await client.get(
                path, headers={"Authorization": f"Bearer {stranger['access_token']}"}
            )
            assert denied.status_code == 404
            assert statuses == ["ISSUED", "ISSUED"]
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()
        await engine.dispose()
