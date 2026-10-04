"""Slow storage work cannot preserve a revoked actor's mutation authority."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.deps import Actor
from app.core.exceptions import UnauthorizedError
from app.integrations.storage.base import ObjectHead
from app.models.enums import UserRole
from app.routers.media import create_upload_url
from app.routers.orders import _prepare_media
from app.schemas.media import UploadUrlRequest

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_revoked_actor_cannot_receive_presigned_grant():
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    db, uploads, storage = AsyncMock(), AsyncMock(), AsyncMock()
    storage.create_upload_url.return_value = "signed-url"
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=make_test_settings(),
                clients=SimpleNamespace(storage=storage),
            )
        )
    )
    with (
        patch("app.routers.media.MediaRepository", return_value=uploads),
        patch("app.routers.media.mark_request_transaction", new=AsyncMock()),
        patch("app.routers.media.require_auth", new=AsyncMock(side_effect=UnauthorizedError)),
    ):
        with pytest.raises(UnauthorizedError):
            await create_upload_url(
                request,
                db,
                UploadUrlRequest(
                    purpose="ORDER_REQUEST",
                    content_type="image/jpeg",
                    byte_size=12,
                ),
                actor,
            )
    uploads.issue.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_actor_cannot_consume_prepared_order_upload():
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    db, uploads, storage = AsyncMock(), AsyncMock(), AsyncMock()
    key = f"orders/pending/{uuid4()}.jpg"
    uploads.get.return_value = SimpleNamespace(
        storage_key=key,
        owner_user_id=actor.id,
        purpose="ORDER_REQUEST",
        content_type="image/jpeg",
        byte_size=12,
        confirmed_at=datetime.now(UTC),
        attached_at=None,
        deleting_at=None,
    )

    async def head(storage_key):
        assert db.commit.await_count == 1
        return ObjectHead(True, 12, "image/jpeg")

    storage.head_object.side_effect = head
    storage.verify_image_magic_bytes.return_value = True
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=make_test_settings(),
                clients=SimpleNamespace(storage=storage),
            )
        )
    )
    with (
        patch("app.routers.orders.MediaRepository", return_value=uploads),
        patch("app.routers.orders.mark_request_transaction", new=AsyncMock()),
        patch("app.routers.orders.require_auth", new=AsyncMock(side_effect=UnauthorizedError)),
    ):
        with pytest.raises(UnauthorizedError):
            await _prepare_media(request, db, actor, [key], "ORDER_REQUEST")
    uploads.claim.assert_not_awaited()
    db.commit.assert_awaited_once()
