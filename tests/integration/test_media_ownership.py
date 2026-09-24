"""Upload grants cannot be attached by another actor, purpose, or transaction."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from app.core.config import Environment
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import BadRequestError, ConflictError
from app.integrations.storage.fake import FakeStorageClient
from app.models import User
from app.models.enums import UserRole
from app.repositories.media_repository import MediaRepository
from app.services.media_service import MediaService
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_test_settings


class _PausingMediaRepository(MediaRepository):
    """Give a competing transaction time to lock its first client-ordered key."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self._paused = False

    async def claim(self, storage_key: str, actor_id: uuid.UUID, purpose: str) -> bool:
        """Pause after the first successful database claim."""
        claimed = await super().claim(storage_key, actor_id, purpose)
        if claimed and not self._paused:
            self._paused = True
            await asyncio.sleep(0.2)
        return claimed


async def _user(session: AsyncSession) -> User:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    session.add(user)
    await session.flush()
    return user


async def test_upload_owner_purpose_and_one_time_claim(db_session: AsyncSession) -> None:
    owner = await _user(db_session)
    other = await _user(db_session)
    media = MediaService(
        FakeStorageClient(Environment.TEST), make_test_settings(), MediaRepository(db_session)
    )
    _url, key, _ttl = await media.request_upload_url(
        actor_id=owner.id, purpose="ORDER_REQUEST", content_type="image/jpeg", byte_size=1000
    )

    with pytest.raises(BadRequestError):
        await media.confirm(key, actor_id=other.id)
    with pytest.raises(BadRequestError):
        await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")

    await media.confirm(key, actor_id=owner.id)
    with pytest.raises(BadRequestError):
        await media.claim(key, actor_id=owner.id, purpose="DELIVERY_PROOF")
    with pytest.raises(BadRequestError):
        await media.claim(key, actor_id=other.id, purpose="ORDER_REQUEST")
    claimed = await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")
    assert (claimed.content_type, claimed.byte_size) == ("image/jpeg", 1000)
    with pytest.raises(ConflictError):
        await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")


async def test_claim_rolls_back_with_failed_attachment(db_session: AsyncSession) -> None:
    owner = await _user(db_session)
    media = MediaService(
        FakeStorageClient(Environment.TEST), make_test_settings(), MediaRepository(db_session)
    )
    _url, key, _ttl = await media.request_upload_url(
        actor_id=owner.id, purpose="ORDER_REQUEST", content_type="image/png", byte_size=500
    )
    await media.confirm(key, actor_id=owner.id)
    async with db_session.begin_nested() as savepoint:
        await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")
        await savepoint.rollback()
    claimed = await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")
    assert (claimed.content_type, claimed.byte_size) == ("image/png", 500)


async def test_claim_rejects_changed_object_metadata(db_session: AsyncSession) -> None:
    owner = await _user(db_session)
    storage = FakeStorageClient(Environment.TEST)
    media = MediaService(storage, make_test_settings(), MediaRepository(db_session))
    _url, key, _ttl = await media.request_upload_url(
        actor_id=owner.id, purpose="ORDER_REQUEST", content_type="image/jpeg", byte_size=1000
    )
    await media.confirm(key, actor_id=owner.id)
    storage._objects[key] = storage._objects[key].__class__(  # noqa: SLF001
        exists=True, byte_size=1001, content_type="image/jpeg"
    )
    with pytest.raises(BadRequestError):
        await media.claim(key, actor_id=owner.id, purpose="ORDER_REQUEST")


async def test_opposite_key_orders_finish_without_deadlock() -> None:
    """Two independent sessions claim the same keys in opposing client order."""
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    settings = make_test_settings(**overrides)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")

    storage = FakeStorageClient(Environment.TEST)
    owner_id: uuid.UUID | None = None
    try:
        async with factory() as session:
            owner = await _user(session)
            owner_id = owner.id
            media = MediaService(storage, settings, MediaRepository(session))
            keys: list[str] = []
            for _ in range(2):
                _url, key, _ttl = await media.request_upload_url(
                    actor_id=owner.id,
                    purpose="ORDER_REQUEST",
                    content_type="image/jpeg",
                    byte_size=1000,
                )
                await media.confirm(key, actor_id=owner.id)
                keys.append(key)
            await session.commit()
        assert owner_id is not None

        async def submit(client_order: list[str]) -> str:
            async with factory() as session:
                media = MediaService(storage, settings, _PausingMediaRepository(session))
                try:
                    await media.claim_many(client_order, actor_id=owner_id, purpose="ORDER_REQUEST")
                    await session.commit()
                    return "claimed"
                except ConflictError:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.wait_for(
            asyncio.gather(submit(keys), submit(list(reversed(keys)))), timeout=5
        )
        assert sorted(results) == ["claimed", "conflict"]
    finally:
        if owner_id is not None:
            async with factory() as session:
                await session.execute(delete(User).where(User.id == owner_id))
                await session.commit()
        await engine.dispose()
