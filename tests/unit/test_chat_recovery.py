from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError
from app.models import Message
from app.services.chat_service import ChatService

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_replay_authorizes_and_returns_original_without_append():
    repository = AsyncMock()
    eligibility = AsyncMock()
    service = ChatService(
        chat=repository, redis=AsyncMock(), settings=make_test_settings(), eligibility=eligibility
    )
    conversation_id, sender_id, key = uuid4(), uuid4(), uuid4()
    from datetime import UTC, datetime

    row = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_type="TEXT",
        content_encrypted=service._encrypt_content(conversation_id, "hello"),
        is_read=False,
        created_at=datetime.now(UTC),
    )
    repository.message_for_client_id.return_value = row
    repository.attachments_for_messages.return_value = []
    result = await service.replay_message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        text="hello",
        client_message_id=key,
        storage_keys=[],
    )
    assert result.id == str(row.id)
    assert eligibility.require_marketplace_actor.await_count == 2
    eligibility.require_marketplace_actor.assert_awaited_with(sender_id)
    repository.add_message.assert_not_awaited()
    with pytest.raises(ConflictError):
        await service.replay_message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            text="different",
            client_message_id=key,
            storage_keys=[],
        )


@pytest.mark.asyncio
async def test_media_race_recovers_replay_after_grant_consumption():
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.core.deps import Actor
    from app.models.enums import UserRole
    from app.routers.chat import send_chat_media
    from app.schemas.chat import SendChatMediaRequest
    from app.services.chat_service import ChatMessage

    actor = Actor(uuid4(), UserRole.CUSTOMER, "test-session")
    conversation_id = uuid4()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation_id),
        str(actor.id),
        "IMAGE",
        "",
        False,
        "2026-10-08T00:00:00+00:00",
    )
    chat, media, db = AsyncMock(), AsyncMock(), AsyncMock()
    chat.replay_message.side_effect = [None, dto]
    media.prepare.side_effect = ConflictError("Upload already used.")
    with (
        patch("app.routers.chat._service", return_value=chat),
        patch("app.routers.chat._media_service", return_value=media),
        patch("app.routers.chat.require_auth", new=AsyncMock(return_value=actor)),
        patch("app.routers.chat.mark_request_transaction", new=AsyncMock()),
    ):
        response = await send_chat_media(
            SimpleNamespace(),
            db,
            conversation_id,
            SendChatMediaRequest(storage_keys=["key"], client_message_id=uuid4()),
            actor,
        )
    assert response.id == dto.id
    assert chat.replay_message.await_count == 2
    media.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_replay_skips_decode_and_grant_consumption():
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.core.deps import Actor
    from app.models.enums import UserRole
    from app.routers.chat import send_chat_media
    from app.schemas.chat import SendChatMediaRequest
    from app.services.chat_service import ChatMessage

    actor = Actor(uuid4(), UserRole.CUSTOMER, "test-session")
    conversation_id = uuid4()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation_id),
        str(actor.id),
        "IMAGE",
        "",
        False,
        "2026-10-08T00:00:00+00:00",
    )
    chat, media, db = AsyncMock(), AsyncMock(), AsyncMock()
    chat.replay_message.return_value = dto
    with (
        patch("app.routers.chat._service", return_value=chat),
        patch("app.routers.chat._media_service", return_value=media),
    ):
        response = await send_chat_media(
            SimpleNamespace(),
            db,
            conversation_id,
            SendChatMediaRequest(storage_keys=["key"], client_message_id=uuid4()),
            actor,
        )
    assert response.id == dto.id
    media.prepare.assert_not_awaited()
    media.send.assert_not_awaited()


def test_socket_retry_identity_is_shared_with_rest_validation():
    from app.routers.chat import _extract_send_request

    key = uuid4()
    body = _extract_send_request('{"text":"hello", "client_message_id":"' + str(key) + '"}')
    assert body is not None and body.client_message_id == key
    assert _extract_send_request('{"text":"hello", "client_message_id":"invalid"}') is None


@pytest.mark.asyncio
async def test_completed_replay_requests_sender_acknowledgement():
    from app.routers.chat import _deliver_chat_message
    from app.services.chat_service import ChatMessage

    service = AsyncMock()
    service.publish_message.return_value = False
    dto = ChatMessage(
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        "TEXT",
        "hello",
        False,
        "2026-10-08T00:00:00+00:00",
    )
    assert not await _deliver_chat_message(service, dto)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["null", "{}", '{"text":"' + "x" * 4001 + '"}'])
async def test_invalid_socket_payload_reports_error_then_accepts_valid_message(invalid: str):
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.core.deps import Actor
    from app.models.enums import UserRole
    from app.routers.chat import _pump_socket_to_chat
    from app.services.chat_service import ChatMessage

    actor = Actor(uuid4(), UserRole.CUSTOMER, "test-session")
    conversation_id = uuid4()
    chat, db, redis = AsyncMock(), AsyncMock(), AsyncMock()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation_id),
        str(actor.id),
        "TEXT",
        "literal valid",
        False,
        "2026-10-08T00:00:00+00:00",
    )
    chat.send_message.return_value = dto
    chat.publish_message.return_value = True
    db.__aenter__.return_value = db
    websocket = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=make_test_settings())),
        receive_text=AsyncMock(
            side_effect=[invalid, '{"text":"literal valid"}', StopAsyncIteration]
        ),
        send_text=AsyncMock(),
    )
    with (
        patch("app.routers.chat._session_service", return_value=chat),
        patch("app.routers.chat.mark_request_transaction", new=AsyncMock()),
        patch("app.routers.chat.set_audit_actor", new=AsyncMock()),
        patch("app.routers.chat._require_live_authorization", new=AsyncMock()) as authorization,
        patch("app.routers.chat.RateLimiter") as limiter,
    ):
        limiter.return_value.check_guarded = AsyncMock(
            return_value=SimpleNamespace(blocked=False, allowed=True)
        )
        with pytest.raises(StopAsyncIteration):
            await _pump_socket_to_chat(
                websocket, conversation_id, actor, uuid4(), lambda: db, redis
            )
    assert authorization.await_count == 2
    chat.send_message.assert_awaited_once_with(
        conversation_id=conversation_id,
        sender_id=actor.id,
        text="literal valid",
        client_message_id=None,
    )
    db.commit.assert_awaited_once()
    websocket.send_text.assert_awaited_once()
    assert json.loads(websocket.send_text.await_args.args[0]) == {
        "error": {"code": "VALIDATION_ERROR", "message": "Invalid chat message."}
    }


def test_live_delivery_migration_registers_committed_metadata_audit():
    import importlib
    from unittest.mock import patch

    migration = importlib.import_module("app.migrations.versions.0026_chat_retry_recovery")
    with patch.object(migration, "op") as operations:
        migration.upgrade()
    statements = [str(call.args[0]) for call in operations.execute.call_args_list]
    assert (
        "CREATE TRIGGER trg_chat_live_deliveries_audit "
        "AFTER INSERT OR UPDATE OR DELETE ON chat_live_deliveries "
        "FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('message_id')"
    ) in statements
