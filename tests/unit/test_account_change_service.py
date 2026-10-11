"""Security boundaries for actor-scoped phone changes and account recovery."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import UnauthorizedError
from app.models import User
from app.models.account_change import AccountChangeChallenge, AccountRequest
from app.models.enums import UserRole, UserStatus
from app.services.account_change_service import AccountChangeService


@pytest.fixture
def service() -> AccountChangeService:
    return AccountChangeService(
        users=AsyncMock(),
        repo=AsyncMock(),
        auth=AsyncMock(),
        otp=AsyncMock(),
        audit=AsyncMock(),
        limiter=AsyncMock(),
    )


def user() -> User:
    return User(
        id=uuid4(),
        phone="+966501234567",
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
        auth_version=3,
        email="customer@example.test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["actor", "purpose", "version", "expired", "replayed"])
async def test_reauthentication_proof_cannot_cross_scope(
    service: AccountChangeService, mismatch: str
) -> None:
    actor = user()
    proof = AccountChangeChallenge(
        id=uuid4(),
        user_id=actor.id,
        phone=actor.phone,
        purpose="PHONE_CHANGE",
        auth_version=3,
        expires_at=datetime.now(UTC),
        verified_at=datetime.now(UTC),
        proof_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    if mismatch == "actor":
        proof.user_id = uuid4()
    elif mismatch == "purpose":
        proof.purpose = "ACCOUNT_REQUEST"
    elif mismatch == "version":
        proof.auth_version = 2
    elif mismatch == "expired":
        proof.proof_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        proof.consumed_at = datetime.now(UTC)
    service._repo.proof.return_value = proof
    with pytest.raises(UnauthorizedError):
        await service._consume_proof(actor, "proof", "PHONE_CHANGE")


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["deadline", "newer_admin_change", "wrong_status", "restored"])
async def test_restore_fails_closed_after_deadline_or_admin_change(
    service: AccountChangeService, cause: str
) -> None:
    actor = user()
    actor.status = UserStatus.DELETED
    actor.deleted_at = datetime.now(UTC)
    request = AccountRequest(
        id=uuid4(),
        user_id=actor.id,
        kind="DELETE_ACCOUNT",
        status="RECOVERABLE",
        prior_status="ACTIVE",
        disabled_auth_version=3,
        restore_deadline=datetime.now(UTC) + timedelta(days=14),
    )
    if cause == "deadline":
        request.restore_deadline = datetime.now(UTC)
    elif cause == "newer_admin_change":
        actor.auth_version = 4
    elif cause == "wrong_status":
        actor.status = UserStatus.BANNED
    else:
        request.status = "RESTORED"
    with pytest.raises(UnauthorizedError):
        service._require_recoverable(actor, request)


@pytest.mark.asyncio
async def test_phone_change_revokes_every_credential_and_device(
    service: AccountChangeService,
) -> None:
    actor = user()
    challenge = AccountChangeChallenge(
        id=uuid4(),
        user_id=actor.id,
        phone="+966509876543",
        purpose="NEW_PHONE",
        auth_version=3,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    service._users.get_for_update.return_value = actor
    service._users.get_by_phone.return_value = None
    service._repo.challenge.return_value = challenge
    service._otp.verify_otp.return_value = True
    phone = await service.confirm_phone(actor.id, challenge.id, "123456")
    assert phone == challenge.phone
    service._auth.invalidate_user_credentials.assert_awaited_once()
    service._repo.remove_devices.assert_awaited_once_with(actor.id)
    assert challenge.consumed_at is not None


@pytest.mark.asyncio
async def test_account_request_retains_funds_and_requires_review(
    service: AccountChangeService,
) -> None:
    actor = user()
    service._users.get_for_update.return_value = actor
    service._repo.active_request.return_value = None
    service._repo.proof.return_value = SimpleNamespace(
        user_id=actor.id,
        phone=actor.phone,
        purpose="ACCOUNT_REQUEST",
        auth_version=3,
        verified_at=datetime.now(UTC),
        proof_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        consumed_at=None,
    )
    request, receipt = await service.submit_account_request(
        actor.id, kind="DELETE_ACCOUNT", confirmation="DELETE", token="proof", reason=None
    )
    assert request.status == "RECOVERABLE"
    assert (
        timedelta(days=13, hours=23)
        < request.restore_deadline - datetime.now(UTC)
        <= timedelta(days=14)
    )
    assert receipt
    assert actor.status is UserStatus.DELETED
    service._auth.invalidate_user_credentials.assert_awaited_once()
    assert service._repo.save.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["actor", "purpose", "expired", "replayed", "phone"])
async def test_scoped_challenge_rejected_before_otp_verification(
    service: AccountChangeService, mismatch: str
) -> None:
    owner = user()
    challenge = AccountChangeChallenge(
        id=uuid4(),
        user_id=owner.id,
        phone=owner.phone,
        purpose="ACCOUNT_REQUEST",
        auth_version=owner.auth_version,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    if mismatch == "actor":
        challenge.user_id = uuid4()
    elif mismatch == "purpose":
        challenge.purpose = "RESTORE_ACCOUNT"
    elif mismatch == "expired":
        challenge.expires_at = datetime.now(UTC)
    elif mismatch == "replayed":
        challenge.verified_at = datetime.now(UTC)
    else:
        challenge.phone = "+966508765432"
    service._repo.challenge.return_value = challenge
    with pytest.raises(UnauthorizedError):
        await service._verify_challenge(owner, challenge.id, "123456", purposes={"ACCOUNT_REQUEST"})
    service._otp.verify_otp.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_recovery_phone_gets_same_challenge_contract(
    service: AccountChangeService,
) -> None:
    service._users.get_by_phone.return_value = None
    service._limiter.check.return_value = SimpleNamespace(allowed=True, unavailable=False)
    service._otp.request_otp.return_value = None
    challenge, code = await service.request_restoration("+966508765432")
    assert code is None and challenge.user_id is None and challenge.purpose == "RESTORE_ACCOUNT"
    assert challenge.expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_failed_shared_limiter_never_issues_a_challenge(
    service: AccountChangeService,
) -> None:
    from app.services.account_change_service import AccountChangeUnavailableError

    service._limiter.check.return_value = SimpleNamespace(allowed=False, unavailable=True)
    with pytest.raises(AccountChangeUnavailableError):
        await service._issue_challenge(user(), "+966508765432", "NEW_PHONE")
    service._repo.save.assert_not_awaited()
    service._otp.request_otp.assert_not_awaited()


@pytest.mark.asyncio
async def test_sms_send_starts_after_challenge_and_proof_commit(
    service: AccountChangeService,
) -> None:
    service._limiter.check.return_value = SimpleNamespace(allowed=True, unavailable=False)

    async def send(*args, **kwargs):
        service._repo.commit_challenge.assert_awaited_once()
        raise TimeoutError("fake provider unavailable")

    service._otp.request_otp.side_effect = send
    from app.services.account_change_service import AccountChangeUnavailableError

    with pytest.raises(AccountChangeUnavailableError):
        await service._issue_challenge(user(), "+966508765432", "NEW_PHONE")
    service._repo.save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("known_actor", [True, False])
async def test_recovery_attempt_limits_are_identical_for_dummy_challenges(
    service: AccountChangeService, known_actor: bool
) -> None:
    from app.core.exceptions import RateLimitedError

    row = AccountChangeChallenge(
        id=uuid4(),
        user_id=uuid4() if known_actor else None,
        phone="+966508765432",
        purpose="RESTORE_ACCOUNT",
        auth_version=3 if known_actor else None,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    service._repo.challenge.return_value = row
    service._otp.verify_otp.side_effect = [False] * 5 + [RateLimitedError(60)]
    for _ in range(5):
        with pytest.raises(UnauthorizedError):
            await service.confirm_restoration(row.id, "000000")
    with pytest.raises(RateLimitedError):
        await service.confirm_restoration(row.id, "000000")
    assert service._otp.verify_otp.await_count == 6
    service._users.get_for_update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("known_actor", [True, False])
async def test_restoration_send_quota_does_not_expose_prior_authenticated_activity(
    service: AccountChangeService, known_actor: bool
) -> None:
    from app.core.security import sha256_hex

    phone = "+966508765432"
    service._limiter.check.return_value = SimpleNamespace(allowed=True, unavailable=False)
    service._otp.request_otp.return_value = None
    await service._issue_challenge(user() if known_actor else None, phone, "RESTORE_ACCOUNT")
    service._limiter.check.assert_awaited_once_with(f"account-recovery:{sha256_hex(phone)}")
