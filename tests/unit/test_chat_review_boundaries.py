import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError
from app.routers.chat import _extract_text, _parse_cursor


@pytest.mark.parametrize("text", [None, {}, 123, "x" * 4001])
def test_socket_rejects_values_rejected_by_rest(text):
    assert _extract_text(json.dumps({"text": text})) == ""


def test_socket_preserves_literal_code_and_whitespace_like_rest():
    text = "  <script>alert('literal')</script>  "
    assert _extract_text(json.dumps({"text": text})) == text


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage",
        "bad|bad",
        "2026-10-08T12:00:00|12345678-1234-4234-8234-123456789012",
        "2026-10-08Z|bad",
    ],
)
def test_nonempty_malformed_inbox_cursor_is_rejected(cursor):
    with pytest.raises(BadRequestError):
        _parse_cursor(cursor)


def test_inbox_cursor_preserves_empty_and_aware_keyset_contract():
    assert _parse_cursor(None) is None
    assert _parse_cursor("") is None
    timestamp = datetime(2026, 10, 8, 12, tzinfo=UTC)
    identity = uuid4()
    assert _parse_cursor(f"{timestamp.isoformat()}|{identity}") == (timestamp, identity)


@pytest.mark.parametrize("transport", ["header", "protocol", "legacy"])
def test_chat_token_transport_supports_safe_headers_and_legacy_clients(transport):
    from app.routers.chat import _socket_token

    socket = SimpleNamespace(headers={}, scope={"subprotocols": []}, query_params={})
    if transport == "header":
        socket.headers["authorization"] = "Bearer fake-not-a-real-token"
    elif transport == "protocol":
        socket.scope["subprotocols"] = ["giftly.chat", "bearer.fake-not-a-real-token"]
    else:
        socket.query_params["token"] = "fake-not-a-real-token"
    assert _socket_token(socket) == "fake-not-a-real-token"
