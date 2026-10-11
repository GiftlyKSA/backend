"""Rollback must preserve restored requests, email history and usable credentials."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.security import sha256_hex
from app.models.account_change import AccountChangeChallenge, AccountRequest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_account_changes import proof, seed_user, service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retained", ["restored_history", "pending_request", "active_otp", "active_proof"]
)
async def test_account_request_downgrade_refuses_retained_state(
    db_session: AsyncSession, retained: str
) -> None:
    owner = await seed_user(db_session)
    workflow = service(db_session)
    if retained in {"restored_history", "pending_request"}:
        token = await proof(db_session, owner, "ACCOUNT_REQUEST")
        request, _ = await workflow.submit_account_request(
            owner.id, kind="DELETE_ACCOUNT", confirmation="DELETE", token=token, reason=None
        )
        if retained == "restored_history":
            challenge, _ = await workflow.request_restoration(owner.phone)
            await workflow.confirm_restoration(challenge.id, "123456")
            assert request.status == "RESTORED"
    else:
        now = datetime.now(UTC)
        db_session.add(
            AccountChangeChallenge(
                id=uuid4(),
                user_id=owner.id,
                phone=owner.phone,
                purpose="PHONE_CHANGE",
                auth_version=0,
                expires_at=now + timedelta(seconds=60)
                if retained == "active_otp"
                else now - timedelta(seconds=60),
                proof_hash=sha256_hex(str(uuid4())) if retained == "active_proof" else None,
                proof_expires_at=now + timedelta(seconds=300)
                if retained == "active_proof"
                else None,
            )
        )
        await db_session.flush()
    migration = import_module("app.migrations.versions.0031_account_requests")

    def attempt(sync_session):
        context = MigrationContext.configure(sync_session.connection())
        with Operations.context(context):
            with pytest.raises(RuntimeError, match="Retain account-request history"):
                migration.downgrade()

    await db_session.run_sync(attempt)
    assert await db_session.scalar(select(func.count()).select_from(AccountRequest)) == (
        1 if retained in {"restored_history", "pending_request"} else 0
    )
