"""Order/proof media batch SQL stays constant without changing per-object validation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.integrations.storage.base import ObjectHead
from app.models import MediaUpload, User
from app.models.enums import UserRole
from app.repositories.media_repository import MediaRepository
from app.services.media_service import MediaService
from sqlalchemy import event

from tests.conftest import make_test_settings


@pytest.mark.parametrize("count", [1, 5])
async def test_prepared_media_batch_uses_three_statements(db_session, monkeypatch, count):
    actor = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
    db_session.add(actor)
    await db_session.flush()
    keys = [f"orders/pending/{uuid4()}.jpg" for _ in range(count)]
    db_session.add_all(
        [
            MediaUpload(
                storage_key=key,
                owner_user_id=actor.id,
                purpose="ORDER_REQUEST",
                content_type="image/jpeg",
                byte_size=100,
                confirmed_at=datetime.now(UTC),
            )
            for key in keys
        ]
    )
    await db_session.flush()
    media = MediaService(AsyncMock(), make_test_settings(), MediaRepository(db_session))
    verify = AsyncMock(return_value=ObjectHead(True, 100, "image/jpeg"))
    monkeypatch.setattr(media, "_verify_object", verify)
    connection = await db_session.connection()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "media_uploads" in statement:
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        await media.prepare_claims(
            keys, actor_id=actor.id, purpose="ORDER_REQUEST", release_reads=AsyncMock()
        )
        heads = await media.claim_many(
            list(reversed(keys)), actor_id=actor.id, purpose="ORDER_REQUEST"
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
    assert len(statements) == 3
    assert set(heads) == set(keys)
    assert verify.await_count == count
    assert "ORDER BY media_uploads.storage_key" in statements[1]
    assert "FOR UPDATE" in statements[1]


async def test_partial_batch_claim_rolls_back_all_attachment_mutations(db_session, monkeypatch):
    from app.core.exceptions import ConflictError
    from sqlalchemy import select

    actor = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
    db_session.add(actor)
    await db_session.flush()
    actor_id = actor.id
    keys = sorted(f"orders/pending/{uuid4()}.jpg" for _ in range(2))
    db_session.add_all(
        [
            MediaUpload(
                storage_key=key,
                owner_user_id=actor_id,
                purpose="ORDER_REQUEST",
                content_type="image/jpeg",
                byte_size=100,
                confirmed_at=datetime.now(UTC),
            )
            for key in keys
        ]
    )
    await db_session.flush()
    repository = MediaRepository(db_session)
    original = repository.claim_confirmed_many

    async def partial(keys, actor, purpose):
        return await original(keys[:1], actor, purpose)

    monkeypatch.setattr(repository, "claim_confirmed_many", partial)
    media = MediaService(AsyncMock(), make_test_settings(), repository)
    media._prepared = {
        (actor_id, "ORDER_REQUEST", key): ObjectHead(True, 100, "image/jpeg") for key in keys
    }
    with pytest.raises(ConflictError):
        async with db_session.begin_nested():
            await media.claim_many(keys, actor_id=actor_id, purpose="ORDER_REQUEST")
    states = list(
        await db_session.scalars(
            select(MediaUpload.attached_at).where(MediaUpload.storage_key.in_(keys))
        )
    )
    assert states == [None, None]


async def test_repository_batch_never_confirms_or_consumes_an_unconfirmed_grant(db_session):
    from sqlalchemy import select

    actor = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
    db_session.add(actor)
    await db_session.flush()
    key = f"orders/pending/{uuid4()}.jpg"
    db_session.add(
        MediaUpload(
            storage_key=key,
            owner_user_id=actor.id,
            purpose="ORDER_REQUEST",
            content_type="image/jpeg",
            byte_size=100,
        )
    )
    await db_session.flush()
    assert (
        await MediaRepository(db_session).claim_confirmed_many([key], actor.id, "ORDER_REQUEST")
        == set()
    )
    state = (
        await db_session.execute(
            select(MediaUpload.confirmed_at, MediaUpload.attached_at).where(
                MediaUpload.storage_key == key
            )
        )
    ).one()
    assert tuple(state) == (None, None)
