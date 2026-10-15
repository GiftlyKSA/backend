"""Storage waits stay outside grant and attachment database critical sections."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError, ConflictError
from app.integrations.storage.base import ObjectHead
from app.services.media_service import MediaService

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_presign_finishes_before_quota_lock_is_acquired():
    uploads, storage = AsyncMock(), AsyncMock()

    async def presign(**kwargs):
        assert uploads.issue.await_count == 0
        return "signed-url"

    storage.create_upload_url.side_effect = presign
    result = await MediaService(storage, make_test_settings(), uploads).request_upload_url(
        actor_id=uuid4(), purpose="ORDER_REQUEST", content_type="image/jpeg", byte_size=12
    )
    assert result[0] == "signed-url"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "race", [None, "metadata", "owner", "purpose", "confirmation", "cleanup", "consumed"]
)
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_prepared_claim_rechecks_grant_without_storage_waits(race, monkeypatch, batch):
    actor = uuid4()
    row = SimpleNamespace(
        storage_key=f"orders/pending/{uuid4()}.jpg",
        owner_user_id=actor,
        purpose="ORDER_REQUEST",
        content_type="image/jpeg",
        byte_size=12,
        confirmed_at=datetime.now(UTC),
        attached_at=None,
        deleting_at=None,
    )
    uploads, storage = AsyncMock(), AsyncMock()
    uploads.get.return_value = row
    uploads.get_many.return_value = [row]
    uploads.claim.return_value = True
    uploads.claim_confirmed_many.return_value = {row.storage_key}
    released = False

    async def release():
        nonlocal released
        released = True

    async def head(key):
        assert released
        return ObjectHead(True, 12, "image/jpeg")

    storage.head_object.side_effect = head
    storage.read_bounded_object.return_value = b"x" * 12
    monkeypatch.setattr("app.services.media_service.verify_image_isolated", AsyncMock())
    service = MediaService(storage, make_test_settings(), uploads)
    await service.prepare_claims(
        [row.storage_key], actor_id=actor, purpose="ORDER_REQUEST", release_reads=release
    )
    storage.head_object.side_effect = AssertionError("S3 wait inside attachment transaction")
    changes = {
        "metadata": ("byte_size", 13),
        "owner": ("owner_user_id", uuid4()),
        "purpose": ("purpose", "DELIVERY_PROOF"),
        "confirmation": ("confirmed_at", None),
        "cleanup": ("deleting_at", datetime.now(UTC)),
    }
    if race in changes:
        name, value = changes[race]
        setattr(row, name, value)
    elif race == "consumed":
        uploads.claim.return_value = False
        uploads.claim_confirmed_many.return_value = set()

    async def claim():
        if batch:
            return (
                await service.claim_many([row.storage_key], actor_id=actor, purpose="ORDER_REQUEST")
            )[row.storage_key]
        return await service.claim(row.storage_key, actor_id=actor, purpose="ORDER_REQUEST")

    if race:
        with pytest.raises((BadRequestError, ConflictError)):
            await claim()
    else:
        result = await claim()
        assert result == ObjectHead(True, 12, "image/jpeg")


@pytest.mark.asyncio
async def test_failed_storage_presign_does_not_reserve_upload_quota():
    uploads, storage = AsyncMock(), AsyncMock()
    storage.create_upload_url.side_effect = RuntimeError("Storage unavailable")
    with pytest.raises(RuntimeError):
        await MediaService(storage, make_test_settings(), uploads).request_upload_url(
            actor_id=uuid4(), purpose="ORDER_REQUEST", content_type="image/jpeg", byte_size=12
        )
    uploads.issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_releases_reads_before_storage_and_rechecks_before_mutation(monkeypatch):
    actor = uuid4()
    row = SimpleNamespace(
        storage_key=f"orders/pending/{uuid4()}.jpg",
        owner_user_id=actor,
        content_type="image/jpeg",
        byte_size=12,
        attached_at=None,
        deleting_at=None,
    )
    uploads, storage = AsyncMock(), AsyncMock()
    uploads.get.return_value = row
    release, resume = AsyncMock(), AsyncMock()

    async def head(key):
        assert release.await_count == 1
        assert resume.await_count == 0
        return ObjectHead(True, 12, "image/jpeg")

    async def reauthenticate():
        row.byte_size = 13

    resume.side_effect = reauthenticate
    storage.head_object.side_effect = head
    storage.read_bounded_object.return_value = b"x" * 12
    monkeypatch.setattr("app.services.media_service.verify_image_isolated", AsyncMock())
    with pytest.raises(ConflictError):
        await MediaService(storage, make_test_settings(), uploads).confirm(
            row.storage_key, actor_id=actor, release_reads=release, resume_writes=resume
        )
    uploads.mark_confirmed.assert_not_awaited()
