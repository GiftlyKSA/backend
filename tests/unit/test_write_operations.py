"""Owned operation-key replay, conflicts, encryption and uncertain outcomes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, NotFoundError
from app.services.operation_service import OperationPendingError, OperationService
from pydantic import BaseModel

from tests.conftest import make_test_settings


class Result(BaseModel):
    id: str
    description: str


def stack():
    repo = AsyncMock()
    repo.get.return_value = None
    repo.resource_accessible.return_value = True
    repo.claim.return_value = (
        SimpleNamespace(
            id=uuid4(),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            result_encrypted=None,
            resource_id=None,
        ),
        True,
    )
    return OperationService(repo, make_test_settings()), repo


async def test_operation_encrypts_response_and_replays_identical_payload():
    service, repo = stack()
    owner, key, resource = uuid4(), uuid4(), uuid4()
    row = await service.begin(owner, "order.create", key, {"text": "<script>data</script>"})
    await service.finish(row, Result(id=str(resource), description="private"), resource)
    assert "private" not in row.result_encrypted
    repo.get.return_value = row
    replay = await service.find(owner, "order.create", key, {"text": "<script>data</script>"})
    assert Result.model_validate_json(service.result(replay)).description == "private"
    with pytest.raises(ConflictError):
        await service.find(owner, "order.create", key, {"text": "different"})
    repo.get.assert_awaited_with(owner, "order.create", key)


async def test_attachment_order_is_part_of_operation_identity():
    service, repo = stack()
    owner, key = uuid4(), uuid4()
    row = await service.begin(owner, "order.create", key, {"keys": ["a", "b"]})
    repo.get.return_value = row
    with pytest.raises(ConflictError):
        await service.find(owner, "order.create", key, {"keys": ["b", "a"]})


async def test_committed_pending_claim_never_reexecutes():
    service, repo = stack()
    owner, key = uuid4(), uuid4()
    row = await service.begin(owner, "wallet.topup", key, {"amount": "100.00"})
    repo.claim.return_value = (row, False)
    with pytest.raises(OperationPendingError):
        await service.begin(owner, "wallet.topup", key, {"amount": "100.00"})


async def test_expired_and_reassigned_results_cannot_be_read():
    service, repo = stack()
    owner, key = uuid4(), uuid4()
    row = await service.begin(owner, "order.create", key, {})
    await service.finish(row, Result(id=str(uuid4()), description="retained"), uuid4())
    repo.get.return_value = row
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(NotFoundError):
        await service.find(owner, "order.create", key)
    row.expires_at = datetime.now(UTC) + timedelta(hours=1)
    row.resource_id = uuid4()
    repo.resource_accessible.return_value = False
    with pytest.raises(NotFoundError):
        await service.find(owner, "order.create", key)


async def test_unresolved_operation_remains_recoverable_after_expiry():
    service, repo = stack()
    owner, key = uuid4(), uuid4()
    row = await service.begin(owner, "wallet.topup", key, {"amount": "100.00"})
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    repo.get.return_value = row
    repo.claim.return_value = (row, False)
    assert await service.find(owner, "wallet.topup", key) is row
    with pytest.raises(OperationPendingError):
        await service.begin(owner, "wallet.topup", key, {"amount": "100.00"})
