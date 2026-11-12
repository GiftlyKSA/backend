"""Durable email delivery acknowledges both success and retryable provider failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.models.account_change import AccountRequestEmail


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", [False, True])
async def test_email_send_outside_database_transaction_and_fenced_ack(
    monkeypatch, provider_failure
) -> None:
    import app.core.config

    from tests.conftest import make_test_settings

    monkeypatch.setattr(app.core.config, "get_settings", lambda: make_test_settings())
    from app.workers import account_requests

    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = session
    finish = AsyncMock()
    monkeypatch.setattr(
        account_requests,
        "AccountChangeRepository",
        lambda active_session: SimpleNamespace(finish_email=finish),
    )
    row = AccountRequestEmail(
        request_id=uuid4(),
        to_email="owner@example.test",
        body="Restore before deadline, then administrative review.",
        lease_id=uuid4(),
        attempts=2,
    )
    email = AsyncMock()

    async def send(*args, **kwargs):
        assert factory.call_count == 0
        if provider_failure:
            raise TimeoutError("fake email outage")

    email.send_transactional.side_effect = send
    assert (
        await account_requests._deliver_email(row, email=email, factory=factory)
        is not provider_failure
    )
    email.send_transactional.assert_awaited_once_with(
        row.to_email, "account-request", {"request_id": str(row.request_id), "body": row.body}
    )
    assert finish.await_args.args == (row.request_id, row.lease_id)
    assert finish.await_args.kwargs["sent"] is not provider_failure
    assert finish.await_args.kwargs["attempts"] == 2
    session.commit.assert_awaited_once()


async def test_account_request_job_preserves_due_email_when_deadline_is_near(monkeypatch) -> None:
    from app.workers import account_requests

    from tests.conftest import make_test_settings

    factory = MagicMock()
    factory.return_value.__aenter__.return_value = AsyncMock()
    repository = SimpleNamespace(
        advance_review=AsyncMock(), purge_expired_challenges=AsyncMock(), claim_email=AsyncMock()
    )
    monkeypatch.setattr(account_requests, "AccountChangeRepository", lambda _session: repository)

    assert (
        await account_requests.process_account_requests(
            email=AsyncMock(),
            factory=factory,
            settings=make_test_settings(),
            deadline=asyncio.get_running_loop().time() + 1,
        )
        == 0
    )
    repository.advance_review.assert_awaited_once()
    repository.claim_email.assert_not_awaited()
