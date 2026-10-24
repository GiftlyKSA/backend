"""Multi-key attachments acquire fresh grant locks in stable order."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError
from app.integrations.storage.base import ObjectHead
from app.services.media_service import MediaService

from tests.conftest import make_test_settings


async def test_multi_key_claims_are_sorted_and_duplicate_keys_rejected() -> None:
    actor = uuid4()
    keys = sorted(f"orders/pending/{uuid4()}.jpg" for _ in range(2))
    rows = [
        SimpleNamespace(
            storage_key=key,
            owner_user_id=actor,
            purpose="ORDER_REQUEST",
            confirmed_at=datetime.now(UTC),
            content_type="image/jpeg",
            byte_size=100,
            attached_at=None,
            deleting_at=None,
        )
        for key in keys
    ]
    uploads = AsyncMock()
    uploads.get_many.return_value = list(reversed(rows))
    uploads.claim_confirmed_many.return_value = set(keys)
    media = MediaService(AsyncMock(), make_test_settings(), uploads)
    media._prepared = {
        (actor, "ORDER_REQUEST", key): ObjectHead(True, 100, "image/jpeg") for key in keys
    }
    heads = await media.claim_many(list(reversed(keys)), actor_id=actor, purpose="ORDER_REQUEST")
    uploads.get_many.assert_awaited_once_with(keys, for_update=True)
    uploads.claim_confirmed_many.assert_awaited_once_with(keys, actor, "ORDER_REQUEST")
    assert set(heads) == set(keys)
    with pytest.raises(ConflictError):
        await media.claim_many([keys[0], keys[0]], actor_id=actor, purpose="ORDER_REQUEST")
    assert uploads.get_many.await_count == 1


async def test_ten_delivery_proofs_claim_in_stable_order() -> None:
    actor = uuid4()
    keys = sorted(f"orders/proof/{uuid4()}.jpg" for _ in range(10))
    rows = [
        SimpleNamespace(
            storage_key=key,
            owner_user_id=actor,
            purpose="DELIVERY_PROOF",
            confirmed_at=datetime.now(UTC),
            content_type="image/jpeg",
            byte_size=100,
            attached_at=None,
            deleting_at=None,
        )
        for key in keys
    ]
    uploads = AsyncMock()
    uploads.get_many.return_value = list(reversed(rows))
    uploads.claim_confirmed_many.return_value = set(keys)
    media = MediaService(AsyncMock(), make_test_settings(), uploads)
    media._prepared = {
        (actor, "DELIVERY_PROOF", key): ObjectHead(True, 100, "image/jpeg") for key in keys
    }

    heads = await media.claim_many(list(reversed(keys)), actor_id=actor, purpose="DELIVERY_PROOF")

    assert set(heads) == set(keys)
    uploads.get_many.assert_any_await(keys, for_update=True)
    uploads.claim_confirmed_many.assert_awaited_once_with(keys, actor, "DELIVERY_PROOF")
