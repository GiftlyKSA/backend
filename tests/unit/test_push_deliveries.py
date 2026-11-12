"""Pushes retain independent outcomes, safe hints and bounded shared coordination."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from app.integrations.push.expo import DeviceSnapshot, ExpoOutcome, ExpoPushClient, ExpoSendError
from app.repositories.expo_receipt_repository import ReceiptClaim
from app.repositories.push_delivery_repository import DeliveryClaim, PushDeliveryRepository
from app.schemas.push_event import PushEvent
from app.services import expo_receipt_service, push_delivery_service
from app.workers import push_deliveries
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


def event() -> PushEvent:
    return PushEvent(event_id=uuid4(), event_type="CHAT_MESSAGE", conversation_id=uuid4())


def claim() -> DeliveryClaim:
    now = datetime.now(UTC)
    return DeliveryClaim(
        uuid4(),
        uuid4(),
        event(),
        DeviceSnapshot(uuid4(), "ExpoPushToken[test]", now),
        1,
        now + timedelta(hours=24),
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"phone": "+966500000000"},
        {"order_id": str(uuid4())},
        {"conversation_id": "invalid"},
        {"conversation_id": None},
        {"type": "ORDER_AVAILABLE"},
    ],
)
def test_payload_rejects_personal_fields_and_mixed_destinations(extra):
    payload = event().wire_data() | extra
    with pytest.raises(ValidationError):
        PushEvent.model_validate(payload)


async def test_transport_partial_success_is_returned_per_device():
    client = ExpoPushClient(AsyncMock())
    await client._client.aclose()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"status": "ok", "id": "accepted"},
                    {"status": "error", "details": {"error": "MessageRateExceeded"}},
                ]
            },
        )

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    outcomes = await client.send_messages([{"to": "one"}, {"to": "two"}])
    assert [outcome.status for outcome in outcomes] == ["ok", "error"]
    assert len(calls) == 1
    await client.aclose()


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "ProviderUnavailable"),
        (503, "ProviderUnavailable"),
        (401, "InvalidCredentials"),
    ],
)
async def test_transport_honors_cooldown_without_hidden_send_retries(status, code):
    client = ExpoPushClient(AsyncMock())
    await client._client.aclose()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status, headers={"Retry-After": "120"}, text="private provider response"
        )

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(ExpoSendError) as caught:
        await client.send_messages([{"to": "token"}])
    assert caught.value.code == code
    assert caught.value.retry_after == 120
    assert "private" not in str(caught.value)
    assert len(requests) == 1
    await client.aclose()


async def test_ambiguous_transport_timeout_is_one_attempt():
    client = ExpoPushClient(AsyncMock())
    await client._client.aclose()
    requests = []

    def respond(request):
        requests.append(request)
        raise httpx.ReadTimeout("private", request=request)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(ExpoSendError, match="Expo send failed"):
        await client.send_messages([{"to": "token"}])
    assert len(requests) == 1
    await client.aclose()


async def test_partial_ticket_results_do_not_reset_accepted_delivery(monkeypatch):
    claims = [claim(), claim()]
    session = SimpleNamespace(execute=AsyncMock())
    repository = PushDeliveryRepository(session)
    saved = AsyncMock(return_value={c.id for c in claims})
    monkeypatch.setattr(repository, "_finish_updates", saved)
    await repository.finish(
        claims,
        [
            ExpoOutcome(status="ok", id="accepted"),
            ExpoOutcome(status="error", details={"error": "MessageRateExceeded"}),
        ],
        now=datetime.now(UTC),
    )
    updates = saved.await_args.args[0]
    assert updates[0][1:3] == ("ACCEPTED", None)
    assert updates[1][1:3] == ("PENDING", "MessageRateExceeded")
    statement = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "expo_push_receipts" in str(statement)
    assert "accepted" in statement.params.values()
    assert claims[0].id in statement.params.values()
    assert claims[0].device.token not in statement.params.values()


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("retired_first", [False, True])
async def test_worker_commits_claim_before_transport_and_handles_shared_budget(
    monkeypatch, allowed, retired_first
):
    open_sessions = 0
    commits = 0

    class Session:
        async def __aenter__(self):
            nonlocal open_sessions
            open_sessions += 1
            return self

        async def __aexit__(self, *_args):
            nonlocal open_sessions
            open_sessions -= 1

        async def commit(self):
            nonlocal commits
            commits += 1

    target = claim()
    repository = SimpleNamespace(
        retired_count=0,
        begin_send=AsyncMock(return_value=[replace(target, attempts=target.attempts + 1)]),
        defer=AsyncMock(),
        finish=AsyncMock(),
    )
    pages = iter(([([], 100)] if retired_first else []) + [([target], 0), ([], 0)])

    async def claim_page(**kwargs):
        result, repository.retired_count = next(pages)
        return result

    repository.claim = AsyncMock(side_effect=claim_page)
    monkeypatch.setattr(push_delivery_service, "PushDeliveryRepository", lambda session: repository)

    async def send(messages):
        assert open_sessions == 0
        assert commits == 2 + int(retired_first)
        repository.begin_send.assert_awaited_once()
        assert messages[0]["data"] == target.event.wire_data()
        assert "message" not in messages[0]["data"]
        return [ExpoOutcome(status="ok", id="ticket")]

    client = SimpleNamespace(send_messages=AsyncMock(side_effect=send))
    redis = SimpleNamespace(eval=AsyncMock(return_value=int(allowed)))
    assert await push_delivery_service.deliver_pushes(Session, client, redis) == 1
    assert client.send_messages.await_count == int(allowed)
    if allowed:
        repository.defer.assert_not_awaited()
        assert repository.finish.await_args.args[1][0].status == "ok"
    else:
        repository.begin_send.assert_not_awaited()
        repository.finish.assert_not_awaited()
        assert repository.defer.await_args.args == ([target], "RateDeferred")
    assert open_sessions == 0


async def test_claim_is_batched_and_fences_current_ownership_and_domain_permissions():
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        flush=AsyncMock(),
    )
    assert await PushDeliveryRepository(session).claim(now=datetime.now(UTC)) == []
    assert session.execute.await_count == 1
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "FOR UPDATE OF push_deliveries SKIP LOCKED" in sql
    assert "device_tokens.user_id = push_deliveries.user_id" in sql
    assert "orders.delivery_city_id" in sql
    assert "messages.id = push_deliveries.event_id" in sql
    assert "occasions.user_id = users.id" in sql
    assert "users.deleted_at IS NULL" in sql
    assert 100 in compiled.params.values()


async def test_claim_without_transport_preserves_attempt_count():
    now = datetime.now(UTC)
    target = claim()
    row = SimpleNamespace(
        id=target.id,
        payload=target.event.wire_data(),
        device_id=target.device.device_id,
        expires_at=target.expires_at,
        attempts=7,
        created_at=now,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(row, target.device.token, target.device.last_seen_at, True)]
            )
        ),
        flush=AsyncMock(),
    )

    [leased] = await PushDeliveryRepository(session).claim(now=now)

    assert row.state == "LEASED"
    assert row.attempts == leased.attempts == 7


def test_final_transient_receipt_is_not_polled_again():
    service = expo_receipt_service.ExpoReceiptService(AsyncMock())
    result = service._resolution(
        SimpleNamespace(), ExpoOutcome(status="error", details={"error": "MessageRateExceeded"})
    )
    assert result[1:] == ("MessageRateExceeded", False, False)
    assert service._resolution(SimpleNamespace(), None)[2] is True


@pytest.mark.parametrize("provider_unavailable", [False, True])
async def test_receipt_drain_persists_outcomes_and_retry_cooldown(
    monkeypatch, provider_unavailable
):
    delivery = claim()
    receipt = ReceiptClaim("ticket", delivery.device, uuid4(), 1, delivery.expires_at)
    repository = SimpleNamespace(
        claim_pending=AsyncMock(side_effect=[[receipt], []]),
        finish_batch=AsyncMock(),
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    monkeypatch.setattr(expo_receipt_service, "ExpoReceiptRepository", lambda _: repository)

    async def get_receipts(ticket_ids):
        assert ticket_ids == [receipt.ticket_id]
        session.commit.assert_awaited_once()
        session.__aexit__.assert_awaited_once()
        if provider_unavailable:
            raise ExpoSendError("ProviderUnavailable", retry_after=45)
        return {
            receipt.ticket_id: ExpoOutcome(status="error", details={"error": "DeviceNotRegistered"})
        }

    client = SimpleNamespace(get_receipts=AsyncMock(side_effect=get_receipts))
    service = expo_receipt_service.ExpoReceiptService(lambda: session)
    assert await service.drain(client) == int(not provider_unavailable)
    client.get_receipts.assert_awaited_once()
    repository.finish_batch.assert_awaited_once()
    (resolutions,) = repository.finish_batch.await_args.args
    if provider_unavailable:
        assert resolutions == [(receipt, "ProviderError", True, False)]
        assert repository.finish_batch.await_args.kwargs["retry_after"] == 45
    else:
        assert resolutions == [(receipt, "DeviceNotRegistered", False, True)]
        assert repository.finish_batch.await_args.kwargs["retry_after"] == 0
    assert session.commit.await_count == 3


@pytest.mark.parametrize("provider", ["fake", "expo"])
async def test_scheduled_push_job_only_allocates_resources_for_expo(monkeypatch, provider):
    settings = make_test_settings(PUSH_PROVIDER=provider)
    engine, redis, client = AsyncMock(), AsyncMock(), AsyncMock()
    engine_builder = Mock(return_value=engine)
    redis_builder = Mock(return_value=redis)
    expo_builder = Mock(return_value=client)
    session_factory = Mock()
    drain = AsyncMock(side_effect=RuntimeError("test drain failure"))
    monkeypatch.setattr(push_deliveries, "get_worker_settings", lambda: settings)
    monkeypatch.setattr(push_deliveries, "build_engine", engine_builder)
    monkeypatch.setattr(push_deliveries, "build_redis", redis_builder)
    monkeypatch.setattr(push_deliveries, "build_expo_client", expo_builder)
    monkeypatch.setattr(push_deliveries, "build_session_factory", lambda _: session_factory)
    monkeypatch.setattr(push_deliveries, "deliver_pushes", drain)

    if provider == "fake":
        await push_deliveries.deliver_push_notifications()
        engine_builder.assert_not_called()
        redis_builder.assert_not_called()
        expo_builder.assert_not_called()
        drain.assert_not_awaited()
        return

    with pytest.raises(RuntimeError, match="test drain failure"):
        await push_deliveries.deliver_push_notifications()
    drain.assert_awaited_once_with(session_factory, client, redis, deadline=ANY)
    pool_settings = engine_builder.call_args.args[0]
    assert (pool_settings.DB_POOL_SIZE, pool_settings.DB_MAX_OVERFLOW) == (1, 0)
    client.aclose.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


async def test_growing_claim_pages_do_not_add_per_device_reads():
    now = datetime.now(UTC)
    rows = [
        (
            SimpleNamespace(
                id=uuid4(),
                device_id=uuid4(),
                payload=event().wire_data(),
                attempts=0,
                expires_at=now + timedelta(hours=24),
                created_at=now,
            ),
            f"ExpoPushToken[{index}]",
            now,
            True,
        )
        for index in range(50)
    ]
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
        flush=AsyncMock(),
    )
    claims = await PushDeliveryRepository(session).claim(now=now)
    assert len(claims) == 50
    assert session.execute.await_count == 1
    assert all(claim.attempts == 0 for claim in claims)


async def test_reminder_expiry_uses_original_occurrence_even_when_enqueued_late():
    now = datetime.now(UTC)
    scheduled = now - timedelta(hours=23, minutes=59)
    device = SimpleNamespace(id=uuid4(), user_id=uuid4())
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=scheduled),
        execute=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [device]), None]),
    )
    reminder = PushEvent(
        event_id=uuid4(),
        event_type="OCCASION_REMINDER",
        type="OCCASION_REMINDER",
        kind="OCCASION",
        occasion_id=uuid4(),
        reminder_version=1,
    )
    await PushDeliveryRepository(session).enqueue(["ExpoPushToken[test]"], reminder, now=now)
    statement = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert scheduled + timedelta(days=1) in statement.params.values()
    assert now + timedelta(hours=24) not in statement.params.values()


def test_published_push_contract_matches_string_transport():
    from pathlib import Path

    for filename in ("openapi.json", "mobile-openapi.json"):
        document = json.loads((Path("docs") / filename).read_text(encoding="utf-8"))
        assert document["x-giftly-push-events"] == PushEvent.model_json_schema(mode="serialization")


async def test_invalid_persisted_payload_does_not_poison_later_devices():
    now = datetime.now(UTC)
    row = SimpleNamespace(
        id=uuid4(),
        device_id=uuid4(),
        payload=event().wire_data() | {"unexpected": "text"},
        attempts=0,
        expires_at=now + timedelta(hours=24),
        created_at=now,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    (row, "ExpoPushToken[test]", now, True),
                ]
            )
        ),
        flush=AsyncMock(),
    )
    assert await PushDeliveryRepository(session).claim(now=now) == []
    assert row.state == "FAILED"
    assert row.error_code == "InvalidPayload"


async def test_worker_transport_requires_no_extra_database_pool(monkeypatch):
    from app.integrations.factory import build_expo_client

    from tests.conftest import make_test_settings

    monkeypatch.setattr("app.core.db.build_engine", lambda settings: pytest.fail("Unexpected pool"))
    client = build_expo_client(make_test_settings())
    assert client._store is None
    await client.aclose()
