"""A short attachment transaction rechecks cleanup races after slow storage reads."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from app.core.config import Environment
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import ConflictError
from app.integrations.storage.fake import FakeStorageClient
from app.models import MediaUpload, User
from app.models.enums import UserRole
from app.repositories.media_repository import MediaRepository
from app.services.media_service import MediaService
from sqlalchemy import delete, select, update

from tests.conftest import make_test_settings


async def test_prepared_claim_loses_to_cleanup_in_independent_transaction(monkeypatch):
    overrides = {"DATABASE_URL": os.environ["DATABASE_URL"]} if os.getenv("DATABASE_URL") else {}
    settings = make_test_settings(**overrides)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001 — PostgreSQL proof is deferred to CI when unavailable.
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")
    storage = FakeStorageClient(Environment.TEST)
    owner_id = uuid.uuid4()
    key = ""
    try:
        async with factory() as session:
            session.add(
                User(
                    id=owner_id,
                    phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
                    role=UserRole.CUSTOMER,
                )
            )
            await session.flush()
            media = MediaService(storage, settings, MediaRepository(session))
            _, key, _ = await media.request_upload_url(
                actor_id=owner_id, purpose="ORDER_REQUEST", content_type="image/jpeg", byte_size=12
            )
            await media.confirm(key, actor_id=owner_id)
            await session.commit()

        original_head = storage.head_object
        async with factory() as attachment_session:

            async def racing_head(storage_key):
                assert not attachment_session.in_transaction()
                async with factory() as cleanup_session:
                    await cleanup_session.execute(
                        update(MediaUpload)
                        .where(MediaUpload.storage_key == storage_key)
                        .values(deleting_at=datetime.now(UTC))
                    )
                    await cleanup_session.commit()
                return await original_head(storage_key)

            monkeypatch.setattr(storage, "head_object", racing_head)
            media = MediaService(storage, settings, MediaRepository(attachment_session))
            await media.prepare_claims(
                [key],
                actor_id=owner_id,
                purpose="ORDER_REQUEST",
                release_reads=attachment_session.commit,
            )
            with pytest.raises(ConflictError):
                await media.claim(key, actor_id=owner_id, purpose="ORDER_REQUEST")
            await attachment_session.rollback()
        async with factory() as session:
            row = await MediaRepository(session).get(key)
            assert row is not None and row.attached_at is None
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == owner_id))
            await session.commit()
        await engine.dispose()
