"""PostgreSQL replay locking and durable live lease regression proof."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.core.db import build_engine
from app.core.exceptions import ConflictError, ForbiddenError
from app.models import ChatLiveDelivery, ChatNotification, Message
from app.repositories.chat_live_delivery_repository import ChatLiveDeliveryRepository
from app.services.chat_live_delivery_service import send_pending_chat_live_deliveries
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import make_test_settings
from tests.integration.test_chat_service import _conversation, _service


@pytest.fixture(autouse=True)
def _require_disposable_database(db_connection) -> None:
    """Use the shared availability fixture for standalone committed-worker checks."""


async def test_replay_and_payload_conflict_do_not_duplicate(db_session: AsyncSession) -> None:
    customer, _, conversation = await _conversation(db_session)
    service = _service(db_session, AsyncMock())
    key = uuid4()
    first = await service.send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="hello", client_message_id=key
    )
    second = await service.send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="hello", client_message_id=key
    )
    assert second == first
    assert conversation.courier_unread_count == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ChatNotification)
            .where(ChatNotification.message_id == UUID(first.id))
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ChatLiveDelivery)
            .where(ChatLiveDelivery.message_id == UUID(first.id))
        )
        == 1
    )
    with pytest.raises(ConflictError):
        await service.send_message(
            conversation_id=conversation.id,
            sender_id=customer.id,
            text="changed",
            client_message_id=key,
        )
    with pytest.raises(ForbiddenError):
        await service.send_message(
            conversation_id=conversation.id, sender_id=uuid4(), text="hello", client_message_id=key
        )


async def test_live_claims_skip_owned_lease_and_fence_expired_owner(
    db_session: AsyncSession,
) -> None:
    customer, _, conversation = await _conversation(db_session)
    dto = await _service(db_session, AsyncMock()).send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="hello"
    )
    repository = ChatLiveDeliveryRepository(db_session)
    now = datetime.now(UTC) + timedelta(seconds=1)
    first = await repository.claim_pending(
        now=now, limit=1, lease_seconds=30, message_id=UUID(dto.id)
    )
    assert len(first.claims) == 1
    assert not (
        await repository.claim_pending(now=now, limit=1, lease_seconds=30, message_id=UUID(dto.id))
    ).claims
    later = now + timedelta(seconds=31)
    second = await repository.claim_pending(
        now=later, limit=1, lease_seconds=30, message_id=UUID(dto.id)
    )
    assert len(second.claims) == 1
    assert not await repository.advance(first.claims[0], now=later)
    await repository.retry(first.claims[0], now=later)
    assert await repository.advance(second.claims[0], now=later)


async def test_committed_concurrent_retry_and_outage_recovery() -> None:
    settings = make_test_settings(DATABASE_URL=os.environ["DATABASE_URL"])
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            customer, _, conversation = await _conversation(session)
            conversation_id, sender_id = conversation.id, customer.id
            await session.commit()
        key = uuid4()

        async def send() -> str:
            async with factory() as session:
                dto = await _service(session, AsyncMock()).send_message(
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text="concurrent",
                    client_message_id=key,
                )
                await session.commit()
                return dto.id

        ids = await asyncio.gather(send(), send())
        assert ids[0] == ids[1]
        redis = AsyncMock()
        redis.publish.side_effect = TimeoutError()
        assert (
            await send_pending_chat_live_deliveries(
                limit=1, message_id=UUID(ids[0]), redis=redis, factory=factory, settings=settings
            )
            == 0
        )
        async with factory() as session:
            row = await session.get(ChatLiveDelivery, UUID(ids[0]))
            assert row is not None and row.attempts == 1 and row.completed_at is None
            row.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        redis.publish.side_effect = None
        assert (
            await send_pending_chat_live_deliveries(
                limit=1, message_id=UUID(ids[0]), redis=redis, factory=factory, settings=settings
            )
            == 1
        )
        assert (
            await send_pending_chat_live_deliveries(
                limit=1, message_id=UUID(ids[0]), redis=redis, factory=factory, settings=settings
            )
            == 0
        )
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
            assert count == 1
    finally:
        await engine.dispose()


async def test_live_delivery_exhaustion_is_terminal(db_session: AsyncSession) -> None:
    customer, _, conversation = await _conversation(db_session)
    dto = await _service(db_session, AsyncMock()).send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="retry bound"
    )
    row = await db_session.get(ChatLiveDelivery, UUID(dto.id))
    assert row is not None
    row.attempts = 7
    await db_session.flush()
    repository = ChatLiveDeliveryRepository(db_session)
    now = datetime.now(UTC) + timedelta(seconds=1)
    batch = await repository.claim_pending(
        now=now, limit=1, lease_seconds=30, message_id=UUID(dto.id)
    )
    assert batch.claims[0].attempts == 8
    await repository.retry(batch.claims[0], now=now)
    assert not (
        await repository.claim_pending(
            now=now + timedelta(hours=1), limit=1, lease_seconds=30, message_id=UUID(dto.id)
        )
    ).claims


async def test_chat_migration_roundtrip_preserves_message_ciphertext(db_connection) -> None:
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = importlib.import_module("app.migrations.versions.0026_chat_retry_recovery")

    def roundtrip(connection):
        before = connection.execute(
            text("SELECT id, content_encrypted FROM messages ORDER BY id")
        ).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert (
                connection.execute(text("SELECT to_regclass('chat_live_deliveries')")).scalar()
                is None
            )
            migration.upgrade()
        after = connection.execute(
            text("SELECT id, content_encrypted FROM messages ORDER BY id")
        ).all()
        assert after == before

    await db_connection.run_sync(roundtrip)


@pytest.mark.parametrize("replay", [False, True])
async def test_ban_committed_while_waiting_for_conversation_lock_rejects_send(replay: bool) -> None:
    from app.models import Conversation, User
    from app.models.enums import UserStatus
    from sqlalchemy import update

    settings = make_test_settings(DATABASE_URL=os.environ["DATABASE_URL"])
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key = uuid4()
    try:
        async with factory() as session:
            customer, _, conversation = await _conversation(session)
            conversation_id, sender_id = conversation.id, customer.id
            if replay:
                await _service(session, AsyncMock()).send_message(
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text="locked",
                    client_message_id=key,
                )
            await session.commit()
        async with factory() as blocker:
            await blocker.scalar(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            reached_lock = asyncio.Event()

            async def send() -> None:
                async with factory() as session:
                    service = _service(session, AsyncMock())
                    original = service._chat.get_for_actor

                    async def get_for_actor(*args, **kwargs):
                        reached_lock.set()
                        return await original(*args, **kwargs)

                    service._chat.get_for_actor = get_for_actor
                    await service.send_message(
                        conversation_id=conversation_id,
                        sender_id=sender_id,
                        text="locked",
                        client_message_id=key,
                    )
                    await session.commit()

            task = asyncio.create_task(send())
            await asyncio.wait_for(reached_lock.wait(), 5)
            async with factory() as ban_session:
                await ban_session.execute(
                    update(User).where(User.id == sender_id).values(status=UserStatus.BANNED)
                )
                await ban_session.commit()
            await blocker.commit()
            with pytest.raises(ForbiddenError):
                await asyncio.wait_for(task, 5)
        async with factory() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            ) == int(replay)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("inactive", ["BANNED", "DELETED", "ERASED"])
async def test_live_worker_inactive_customer_cannot_decrypt_or_publish(inactive: str) -> None:
    from unittest.mock import patch

    from app.models.enums import UserStatus

    settings = make_test_settings(DATABASE_URL=os.environ["DATABASE_URL"])
    engine = build_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = AsyncMock()
    try:
        async with factory() as session:
            customer, _, conversation = await _conversation(session)
            dto = await _service(session, redis).send_message(
                conversation_id=conversation.id, sender_id=customer.id, text="private"
            )
            if inactive == "ERASED":
                customer.deleted_at = datetime.now(UTC)
            else:
                customer.status = UserStatus(inactive)
            await session.flush()
            intent = await session.get(ChatLiveDelivery, UUID(dto.id))
            assert intent is not None
            intent.attempts = 7
            await session.commit()
        with patch("app.services.chat_live_delivery_service.build_cipher") as cipher:
            assert (
                await send_pending_chat_live_deliveries(
                    limit=1,
                    message_id=UUID(dto.id),
                    redis=redis,
                    factory=factory,
                    settings=settings,
                )
                == 0
            )
            cipher.return_value.decrypt.assert_not_called()
        redis.publish.assert_not_awaited()
        async with factory() as session:
            row = await session.get(ChatLiveDelivery, UUID(dto.id))
            assert row is not None and row.attempts == 8 and row.completed_at is None
            assert row.failed_at is not None
        assert (
            await send_pending_chat_live_deliveries(
                limit=1,
                message_id=UUID(dto.id),
                redis=redis,
                factory=factory,
                settings=settings,
            )
            == 0
        )
        redis.publish.assert_not_awaited()
    finally:
        await engine.dispose()


async def test_live_delivery_metadata_audit_is_system_and_transactional(
    db_session: AsyncSession,
    db_connection,
) -> None:
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.models import AuditLog
    from sqlalchemy import delete

    migration = importlib.import_module("app.migrations.versions.0026_chat_retry_recovery")

    def apply_draft_migration(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    await db_connection.run_sync(apply_draft_migration)
    customer, _, conversation = await _conversation(db_session)
    dto = await _service(db_session, AsyncMock()).send_message(
        conversation_id=conversation.id, sender_id=customer.id, text="private audit payload"
    )
    message_id = UUID(dto.id)
    row = await db_session.get(ChatLiveDelivery, message_id)
    assert row is not None
    row.attempts = 1
    await db_session.flush()
    await db_session.execute(
        delete(ChatLiveDelivery).where(ChatLiveDelivery.message_id == message_id)
    )
    rows = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "chat_live_deliveries",
                AuditLog.entity_id == message_id,
            )
        )
    )
    assert {row.action for row in rows} == {"CREATE", "UPDATE", "DELETE"}
    assert len(rows) == 3
    assert all(row.actor_user_id is None for row in rows)
    assert all(row.audit_metadata == {"actor_category": "SYSTEM"} for row in rows)
    async with db_session.begin_nested() as nested:
        db_session.add(ChatLiveDelivery(message_id=message_id))
        await db_session.flush()
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_type == "chat_live_deliveries",
                    AuditLog.entity_id == message_id,
                )
            )
            == 4
        )
        await nested.rollback()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_type == "chat_live_deliveries",
                AuditLog.entity_id == message_id,
            )
        )
        == 3
    )
