"""Order streams enforce ownership and current credentials before sending state."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.jwt import create_access_token
from app.models.enums import UserRole, UserStatus
from app.routers import order_events
from app.schemas.order_events import OrderStatusEvent
from app.services import order_realtime_service as realtime

from tests.conftest import make_test_settings


async def test_invalid_token_never_opens_database():
    factory = Mock()
    service = realtime.OrderRealtimeService(make_test_settings(), AsyncMock(), factory)
    with pytest.raises(UnauthorizedError):
        await service.snapshot(uuid4(), "invalid")
    factory.assert_not_called()


async def test_admin_token_cannot_subscribe_to_customer_order():
    settings = make_test_settings()
    token, _, _ = create_access_token(settings, user_id=uuid4(), role="ADMIN")
    factory = Mock()
    service = realtime.OrderRealtimeService(settings, AsyncMock(), factory)
    with pytest.raises(ForbiddenError):
        await service.snapshot(uuid4(), token)
    factory.assert_not_called()


async def test_unrelated_customer_cannot_read_order(monkeypatch):
    settings = make_test_settings()
    actor_id, order_id = uuid4(), uuid4()
    token, _, _ = create_access_token(settings, user_id=actor_id, role="CUSTOMER")
    session = AsyncMock()
    factory = Mock(return_value=session)
    redis = AsyncMock()
    redis.get.return_value = None
    repository = AsyncMock()
    repository.get_live_state.return_value = SimpleNamespace(
        user=SimpleNamespace(
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
            deleted_at=None,
            auth_version=0,
        ),
        order_id=None,
    )
    monkeypatch.setattr(realtime, "OrderRepository", Mock(return_value=repository))
    service = realtime.OrderRealtimeService(settings, redis, factory)
    with pytest.raises(NotFoundError):
        await service.snapshot(order_id, token)
    repository.get_live_state.assert_awaited_once_with(order_id, actor_id)


async def test_revocation_during_updates_sends_no_state():
    websocket = AsyncMock()
    service = AsyncMock()
    service.snapshot.side_effect = UnauthorizedError()
    pubsub = AsyncMock()
    pubsub.get_message.return_value = {"data": "changed"}
    snapshot = OrderStatusEvent(order_id=uuid4(), status="NEW", courier_id=None, assigned_at=None)
    with pytest.raises(UnauthorizedError):
        await order_events._updates(
            websocket, snapshot.order_id, "token", service, AsyncMock(), snapshot, pubsub
        )
    websocket.send_text.assert_not_awaited()


async def test_assignment_hint_sends_updated_snapshot():
    snapshot = OrderStatusEvent(order_id=uuid4(), status="NEW", courier_id=None, assigned_at=None)
    updated = snapshot.model_copy(update={"status": "ASSIGNED", "courier_id": uuid4()})
    service = AsyncMock()
    service.snapshot.side_effect = [updated, UnauthorizedError()]
    websocket, pubsub = AsyncMock(), AsyncMock()
    pubsub.get_message.return_value = {"data": "changed"}
    with pytest.raises(UnauthorizedError):
        await order_events._updates(
            websocket, snapshot.order_id, "token", service, AsyncMock(), snapshot, pubsub
        )
    websocket.send_text.assert_awaited_once_with(
        updated.model_copy(update={"type": "order.updated"}).model_dump_json()
    )


async def test_subscribes_before_initial_snapshot_and_cleans_up():
    sequence = []
    pubsub = AsyncMock()

    async def subscribe(*args):
        sequence.append("subscribe")

    async def idle(**kwargs):
        await asyncio.sleep(0.01)
        return None

    snapshot = OrderStatusEvent(order_id=uuid4(), status="NEW", courier_id=None, assigned_at=None)

    async def load(*args):
        sequence.append("snapshot")
        return snapshot

    pubsub.subscribe.side_effect = subscribe
    pubsub.get_message.side_effect = idle
    websocket = AsyncMock()
    websocket.scope = {"subprotocols": ["giftly.orders", "bearer.secret"]}
    websocket.app = SimpleNamespace(
        state=SimpleNamespace(redis=Mock(pubsub=Mock(return_value=pubsub)))
    )
    websocket.receive.return_value = {"type": "websocket.disconnect", "code": 1000}
    with pytest.raises(order_events.WebSocketDisconnect):
        await order_events._serve(
            websocket, snapshot.order_id, "token", SimpleNamespace(snapshot=load), AsyncMock()
        )
    assert sequence[:2] == ["subscribe", "snapshot"]
    websocket.accept.assert_awaited_once_with(subprotocol="giftly.orders")
    websocket.send_text.assert_awaited_once_with(snapshot.model_dump_json())
    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()


async def test_publish_failure_does_not_turn_committed_write_into_failure(caplog):
    redis = AsyncMock()
    redis.publish.side_effect = ConnectionError("Redis unavailable")
    await realtime.publish_order_change(redis, uuid4())
    assert "Order saved, but its live update could not be sent" in caplog.text


def test_browser_token_uses_subprotocol_and_ignores_query_credentials():
    websocket = SimpleNamespace(
        headers={}, scope={"subprotocols": ["giftly.orders", "bearer.access-token"]}
    )
    assert order_events._token(websocket) == "access-token"
    websocket.scope = {"subprotocols": [], "query_string": b"token=secret"}
    assert order_events._token(websocket) == ""
