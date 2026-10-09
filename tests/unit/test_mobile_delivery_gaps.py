"""Bound complete live frames and retain safe push navigation metadata."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import Environment
from app.integrations.push.fake import FakePushClient
from app.routers.chat import _send_socket_frame
from starlette.websockets import WebSocketDisconnect

from tests.conftest import make_test_settings


async def test_push_preserves_order_uuid_without_personal_content():
    push = FakePushClient(Environment.TEST)
    data = {"type": "ORDER_AVAILABLE", "order_id": "00000000-0000-0000-0000-000000000001"}
    await push.send_push(["testing-device"], "New order", "Open the app", data=data)
    data["order_id"] = "changed"
    assert push.sent[0].data["order_id"] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("payload,accepted", [("a" * 32, True), ("é" * 17, False)])
async def test_outgoing_frame_counts_utf8_bytes(payload, accepted):
    socket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=make_test_settings(WS_MAX_OUTGOING_FRAME_BYTES=32))
        ),
        send_text=AsyncMock(),
        close=AsyncMock(),
    )
    if accepted:
        await _send_socket_frame(socket, payload)
        socket.send_text.assert_awaited_once_with(payload)
    else:
        with pytest.raises(WebSocketDisconnect):
            await _send_socket_frame(socket, payload)
        socket.close.assert_awaited_once_with(code=1009)
        socket.send_text.assert_not_awaited()
