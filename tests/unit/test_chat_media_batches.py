"""Attachment count must not multiply persistence round trips or weaken grants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.services.chat_media_service import ValidatedAttachment
from app.services.chat_service import ChatMessage

from tests.unit.test_chat_media_service import grant, stack


def batch(count):
    service, deps = stack()
    actor, conversation = uuid4(), uuid4()
    rows = [grant(actor, conversation) for _ in range(count)]
    deps.uploads.get_many.return_value = rows[::-1]
    keys = [row.storage_key for row in rows]
    deps.uploads.confirm_and_claim_many.return_value = set(keys)
    deps.chat.send_message.return_value = ChatMessage(
        str(uuid4()), str(conversation), str(actor), "IMAGE", "", False, "2026-10-08T00:00:00Z"
    )
    deps.repository.add_attachments.return_value = [
        SimpleNamespace(
            id=uuid4(),
            content_type=row.content_type,
            byte_size=row.byte_size,
            display_order=index,
            duration_seconds=None,
        )
        for index, row in enumerate(rows)
    ]
    return service, deps, actor, conversation, rows, keys


@pytest.mark.parametrize("count", [1, 5])
async def test_prepare_fetches_grants_once_and_preserves_order(count):
    service, deps, actor, conversation, rows, keys = batch(count)
    deps.redis.set.return_value = True
    service._verify = AsyncMock(side_effect=lambda item: item)
    result = await service.prepare(conversation_id=conversation, actor_id=actor, keys=keys)
    assert [item.key for item in result] == keys
    deps.uploads.get_many.assert_awaited_once()
    deps.uploads.get.assert_not_awaited()


@pytest.mark.parametrize("count", [1, 5])
async def test_send_uses_three_batch_operations_independent_of_count(count):
    service, deps, actor, conversation, rows, keys = batch(count)
    result = await service.send(
        conversation_id=conversation,
        actor_id=actor,
        text="",
        attachments=[
            ValidatedAttachment(row.storage_key, row.content_type, row.byte_size) for row in rows
        ],
    )
    assert len(result.attachments) == count
    deps.uploads.get_many.assert_awaited_once_with(keys, for_update=True)
    deps.uploads.confirm_and_claim_many.assert_awaited_once_with(keys, actor, "CHAT_ATTACHMENT")
    deps.repository.add_attachments.assert_awaited_once()
    deps.uploads.get.assert_not_awaited()
    deps.uploads.mark_confirmed.assert_not_awaited()
    deps.uploads.claim.assert_not_awaited()
    deps.repository.add_attachment.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    ["missing", "foreign", "used", "deleting", "changed", "partial_claim", "partial_insert"],
)
async def test_send_rejects_any_invalid_or_partial_batch(failure):
    service, deps, actor, conversation, rows, keys = batch(2)
    attachments = [
        ValidatedAttachment(row.storage_key, row.content_type, row.byte_size) for row in rows
    ]
    if failure == "missing":
        deps.uploads.get_many.return_value = rows[:1]
    elif failure == "foreign":
        rows[0].owner_user_id = uuid4()
    elif failure == "used":
        rows[0].attached_at = "used"
    elif failure == "deleting":
        rows[0].deleting_at = "deleting"
    elif failure == "changed":
        rows[0].byte_size += 1
    elif failure == "partial_claim":
        deps.uploads.confirm_and_claim_many.return_value = {keys[0]}
    else:
        deps.repository.add_attachments.return_value = deps.repository.add_attachments.return_value[
            :1
        ]
    with pytest.raises((BadRequestError, ConflictError, NotFoundError)):
        await service.send(
            conversation_id=conversation, actor_id=actor, attachments=attachments, text=""
        )
    deps.chat.publish_message.assert_not_awaited()
    deps.session.commit.assert_not_awaited()


@pytest.mark.parametrize("count", [0, 6])
async def test_send_rejects_unbounded_batch_before_any_mutation(count):
    service, deps, actor, conversation, rows, keys = batch(count)
    with pytest.raises(BadRequestError):
        await service.send(
            conversation_id=conversation,
            actor_id=actor,
            text="",
            attachments=[
                ValidatedAttachment(row.storage_key, row.content_type, row.byte_size)
                for row in rows
            ],
        )
    deps.chat.send_message.assert_not_awaited()
