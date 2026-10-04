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
async def test_prepared_claim_rechecks_grant_without_storage_waits(race):
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
    uploads.claim.return_value = True
    released = False

    async def release():
        nonlocal released
        released = True

    async def head(key):
        assert released
        return ObjectHead(True, 12, "image/jpeg")

    storage.head_object.side_effect = head
    storage.verify_image_magic_bytes.return_value = True
    service = MediaService(storage, make_test_settings(), uploads)
    await service.prepare_claims(
        [row.storage_key], actor_id=actor, purpose="ORDER_REQUEST", release_reads=release
    )
    storage.head_object.side_effect = AssertionError("S3 wait inside attachment transaction")
    if race == "metadata":
        row.byte_size = 13
    elif race == "owner":
        row.owner_user_id = uuid4()
    elif race == "purpose":
        row.purpose = "DELIVERY_PROOF"
    elif race == "confirmation":
        row.confirmed_at = None
    elif race == "cleanup":
        row.deleting_at = datetime.now(UTC)
    elif race == "consumed":
        uploads.claim.return_value = False
    if race:
        with pytest.raises((BadRequestError, ConflictError)):
            await service.claim(row.storage_key, actor_id=actor, purpose="ORDER_REQUEST")
    else:
        result = await service.claim(row.storage_key, actor_id=actor, purpose="ORDER_REQUEST")
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
async def test_confirm_releases_reads_before_storage_and_rechecks_before_mutation():
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
    storage.verify_image_magic_bytes.return_value = True
    with pytest.raises(ConflictError):
        await MediaService(storage, make_test_settings(), uploads).confirm(
            row.storage_key, actor_id=actor, release_reads=release, resume_writes=resume
        )
    uploads.mark_confirmed.assert_not_awaited()
