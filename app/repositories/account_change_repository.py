"""Persistence for account-change challenges, receipts and notification delivery."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import set_audit_actor
from app.core.db import emit_committed_audit_events
from app.models import DeviceToken
from app.models.account_change import AccountChangeChallenge, AccountRequest, AccountRequestEmail


class AccountChangeRepository:
    """Bounded queries and row locks used after the owning account is locked."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one unit of work."""
        self._session = session

    async def save(
        self, row: AccountChangeChallenge | AccountRequest | AccountRequestEmail
    ) -> None:
        """Persist a new row."""
        self._session.add(row)
        await self._session.flush()

    async def commit_challenge(self) -> None:
        """Publish a challenge and consumed proof before waiting on SMS delivery."""
        await self._session.commit()
        emit_committed_audit_events(self._session)

    async def challenge(
        self, challenge_id: uuid.UUID, *, lock: bool = False
    ) -> AccountChangeChallenge | None:
        """Select one challenge with optional fresh row locking."""
        query = select(AccountChangeChallenge).where(AccountChangeChallenge.id == challenge_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        result: AccountChangeChallenge | None = await self._session.scalar(query)
        return result

    async def proof(self, proof_hash: str) -> AccountChangeChallenge | None:
        """Lock a proof for one-use consumption."""
        result: AccountChangeChallenge | None = await self._session.scalar(
            select(AccountChangeChallenge)
            .where(AccountChangeChallenge.proof_hash == proof_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return result

    async def active_request(
        self, user_id: uuid.UUID, *, lock: bool = False
    ) -> AccountRequest | None:
        """Read the only unresolved request for an account."""
        query = select(AccountRequest).where(
            AccountRequest.user_id == user_id, AccountRequest.status != "RESTORED"
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        result: AccountRequest | None = await self._session.scalar(query)
        return result

    async def receipt(self, receipt_hash: str) -> AccountRequest | None:
        """Resolve only the restricted request receipt, never an access token."""
        result: AccountRequest | None = await self._session.scalar(
            select(AccountRequest).where(AccountRequest.receipt_hash == receipt_hash)
        )

        return result

    async def attribute_recovery(self, user_id: uuid.UUID, role: str) -> None:
        """Attribute proven recovery writes before mutation triggers run."""
        await set_audit_actor(self._session, category=role, actor_user_id=user_id)

    async def remove_devices(self, user_id: uuid.UUID) -> None:
        """Delete push registrations owned by this account."""
        await self._session.execute(delete(DeviceToken).where(DeviceToken.user_id == user_id))

    async def change_phone(self, user_id: uuid.UUID, phone: str) -> None:
        """Persist phone mutation inside a savepoint so uniqueness errors are contained."""
        from app.models import User

        async with self._session.begin_nested():
            await self._session.execute(update(User).where(User.id == user_id).values(phone=phone))
            await self._session.flush()

    async def flush(self) -> None:
        """Flush transitions before the request commits."""
        await self._session.flush()

    async def claim_email(self, now: datetime) -> AccountRequestEmail | None:
        """Lease one ready email; the caller commits before external I/O."""
        row = await self._session.scalar(
            select(AccountRequestEmail)
            .where(
                AccountRequestEmail.sent_at.is_(None),
                AccountRequestEmail.failed_at.is_(None),
                AccountRequestEmail.available_at <= now,
                or_(
                    AccountRequestEmail.leased_until.is_(None),
                    AccountRequestEmail.leased_until <= now,
                ),
            )
            .order_by(AccountRequestEmail.available_at, AccountRequestEmail.request_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is not None:
            if row.attempts >= 5:
                row.failed_at = now
                await self._session.flush()
                return None
            row.lease_id = uuid.uuid4()
            row.leased_until = now + timedelta(seconds=60)
            row.attempts += 1
            row.updated_at = now
            await self._session.flush()
        return row

    async def finish_email(
        self,
        request_id: uuid.UUID,
        lease_id: uuid.UUID,
        *,
        now: datetime,
        sent: bool,
        attempts: int,
    ) -> None:
        """Fence delivery acknowledgment and retry at most five provider attempts."""
        await self._session.execute(
            update(AccountRequestEmail)
            .where(
                AccountRequestEmail.request_id == request_id,
                AccountRequestEmail.lease_id == lease_id,
                AccountRequestEmail.leased_until > now,
            )
            .values(
                sent_at=now if sent else None,
                failed_at=now if not sent and attempts >= 5 else None,
                available_at=now + timedelta(seconds=min(3600, 60 * 2**attempts)),
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )

    async def advance_review(self, now: datetime, *, limit: int = 100) -> int:
        """Advance expired recovery windows for review without changing account or money."""
        ids = (
            select(AccountRequest.id)
            .where(AccountRequest.status == "RECOVERABLE", AccountRequest.restore_deadline <= now)
            .order_by(AccountRequest.restore_deadline, AccountRequest.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(
            update(AccountRequest)
            .where(AccountRequest.id.in_(ids))
            .values(status="PENDING_REVIEW", updated_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def purge_expired_challenges(self, now: datetime, *, limit: int = 1000) -> int:
        """Remove only bounded expired OTP metadata, retaining still-usable proofs."""
        ids = (
            select(AccountChangeChallenge.id)
            .where(
                AccountChangeChallenge.expires_at <= now,
                or_(
                    AccountChangeChallenge.proof_expires_at.is_(None),
                    AccountChangeChallenge.proof_expires_at <= now,
                ),
            )
            .order_by(AccountChangeChallenge.expires_at, AccountChangeChallenge.id)
            .limit(min(max(limit, 1), 1000))
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(
            delete(AccountChangeChallenge).where(AccountChangeChallenge.id.in_(ids))
        )
        return int(getattr(result, "rowcount", 0) or 0)
