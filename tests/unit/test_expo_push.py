from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest


async def test_expo_commits_each_chunk_and_preserves_hint() -> None:
    from app.integrations.push.expo import DeviceSnapshot, ExpoPushClient

    tokens = [f"ExpoPushToken[{index}]" for index in range(101)]
    snapshots = {token: DeviceSnapshot(uuid4(), token, datetime.now(UTC)) for token in tokens}
    store = AsyncMock()
    store.snapshot_devices.side_effect = lambda chunk, **kwargs: {t: snapshots[t] for t in chunk}
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        messages = json.loads(request.content)
        requests.append(messages)
        if len(requests) == 2:
            return httpx.Response(400)
        return httpx.Response(
            200, json={"data": [{"status": "ok", "id": str(i)} for i in range(100)]}
        )

    client = ExpoPushClient(store, access_token="dummy")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(RuntimeError):
        await client.send_push(
            tokens,
            "Order available",
            "Open Giftly",
            data={"type": "ORDER_AVAILABLE", "order_id": str(uuid4())},
        )
    assert [len(chunk) for chunk in requests] == [100, 1]
    assert requests[0][0]["data"]["type"] == "ORDER_AVAILABLE"
    assert len(store.save_tickets.call_args.args[0]) == 100
    await client.aclose()


async def test_expo_invalid_ticket_uses_presend_snapshot() -> None:
    from app.integrations.push.expo import DeviceSnapshot, ExpoPushClient

    snapshot = DeviceSnapshot(uuid4(), "ExpoPushToken[invalid]", datetime.now(UTC))
    store = AsyncMock()
    store.snapshot_devices.return_value = {snapshot.token: snapshot}
    client = ExpoPushClient(store)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]},
            )
        )
    )
    await client.send_push([snapshot.token], "Update", "Open Giftly")
    store.remove_invalid_batch.assert_awaited_once_with([snapshot])
    await client.aclose()


@pytest.mark.parametrize("status", [429, 503])
async def test_expo_temporary_failures_retry_with_finite_backoff(status: int) -> None:
    from unittest.mock import patch

    from app.integrations.push.expo import DeviceSnapshot, ExpoPushClient

    snapshot = DeviceSnapshot(uuid4(), "ExpoPushToken[temporary]", datetime.now(UTC))
    store = AsyncMock()
    store.snapshot_devices.return_value = {snapshot.token: snapshot}
    client = ExpoPushClient(store)
    await client._client.aclose()
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with patch("app.integrations.push.expo.asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(RuntimeError, match="unavailable"):
            await client.send_push([snapshot.token], "Update", "Open Giftly")
    assert len(requests) == 3
    assert [c.args[0] for c in sleep.await_args_list] == [0.25, 0.5]
    store.save_tickets.assert_not_awaited()
    await client.aclose()


async def test_receipts_validate_batch_and_malformed_response() -> None:
    from app.integrations.push.expo import ExpoPushClient

    client = ExpoPushClient(AsyncMock())
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": {"ticket": {"status": "invalid"}}})
        )
    )
    with pytest.raises(ValueError):
        await client.get_receipts(["id"] * 1001)
    with pytest.raises(RuntimeError, match="Invalid Expo receipts"):
        await client.get_receipts(["ticket"])
    await client.aclose()


async def test_expo_rejects_oversized_provider_response():
    from app.integrations.push.expo import ExpoPushClient

    store = AsyncMock()
    client = ExpoPushClient(store)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        )
    )
    with pytest.raises(RuntimeError, match="exceeds"):
        await client.get_receipts(["ticket"])
    await client.aclose()


async def test_expo_compressed_response_is_decoded_exactly_once():
    import gzip
    import json

    from app.integrations.push.expo import ExpoPushClient

    client = ExpoPushClient(AsyncMock())
    await client._client.aclose()
    content = gzip.compress(json.dumps({"data": {"ticket": {"status": "ok"}}}).encode())
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=content, headers={"Content-Encoding": "gzip"}
            )
        )
    )
    result = await client.get_receipts(["ticket"])
    assert result["ticket"].status == "ok"
    await client.aclose()
