from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from app.routers import webhooks
from app.schemas.payments import DhamenWebhookAck
from fastapi import FastAPI

from tests.conftest import make_test_settings


@pytest.mark.parametrize("failure", [None, "settlement", "commit"])
async def test_webhook_acknowledges_only_committed_settlement(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    app = FastAPI()
    app.include_router(webhooks.dhamen_router)
    app.state.settings = make_test_settings()
    app.state.redis = AsyncMock()
    app.state.clients = Mock()
    session = AsyncMock()
    manager = AsyncMock()
    manager.__aenter__.return_value = session
    app.state.session_factory = Mock(return_value=manager)
    service = AsyncMock()
    events: list[str] = []

    async def handle(*, raw_body: bytes) -> DhamenWebhookAck:
        events.append("settle")
        if failure == "settlement":
            raise RuntimeError("test settlement failure")
        return DhamenWebhookAck(response_id="test-response")

    async def commit() -> None:
        events.append("commit")
        if failure == "commit":
            raise RuntimeError("test commit failure")

    service.handle_dhamen_notifications.side_effect = handle
    session.commit.side_effect = commit
    monkeypatch.setattr(webhooks, "build_payment_service", Mock(return_value=service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://giftly.test",
    ) as client:
        response = await client.post("/api/webhooks/dhamen", json={"test": "batch"})
    if failure:
        assert response.status_code == 500
        session.rollback.assert_awaited_once()
        assert events == (["settle"] if failure == "settlement" else ["settle", "commit"])
    else:
        assert response.status_code == 200
        assert response.json() == {"responseId": "test-response", "status": "SUCCESS"}
        assert events == ["settle", "commit"]
        session.rollback.assert_not_called()
