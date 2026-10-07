"""Chat sends stay independent of slow providers and persist their push intent."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from app.models import Message
from app.models.enums import MessageType
from app.repositories.chat_repository import ChatRepository
from app.routers.chat import _deliver_chat_message
from app.services.chat_service import ChatMessage


async def test_live_delivery_does_not_wait_for_push_provider() -> None:
    pending = asyncio.Event()

    async def wait_for_provider(*_args: object) -> None:
        await pending.wait()

    push = AsyncMock(side_effect=wait_for_provider)
    chat = AsyncMock()
    message = ChatMessage(
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        "TEXT",
        "hi",
        False,
        datetime.now(UTC).isoformat(),
    )
    with patch("app.routers.chat._notify_chat_recipient", new=push, create=True):
        delivered = await asyncio.wait_for(
            _deliver_chat_message(chat, message),
            timeout=0.1,
        )
    assert delivered is True
    push.assert_not_awaited()


@pytest.mark.parametrize("message_type", [MessageType.TEXT, MessageType.VOICE])
async def test_message_and_recipient_intent_share_transaction(message_type: MessageType) -> None:
    sender, recipient = uuid.uuid4(), uuid.uuid4()
    added: list[object] = []

    async def flush() -> None:
        for row in added:
            if isinstance(row, Message) and row.id is None:
                row.id = uuid.uuid4()
                row.created_at = datetime.now(UTC)

    session = SimpleNamespace(add=Mock(side_effect=added.append), flush=flush)
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        customer_id=sender,
        courier_id=recipient,
        customer_unread_count=0,
        courier_unread_count=0,
    )
    message = await ChatRepository(session).add_message(
        conversation=conversation,
        sender_id=sender,
        content_encrypted="ciphertext",
        preview_encrypted="preview",
        sender_is_customer=True,
        message_type=message_type,
    )
    intents = [row for row in added if type(row).__name__ == "ChatNotification"]
    assert len(intents) == 1
    assert intents[0].message_id == message.id
    assert intents[0].recipient_id == recipient


@pytest.mark.parametrize("fail", [False, True, "timeout"])
async def test_sweeper_commits_claim_and_closes_sessions_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: object,
    fail: bool | str,
) -> None:
    from app.repositories.chat_notification_repository import (
        ChatNotificationClaimBatch,
        ClaimedChatNotification,
    )
    from app.services import chat_notification_service as delivery

    open_sessions = 0
    commits = 0
    sent: list[str] = []

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

    class Push:
        async def send_push(self, tokens, title, body):
            assert open_sessions == 0
            assert commits == 1
            assert "hi" not in body
            if fail == "timeout":
                await asyncio.Event().wait()
            if fail:
                raise RuntimeError("provider failed")
            sent.extend(tokens)

    token_id = uuid.uuid4()
    claim = ClaimedChatNotification(uuid.uuid4(), uuid.uuid4(), None, uuid.uuid4(), 1)
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=ChatNotificationClaimBatch([claim], 0)),
        token_page=AsyncMock(return_value=[(token_id, "recipient-token")]),
        advance=AsyncMock(return_value=True),
        retry=AsyncMock(),
    )
    monkeypatch.setattr(delivery, "ChatNotificationRepository", lambda _session: repository)
    if fail == "timeout":
        monkeypatch.setattr(delivery, "_DELIVERY_TIMEOUT_SECONDS", 0.001)
    processed = await delivery.send_pending_chat_notifications(
        limit=1,
        push=Push(),
        factory=Session,
        settings=test_settings,
    )
    assert processed == (0 if fail else 1)
    assert sent == ([] if fail else ["recipient-token"])
    assert open_sessions == 0
    assert commits == 2
    if fail:
        repository.advance.assert_not_awaited()
        repository.retry.assert_awaited_once()
    else:
        assert repository.advance.await_args.kwargs["cursor"] == token_id
        assert repository.advance.await_args.kwargs["complete"] is True


@pytest.mark.parametrize(
    ("exhausted_count", "budget", "expected_sent"), [(1, 2, 1), (2, 3, 1), (21, 20, 0)]
)
async def test_retired_intents_do_not_stop_healthy_delivery_within_sweep_budget(
    test_settings: object,
    exhausted_count: int,
    budget: int,
    expected_sent: int,
) -> None:
    from app.services.chat_notification_service import send_pending_chat_notifications

    exhausted = [
        SimpleNamespace(message_id=uuid.uuid4(), attempts=8) for _ in range(exhausted_count)
    ]
    healthy = SimpleNamespace(
        message_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        cursor_token_id=None,
        attempts=0,
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.scalars.side_effect = [[row] for row in exhausted] + [[healthy], []]
    session.execute.return_value = SimpleNamespace(
        all=lambda: [SimpleNamespace(id=uuid.uuid4(), token="healthy-device")],
        rowcount=1,
    )
    push = AsyncMock()
    sent = await send_pending_chat_notifications(
        limit=budget,
        push=push,
        factory=lambda: session,
        settings=test_settings,
    )
    assert sent == expected_sent
    assert session.scalars.await_count == budget
    assert all(row.failed_at is not None for row in exhausted[:budget])
    if expected_sent:
        assert push.send_push.await_args.args[0] == ["healthy-device"]
    else:
        push.send_push.assert_not_awaited()
