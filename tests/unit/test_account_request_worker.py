"""Durable email delivery acknowledges both success and retryable provider failure."""

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
