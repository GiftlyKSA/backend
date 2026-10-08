"""PostgreSQL attachment inserts preserve atomic claims and participant scope."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.core.exceptions import ConflictError
from app.models import MediaUpload, Message, MessageAttachment
from app.repositories.chat_repository import AttachmentInput, ChatRepository
from app.repositories.media_repository import MediaRepository
from app.services.chat_media_service import ChatMediaService, ValidatedAttachment
from sqlalchemy import func, select

from tests.conftest import make_test_settings
from tests.integration.test_chat_service import _conversation, _service


@pytest.mark.parametrize("count", [1, 5])
async def test_batch_insert_has_distinct_ids_and_foreign_sender_cannot_insert(db_session, count):
    customer, courier, conversation = await _conversation(db_session)
    message = await _service(db_session, AsyncMock()).send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="Images"
    )
    attachments = [
        AttachmentInput(f"chat/{conversation.id}/{uuid4()}.jpg", "image/jpeg", 12, i, None)
        for i in range(count)
    ]
    repository = ChatRepository(db_session)
    assert (
        await repository.add_attachments(
            message_id=UUID(message.id), actor_id=courier.id, attachments=attachments
        )
        == []
    )
    saved = await repository.add_attachments(
        message_id=UUID(message.id), actor_id=customer.id, attachments=attachments
    )
    assert len(saved) == count and len({row.id for row in saved}) == count
    assert [row.storage_key for row in saved] == [item.storage_key for item in attachments]
    assert all(row.created_at is not None for row in saved)


async def test_partial_claim_rolls_back_all_grants_and_message(db_session, monkeypatch):
    customer, courier, conversation = await _conversation(db_session)
    keys = [f"chat/{conversation.id}/{uuid4()}.jpg" for _ in range(2)]
    db_session.add_all(
        [
            MediaUpload(
                storage_key=key,
                owner_user_id=customer.id,
                purpose="CHAT_ATTACHMENT",
                content_type="image/jpeg",
                byte_size=12,
            )
            for key in keys
        ]
    )
    await db_session.flush()
    uploads = MediaRepository(db_session)
    claim = uploads.confirm_and_claim_many

    async def partial_claim(storage_keys, actor, purpose):
        return await claim(storage_keys[:1], actor, purpose)

    monkeypatch.setattr(uploads, "confirm_and_claim_many", partial_claim)
    service = ChatMediaService(
        session=db_session,
        chat=_service(db_session, AsyncMock()),
        repository=ChatRepository(db_session),
        uploads=uploads,
        storage=AsyncMock(),
        settings=make_test_settings(),
        redis=AsyncMock(),
        eligibility=AsyncMock(),
    )
    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await service.send(
                conversation_id=conversation.id,
                actor_id=customer.id,
                text="",
                attachments=[ValidatedAttachment(key, "image/jpeg", 12) for key in keys],
            )
    rows = await uploads.get_many(keys)
    assert all(row.confirmed_at is None and row.attached_at is None for row in rows)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation.id)
        )
        == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(MessageAttachment)) == 0
