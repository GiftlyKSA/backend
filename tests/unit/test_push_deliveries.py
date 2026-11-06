"""Pushes retain independent outcomes, safe hints and bounded shared coordination."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.integrations.push.expo import DeviceSnapshot, ExpoOutcome, ExpoPushClient, ExpoSendError
from app.repositories.push_delivery_repository import DeliveryClaim, PushDeliveryRepository
from app.schemas.push_event import PushEvent
from app.services import push_delivery_service
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql


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
        assert commits == 1 + int(retired_first)
        assert messages[0]["data"] == target.event.wire_data()
        assert "message" not in messages[0]["data"]
        return [ExpoOutcome(status="ok", id="ticket")]

    client = SimpleNamespace(send_messages=AsyncMock(side_effect=send))
    redis = SimpleNamespace(eval=AsyncMock(return_value=int(allowed)))
    assert await push_delivery_service.deliver_pushes(Session, client, redis) == 1
    assert client.send_messages.await_count == int(allowed)
    outcomes = repository.finish.await_args.args[1]
    assert outcomes[0].status == ("ok" if allowed else "error")
    if not allowed:
        assert outcomes[0].details["error"] == "RateDeferred"
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


def test_final_transient_receipt_is_not_polled_again():
    from app.services.expo_receipt_service import ExpoReceiptService

    service = ExpoReceiptService(AsyncMock())
    result = service._resolution(
        SimpleNamespace(), ExpoOutcome(status="error", details={"error": "MessageRateExceeded"})
    )
    assert result[1:] == ("MessageRateExceeded", False, False)
    assert service._resolution(SimpleNamespace(), None)[2] is True


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
    assert all(claim.attempts == 1 for claim in claims)


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
