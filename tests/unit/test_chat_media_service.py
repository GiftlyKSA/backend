"""Ownership, one-time grants and bounded validation for chat media."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    MediaValidationUnavailableError,
    NotFoundError,
)
from app.integrations.storage.base import ObjectHead
from app.models.enums import MessageType
from app.services.chat_media_service import ChatMediaService, ValidatedAttachment
from app.services.chat_service import ChatMessage, ChatService

from tests.conftest import make_test_settings


def stack():
    session, chat, repository, uploads, storage, redis, eligibility = [
        AsyncMock() for _ in range(7)
    ]
    service = ChatMediaService(
        session=session,
        chat=chat,
        repository=repository,
        uploads=uploads,
        storage=storage,
        settings=make_test_settings(),
        redis=redis,
        eligibility=eligibility,
    )
    return service, SimpleNamespace(
        session=session,
        chat=chat,
        repository=repository,
        uploads=uploads,
        storage=storage,
        redis=redis,
        eligibility=eligibility,
    )


def grant(actor, conversation):
    return SimpleNamespace(
        owner_user_id=actor,
        purpose="CHAT_ATTACHMENT",
        storage_key=f"chat/{conversation}/{uuid4()}.jpg",
        content_type="image/jpeg",
        byte_size=12,
        attached_at=None,
        deleting_at=None,
    )


@pytest.mark.asyncio
async def test_foreign_conversation_upload_denied_before_storage():
    service, deps = stack()
    deps.chat.get_conversation_for_actor.side_effect = NotFoundError("Conversation not found.")
    with pytest.raises(NotFoundError):
        await service.request_upload(
            conversation_id=uuid4(), actor_id=uuid4(), kind="IMAGE", mime="image/jpeg", size=12
        )
    deps.storage.create_upload_url.assert_not_awaited()
    deps.uploads.issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_presign_releases_reads_and_precedes_quota_lock():
    service, deps = stack()
    released = False

    async def release():
        nonlocal released
        released = True

    async def presign(**kwargs):
        assert released
        assert deps.uploads.issue.await_count == 0
        return "signed-url"

    deps.storage.create_upload_url.side_effect = presign
    result = await service.request_upload(
        conversation_id=uuid4(),
        actor_id=uuid4(),
        kind="IMAGE",
        mime="image/jpeg",
        size=12,
        release_reads=release,
        resume_writes=AsyncMock(),
    )
    assert result[0] == "signed-url"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign_owner,foreign_conversation,used",
    [(True, False, False), (False, True, False), (False, False, True)],
)
async def test_prepare_rejects_foreign_or_consumed_grants(
    foreign_owner, foreign_conversation, used
):
    service, deps = stack()
    actor, conversation = uuid4(), uuid4()
    row = grant(
        uuid4() if foreign_owner else actor, uuid4() if foreign_conversation else conversation
    )
    row.attached_at = "used" if used else None
    deps.uploads.get_many.return_value = [row]
    with pytest.raises((BadRequestError, ConflictError)):
        await service.prepare(conversation_id=conversation, actor_id=actor, keys=[row.storage_key])
    deps.storage.read_bounded_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_keys_rejected_before_reads():
    service, deps = stack()
    with pytest.raises(BadRequestError):
        await service.prepare(conversation_id=uuid4(), actor_id=uuid4(), keys=["key", "key"])
    deps.uploads.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_rejects_real_size_mismatch():
    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, 12, "image/jpeg")
    deps.storage.read_bounded_object.return_value = b"short"
    with pytest.raises(BadRequestError):
        await service._verify(ValidatedAttachment("key", "image/jpeg", 12))


@pytest.mark.asyncio
async def test_losing_atomic_claim_does_not_attach_or_publish():
    service, deps = stack()
    actor, conversation = uuid4(), uuid4()
    row = grant(actor, conversation)
    deps.uploads.get_many.return_value = [row]
    deps.uploads.confirm_and_claim_many.return_value = set()
    deps.chat.send_message.return_value = ChatMessage(
        str(uuid4()), str(conversation), str(actor), "IMAGE", "", False, "2026-10-03T00:00:00+00:00"
    )
    with pytest.raises(ConflictError):
        await service.send(
            conversation_id=conversation,
            actor_id=actor,
            attachments=[ValidatedAttachment(row.storage_key, row.content_type, row.byte_size)],
            text="",
        )
    deps.repository.add_attachments.assert_not_awaited()
    deps.chat.publish_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_playback_hides_foreign_attachment():
    service, deps = stack()
    deps.repository.attachment_for_actor.return_value = None
    with pytest.raises(NotFoundError):
        await service.playback(attachment_id=uuid4(), actor_id=uuid4())
    deps.storage.signed_read_url.assert_not_called()


@pytest.mark.asyncio
async def test_validation_closes_read_transaction_before_storage_work():
    service, deps = stack()
    actor, conversation = uuid4(), uuid4()
    row = grant(actor, conversation)
    deps.uploads.get_many.return_value = [row]
    deps.redis.set.return_value = True

    async def verify(item):
        deps.session.commit.assert_awaited_once()
        return item

    service._verify = verify
    result = await service.prepare(
        conversation_id=conversation, actor_id=actor, keys=[row.storage_key]
    )
    assert result[0].key == row.storage_key
    deps.redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_global_decoder_slots_fail_before_reading_bytes():
    service, deps = stack()
    actor, conversation = uuid4(), uuid4()
    row = grant(actor, conversation)
    deps.uploads.get_many.return_value = [row]
    deps.redis.set.return_value = False
    with pytest.raises(MediaValidationUnavailableError):
        await service.prepare(conversation_id=conversation, actor_id=actor, keys=[row.storage_key])
    assert deps.redis.set.await_count == 2
    deps.storage.read_bounded_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 5, 30])
async def test_history_fetches_attachments_in_one_batch(count):
    repository = AsyncMock()
    conversation = uuid4()
    service = ChatService(
        chat=repository, redis=AsyncMock(), settings=make_test_settings(), eligibility=AsyncMock()
    )
    rows = [
        SimpleNamespace(
            id=uuid4(),
            conversation_id=conversation,
            sender_id=uuid4(),
            message_type=MessageType.TEXT,
            is_read=False,
            created_at=datetime.now(UTC),
            content_encrypted=service._encrypt_content(conversation, "Hello"),
        )
        for _ in range(count)
    ]
    repository.list_messages.return_value = rows
    repository.attachments_for_messages.return_value = []
    result = await service.list_messages(
        conversation_id=conversation, actor_id=uuid4(), limit=30, before_id=None
    )
    assert len(result) == count
    repository.attachments_for_messages.assert_awaited_once_with([row.id for row in rows])


@pytest.mark.asyncio
async def test_recording_streams_to_file_and_removes_it_after_validation():
    from pathlib import Path
    from unittest.mock import patch

    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, 9, "audio/ogg")
    deps.storage.read_bounded_object.side_effect = AssertionError("Recording buffered in memory")

    async def chunks(*args, **kwargs):
        yield b"record"
        yield b"ing"

    deps.storage.iter_bounded_object = chunks
    paths = []

    async def decode(path, **kwargs):
        paths.append(Path(path))
        assert paths[-1].read_bytes() == b"recording"
        return 1.0

    with patch("app.services.chat_media_service.verify_recording_file", decode, create=True):
        result = await service._verify(ValidatedAttachment("key", "audio/ogg", 9))
    assert result.duration == 1
    assert paths and not paths[0].exists()
    deps.storage.read_bounded_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,total", [([b"short"], 9), ([b"recording", b"x"], 9)])
async def test_recording_stream_size_mismatch_never_decodes(chunks, total):
    from unittest.mock import patch

    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, total, "audio/ogg")

    async def stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    deps.storage.iter_bounded_object = stream
    with patch(
        "app.services.chat_media_service.verify_recording_file", new=AsyncMock(), create=True
    ) as decode:
        with pytest.raises(BadRequestError):
            await service._verify(ValidatedAttachment("key", "audio/ogg", total))
        decode.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_recording_decoder_failure_or_cancellation_removes_private_file(cancel):
    import asyncio
    from pathlib import Path
    from unittest.mock import patch

    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, 9, "audio/ogg")

    async def chunks(*args, **kwargs):
        yield b"recording"

    deps.storage.iter_bounded_object = chunks
    paths = []

    async def decode(path, **kwargs):
        paths.append(Path(path))
        assert paths[-1].exists()
        if cancel:
            raise asyncio.CancelledError
        raise BadRequestError("bad recording")

    with patch("app.services.chat_media_service.verify_recording_file", decode, create=True):
        with pytest.raises(asyncio.CancelledError if cancel else BadRequestError):
            await service._verify(ValidatedAttachment("key", "audio/ogg", 9))
    assert paths and not paths[0].exists()


@pytest.mark.asyncio
async def test_streamed_recording_waits_for_disk_write_before_cancelled_cleanup(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import patch

    from app.services import chat_media_validation as validation

    started, release = threading.Event(), threading.Event()
    paths = []
    original = validation.Path.open

    def blocking_open(path, *args, **kwargs):
        paths.append(path)
        started.set()
        assert release.wait(5)
        return original(path, *args, **kwargs)

    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, 9, "audio/ogg")

    async def chunks(*args, **kwargs):
        yield b"recording"

    deps.storage.iter_bounded_object = chunks
    monkeypatch.setattr(validation.Path, "open", blocking_open)
    with patch(
        "app.services.chat_media_service.verify_recording_file", new=AsyncMock(), create=True
    ) as decode:
        task = asyncio.create_task(service._verify(ValidatedAttachment("key", "audio/ogg", 9)))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert paths[0].parent.exists()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not paths[0].exists()
        decode.assert_not_awaited()


@pytest.mark.asyncio
async def test_recording_download_failure_removes_file_and_closes_stream(monkeypatch):
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from app.services import chat_media_validation as validation

    directories = []
    closed = False

    def directory(**kwargs):
        result = TemporaryDirectory(**kwargs)
        directories.append(validation.Path(result.name))
        return result

    service, deps = stack()
    deps.storage.head_object.return_value = ObjectHead(True, 9, "audio/ogg")

    async def chunks(*args, **kwargs):
        nonlocal closed
        try:
            yield b"record"
            raise OSError("read failed")
        finally:
            closed = True

    deps.storage.iter_bounded_object = chunks
    monkeypatch.setattr(validation, "TemporaryDirectory", directory)
    with patch(
        "app.services.chat_media_service.verify_recording_file", new=AsyncMock(), create=True
    ) as decode:
        with pytest.raises(OSError, match="read failed"):
            await service._verify(ValidatedAttachment("key", "audio/ogg", 9))
        decode.assert_not_awaited()
    assert closed
    assert directories and not directories[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head",
    [
        None,
        ObjectHead(False, 9, "audio/ogg"),
        ObjectHead(True, 8, "audio/ogg"),
        ObjectHead(True, 9, "audio/webm"),
    ],
)
async def test_recording_rejects_head_mismatch_before_streaming(head):
    from unittest.mock import Mock

    service, deps = stack()
    deps.storage.head_object.return_value = head
    deps.storage.iter_bounded_object = Mock()
    with pytest.raises(BadRequestError):
        await service._verify(ValidatedAttachment("key", "audio/ogg", 9))
    deps.storage.iter_bounded_object.assert_not_called()
