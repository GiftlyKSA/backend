"""Purpose-bound OTP reauthentication, phone mutation and recoverable account requests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import HTTPError
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationDomainError,
)
from app.core.ratelimit import RateLimiter
from app.core.security import generate_session_token, sha256_hex
from app.models import User
from app.models.account_change import AccountChangeChallenge, AccountRequest, AccountRequestEmail
from app.models.enums import UserRole, UserStatus
from app.repositories.account_change_repository import AccountChangeRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.repositories.user_repository import UserRepository
from app.services.otp_service import OtpService

_INVALID = "Invalid or expired account-change proof."


class AccountChangeUnavailableError(DomainError):
    """Shared OTP abuse prevention could not be validated."""

    code = "ACCOUNT_CHANGE_UNAVAILABLE"
    message = "Account verification is temporarily unavailable. Please try again later."
    status_code = 503


class AccountChangeService:
    """Serialize account mutations and keep credentials separate from normal login."""

    def __init__(
        self,
        *,
        users: UserRepository,
        repo: AccountChangeRepository,
        auth: AuthRepository,
        otp: OtpService,
        audit: AuditRepository,
        limiter: RateLimiter,
    ) -> None:
        """Wire existing authentication and persistence boundaries."""
        self._users = users
        self._repo = repo
        self._auth = auth
        self._otp = otp
        self._audit = audit
        self._limiter = limiter

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def _active_user(self, actor_id: uuid.UUID) -> User:
        user = await self._users.get_for_update(actor_id)
        if (
            user is None
            or user.deleted_at is not None
            or user.status in {UserStatus.BANNED, UserStatus.DELETED}
        ):
            raise UnauthorizedError(_INVALID)
        if user.role not in {UserRole.CUSTOMER, UserRole.COURIER}:
            raise ForbiddenError("This action requires a customer or courier account.")
        return user

    async def _issue_challenge(
        self, user: User | None, phone: str, purpose: str
    ) -> tuple[AccountChangeChallenge, str | None]:
        identity = (
            f"account-change:{user.id}"
            if user is not None and purpose != "RESTORE_ACCOUNT"
            else f"account-recovery:{sha256_hex(phone)}"
        )
        decision = await self._limiter.check(identity)
        if decision.unavailable:
            raise AccountChangeUnavailableError()
        if not decision.allowed:
            raise RateLimitedError(decision.retry_after_seconds)
        now = self._now()
        row = AccountChangeChallenge(
            id=uuid.uuid4(),
            user_id=user.id if user else None,
            phone=phone,
            purpose=purpose,
            auth_version=user.auth_version if user else None,
            expires_at=now + timedelta(seconds=60),
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(row)
        await self._repo.commit_challenge()
        try:
            code = await self._otp.request_otp(
                phone, scope=f"account-change:{row.id}", ttl_seconds=60
            )
        except HTTPError, RedisError, TimeoutError:
            raise AccountChangeUnavailableError() from None
        return row, code

    async def request_reauthentication(
        self, actor_id: uuid.UUID, purpose: str
    ) -> tuple[AccountChangeChallenge, str | None]:
        """Send a current-phone OTP for exactly one sensitive operation."""
        if purpose not in {"PHONE_CHANGE", "ACCOUNT_REQUEST"}:
            raise ValidationDomainError("Unsupported reauthentication purpose.")
        user = await self._active_user(actor_id)
        return await self._issue_challenge(user, user.phone, purpose)

    @classmethod
    def _pending_challenge(
        cls, row: AccountChangeChallenge | None, *, purposes: set[str]
    ) -> AccountChangeChallenge:
        if (
            row is None
            or row.purpose not in purposes
            or row.expires_at <= cls._now()
            or row.verified_at is not None
            or row.consumed_at is not None
        ):
            raise UnauthorizedError(_INVALID)
        return row

    @classmethod
    def _owned_challenge(
        cls, row: AccountChangeChallenge | None, user: User, *, purposes: set[str]
    ) -> AccountChangeChallenge:
        row = cls._pending_challenge(row, purposes=purposes)
        if row.user_id != user.id or row.auth_version != user.auth_version:
            raise UnauthorizedError(_INVALID)
        if row.purpose != "NEW_PHONE" and row.phone != user.phone:
            raise UnauthorizedError(_INVALID)
        return row

    async def _verify_challenge(
        self, user: User, challenge_id: uuid.UUID, code: str, *, purposes: set[str]
    ) -> AccountChangeChallenge:
        row = self._owned_challenge(
            await self._repo.challenge(challenge_id, lock=True), user, purposes=purposes
        )
        if not await self._otp.verify_otp(row.phone, code, scope=f"account-change:{row.id}"):
            raise UnauthorizedError(_INVALID)
        row.verified_at = self._now()
        return row

    async def confirm_reauthentication(
        self, actor_id: uuid.UUID, challenge_id: uuid.UUID, code: str
    ) -> str:
        """Convert the current-phone challenge into a five-minute, one-use proof."""
        user = await self._active_user(actor_id)
        row = await self._verify_challenge(
            user, challenge_id, code, purposes={"PHONE_CHANGE", "ACCOUNT_REQUEST"}
        )
        token = generate_session_token()
        row.proof_hash = sha256_hex(token)
        row.proof_expires_at = self._now() + timedelta(seconds=300)
        await self._repo.flush()
        return token

    async def _consume_proof(self, user: User, token: str, purpose: str) -> None:
        row = await self._repo.proof(sha256_hex(token))
        if (
            row is None
            or row.user_id != user.id
            or row.phone != user.phone
            or row.purpose != purpose
            or row.auth_version != user.auth_version
            or row.verified_at is None
            or row.proof_expires_at is None
            or row.proof_expires_at <= self._now()
            or row.consumed_at is not None
        ):
            raise UnauthorizedError(_INVALID)
        row.consumed_at = self._now()

    async def request_phone(
        self, actor_id: uuid.UUID, phone: str, token: str
    ) -> tuple[AccountChangeChallenge, str | None]:
        """Consume current-phone proof before sending the isolated new-phone OTP."""
        user = await self._active_user(actor_id)
        await self._consume_proof(user, token, "PHONE_CHANGE")
        if phone == user.phone:
            raise ValidationDomainError("Choose a different phone number.")
        return await self._issue_challenge(user, phone, "NEW_PHONE")

    async def confirm_phone(self, actor_id: uuid.UUID, challenge_id: uuid.UUID, code: str) -> str:
        """Change a verified phone under its unique constraint and revoke all devices."""
        user = await self._active_user(actor_id)
        row = await self._verify_challenge(user, challenge_id, code, purposes={"NEW_PHONE"})
        if await self._users.get_by_phone(row.phone) is not None:
            raise ConflictError("The phone change could not be completed.")
        try:
            await self._repo.change_phone(user.id, row.phone)
        except IntegrityError as exc:
            raise ConflictError("The phone change could not be completed.") from exc
        row.consumed_at = self._now()
        await self._revoke(user.id)
        await self._audit.record(
            actor_user_id=user.id, action="PHONE_CHANGE", entity_type="users", entity_id=user.id
        )
        await self._repo.flush()
        return row.phone

    async def _revoke(self, user_id: uuid.UUID) -> None:
        await self._auth.invalidate_user_credentials(user_id, self._now())
        await self._repo.remove_devices(user_id)

    async def submit_account_request(
        self, actor_id: uuid.UUID, *, kind: str, confirmation: str, token: str, reason: str | None
    ) -> tuple[AccountRequest, str]:
        """Disable access for fourteen-day recovery, retaining obligations for review."""
        user = await self._active_user(actor_id)
        await self._consume_proof(user, token, "ACCOUNT_REQUEST")
        required_kind, required_confirmation = (
            ("DELETE_ACCOUNT", "DELETE")
            if user.role is UserRole.CUSTOMER
            else ("TERMINATE_CONTRACT", "TERMINATE")
        )
        if (kind, confirmation) != (required_kind, required_confirmation):
            raise ValidationDomainError(
                "The request kind and confirmation do not match your account."
            )
        if not user.email:
            raise ValidationDomainError("Add an email address before requesting account closure.")
        if await self._repo.active_request(user.id, lock=True) is not None:
            raise ConflictError("An account request is already pending review.")
        now = self._now()
        receipt = generate_session_token()
        row = AccountRequest(
            id=uuid.uuid4(),
            user_id=user.id,
            kind=kind,
            status="RECOVERABLE",
            prior_status=user.status.value,
            disabled_auth_version=user.auth_version + 1,
            restore_deadline=now + timedelta(days=14),
            receipt_hash=sha256_hex(receipt),
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(row)
        user.status = UserStatus.DELETED
        user.deleted_at = now
        user.deletion_reason = "Recoverable account request pending administrative review."
        await self._revoke(user.id)
        await self._repo.save(
            AccountRequestEmail(
                request_id=row.id,
                to_email=user.email,
                body=self._email_body(row),
                created_at=now,
                updated_at=now,
            )
        )
        await self._audit.record(
            actor_user_id=user.id,
            action="ACCOUNT_REQUEST",
            entity_type="account_requests",
            entity_id=row.id,
        )
        await self._repo.flush()
        return row, receipt

    @classmethod
    def effective_status(cls, row: AccountRequest) -> str:
        """Apply the deadline even if the scheduled review sweep is delayed."""
        if row.status == "RECOVERABLE" and row.restore_deadline <= cls._now():
            return "PENDING_REVIEW"
        return row.status

    @staticmethod
    def _email_body(row: AccountRequest) -> str:
        return (
            f"Your Giftly {row.kind.lower().replace('_', ' ')} request has been received. "
            "For an active request, you can restore using the current-phone recovery OTP before "
            f"{row.restore_deadline.isoformat()}. "
            "Your account was disabled when this request was submitted, and existing sessions "
            "and device registrations were revoked at that time. If the request remains active "
            "after the 14-day recovery window, it requires administrative review. "
            "Permanent deletion or contract termination is not automatic. "
            "Please contact Giftly support through the published support channel to arrange "
            "a support call and discuss your request and any funds. Active orders, disputes, "
            "payments, withdrawals and account funds remain subject to review and the applicable "
            "settlement process. This request does not automatically cancel obligations, "
            "refund money or forfeit balances. Financial and audit records are retained "
            "pending the approved legal retention policy."
        )

    async def get_account_request(
        self, *, actor_id: uuid.UUID | None = None, receipt: str | None = None
    ) -> AccountRequest:
        """Return the own request through normal auth or a separate restricted receipt."""
        row = (
            await self._repo.active_request(actor_id)
            if actor_id is not None
            else await self._repo.receipt(sha256_hex(receipt or ""))
        )
        if row is None:
            if actor_id is not None:
                raise NotFoundError("Account request not found.")
            raise UnauthorizedError("Invalid account-request receipt or unavailable request.")
        return row

    @classmethod
    def _require_recoverable(cls, user: User, row: AccountRequest | None) -> None:
        if (
            row is None
            or row.user_id != user.id
            or row.status != "RECOVERABLE"
            or (user.role.value, row.kind)
            not in {("CUSTOMER", "DELETE_ACCOUNT"), ("COURIER", "TERMINATE_CONTRACT")}
            or row.prior_status not in {"ACTIVE", "PENDING_VERIFICATION", "REJECTED"}
            or row.restore_deadline <= cls._now()
            or user.auth_version != row.disabled_auth_version
            or user.status is not UserStatus.DELETED
            or user.deleted_at != row.created_at
            or user.deletion_reason != "Recoverable account request pending administrative review."
        ):
            raise UnauthorizedError(_INVALID)

    async def request_restoration(self, phone: str) -> tuple[AccountChangeChallenge, str | None]:
        """Return identical challenge metadata without revealing account existence."""
        user = await self._users.get_by_phone(phone)
        eligible = None
        if user is not None:
            user = await self._users.get_for_update(user.id)
            if user is not None:
                row = await self._repo.active_request(user.id, lock=True)
                try:
                    self._require_recoverable(user, row)
                    eligible = user
                except UnauthorizedError:
                    pass
        return await self._issue_challenge(eligible, phone, "RESTORE_ACCOUNT")

    async def confirm_restoration(self, challenge_id: uuid.UUID, code: str) -> AccountRequest:
        """Restore only an OTP-proven pending request; never mint login credentials."""
        initial = self._pending_challenge(
            await self._repo.challenge(challenge_id), purposes={"RESTORE_ACCOUNT"}
        )
        # Dummy and recoverable challenges share OTP attempts before account eligibility.
        if not await self._otp.verify_otp(
            initial.phone, code, scope=f"account-change:{initial.id}"
        ):
            raise UnauthorizedError(_INVALID)
        if initial.user_id is None:
            raise UnauthorizedError(_INVALID)
        user = await self._users.get_for_update(initial.user_id)
        if user is None:
            raise UnauthorizedError(_INVALID)
        row = await self._repo.active_request(user.id, lock=True)
        self._require_recoverable(user, row)
        assert row is not None
        challenge = self._owned_challenge(
            await self._repo.challenge(challenge_id, lock=True), user, purposes={"RESTORE_ACCOUNT"}
        )
        challenge.verified_at = self._now()
        await self._repo.attribute_recovery(user.id, user.role.value)
        challenge.consumed_at = self._now()
        user.status = UserStatus(row.prior_status)
        user.deleted_at = None
        user.deletion_reason = None
        row.status = "RESTORED"
        row.restored_at = self._now()
        row.updated_at = row.restored_at
        await self._revoke(user.id)
        await self._audit.record(
            actor_user_id=user.id,
            action="ACCOUNT_RESTORED",
            entity_type="account_requests",
            entity_id=row.id,
        )
        await self._repo.flush()
        return row
