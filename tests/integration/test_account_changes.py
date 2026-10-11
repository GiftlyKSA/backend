"""PostgreSQL persistence, financial retention and exact account recovery fences."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.ratelimit import RateLimitDecision
from app.core.security import sha256_hex
from app.models import DeviceToken, RefreshToken, User, Wallet
from app.models.account_change import AccountChangeChallenge, AccountRequestEmail
from app.models.enums import DeviceOs, UserRole, UserStatus, WalletType
from app.repositories.account_change_repository import AccountChangeRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.repositories.user_repository import UserRepository
from app.services.account_change_service import AccountChangeService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def service(session: AsyncSession) -> AccountChangeService:
    limiter = AsyncMock()
    limiter.check.return_value = RateLimitDecision(True, 0)
    otp = AsyncMock()
    otp.verify_otp.return_value = True
    otp.request_otp.return_value = None
    return AccountChangeService(
        users=UserRepository(session),
        repo=AccountChangeRepository(session),
        auth=AuthRepository(session),
        audit=AuditRepository(session),
        otp=otp,
        limiter=limiter,
    )


async def seed_user(
    session: AsyncSession,
    *,
    role: UserRole = UserRole.CUSTOMER,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    user = User(
        id=uuid4(),
        phone=f"+9665{uuid4().int % 100_000_000:08d}",
        role=role,
        status=status,
        email=f"owner-{uuid4()}@example.test",
    )
    session.add(user)
    await session.flush()
    return user


async def proof(session: AsyncSession, owner: User, purpose: str) -> str:
    token = f"proof-{uuid4()}"
    session.add(
        AccountChangeChallenge(
            id=uuid4(),
            user_id=owner.id,
            phone=owner.phone,
            purpose=purpose,
            auth_version=owner.auth_version,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
            verified_at=datetime.now(UTC),
            proof_hash=sha256_hex(token),
            proof_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.flush()
    return token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,status,kind,confirmation",
    [
        (UserRole.CUSTOMER, UserStatus.ACTIVE, "DELETE_ACCOUNT", "DELETE"),
        (UserRole.COURIER, UserStatus.PENDING_VERIFICATION, "TERMINATE_CONTRACT", "TERMINATE"),
        (UserRole.COURIER, UserStatus.REJECTED, "TERMINATE_CONTRACT", "TERMINATE"),
    ],
)
async def test_account_request_and_otp_restoration_preserve_wallet_and_role(
    db_session: AsyncSession, role: UserRole, status: UserStatus, kind: str, confirmation: str
) -> None:
    owner = await seed_user(db_session, role=role, status=status)
    wallet = Wallet(
        user_id=owner.id,
        type=WalletType.CUSTOMER if role is UserRole.CUSTOMER else WalletType.COURIER,
    )
    db_session.add(wallet)
    db_session.add(DeviceToken(user_id=owner.id, token=f"device-{uuid4()}", device_os=DeviceOs.IOS))
    refresh = RefreshToken(
        user_id=owner.id,
        token_hash=sha256_hex(str(uuid4())),
        family_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(refresh)
    await db_session.flush()
    token = await proof(db_session, owner, "ACCOUNT_REQUEST")
    workflow = service(db_session)
    request, receipt = await workflow.submit_account_request(
        owner.id, kind=kind, confirmation=confirmation, token=token, reason="Requested review"
    )
    assert owner.auth_version == request.disabled_auth_version == 1
    assert owner.status is UserStatus.DELETED
    assert (
        await db_session.scalar(
            select(RefreshToken.revoked_at).where(RefreshToken.id == refresh.id)
        )
        is not None
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(DeviceToken).where(DeviceToken.user_id == owner.id)
        )
        == 0
    )
    assert await db_session.get(Wallet, wallet.id) is wallet
    email = await db_session.get(AccountRequestEmail, request.id)
    assert email is not None and "support call" in email.body and "refund money" in email.body
    assert (await workflow.get_account_request(receipt=receipt)).id == request.id
    challenge, _ = await workflow.request_restoration(owner.phone)
    assert challenge.purpose == "RESTORE_ACCOUNT"
    restored = await workflow.confirm_restoration(challenge.id, "123456")
    assert restored.status == "RESTORED"
    assert owner.status is status and owner.deleted_at is None and owner.auth_version == 2
    with pytest.raises(UnauthorizedError):
        await workflow.confirm_restoration(challenge.id, "123456")


@pytest.mark.asyncio
async def test_phone_unique_constraint_keeps_both_account_identities(
    db_session: AsyncSession,
) -> None:
    first = await seed_user(db_session)
    second = await seed_user(db_session)
    original = first.phone
    workflow = service(db_session)
    token = await proof(db_session, first, "PHONE_CHANGE")
    challenge, _ = await workflow.request_phone(first.id, second.phone, token)
    with pytest.raises(ConflictError):
        await workflow.confirm_phone(first.id, challenge.id, "123456")
    assert first.phone == original and first.auth_version == 0
    assert second.phone == challenge.phone


@pytest.mark.asyncio
async def test_proof_consumption_and_phone_change_invalidate_other_pending_challenges(
    db_session: AsyncSession,
) -> None:
    owner = await seed_user(db_session)
    workflow = service(db_session)
    token = await proof(db_session, owner, "PHONE_CHANGE")
    challenge, _ = await workflow.request_phone(owner.id, "+966508765432", token)
    with pytest.raises(UnauthorizedError):
        await workflow.request_phone(owner.id, "+966508765431", token)
    old_auth_version = owner.auth_version
    assert await workflow.confirm_phone(owner.id, challenge.id, "123456") == "+966508765432"
    assert owner.auth_version == old_auth_version + 1
    with pytest.raises(UnauthorizedError):
        await workflow.confirm_phone(owner.id, challenge.id, "123456")


@pytest.mark.asyncio
async def test_review_sweep_preserves_disabled_account_and_funds(db_session: AsyncSession) -> None:
    owner = await seed_user(db_session)
    workflow = service(db_session)
    token = await proof(db_session, owner, "ACCOUNT_REQUEST")
    request, _ = await workflow.submit_account_request(
        owner.id, kind="DELETE_ACCOUNT", confirmation="DELETE", token=token, reason=None
    )
    request.restore_deadline = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    assert await workflow._repo.advance_review(datetime.now(UTC)) == 1
    assert request.status == "PENDING_REVIEW" and owner.status is UserStatus.DELETED
    with pytest.raises(UnauthorizedError):
        workflow._require_recoverable(owner, request)


@pytest.mark.asyncio
async def test_email_lease_fences_ack_and_stops_after_five_attempts(
    db_session: AsyncSession,
) -> None:
    owner = await seed_user(db_session)
    workflow = service(db_session)
    token = await proof(db_session, owner, "ACCOUNT_REQUEST")
    request, _ = await workflow.submit_account_request(
        owner.id, kind="DELETE_ACCOUNT", confirmation="DELETE", token=token, reason=None
    )
    repo = workflow._repo
    now = datetime.now(UTC)
    email = await repo.claim_email(now)
    assert email is not None and email.lease_id is not None
    lease = email.lease_id
    await repo.finish_email(request.id, uuid4(), now=now, sent=True, attempts=email.attempts)
    assert email.sent_at is None
    await repo.finish_email(request.id, lease, now=now, sent=False, attempts=5)
    assert email.failed_at == now and email.sent_at is None
    assert await repo.claim_email(now + timedelta(hours=2)) is None


@pytest.mark.asyncio
async def test_expired_challenge_cleanup_retains_active_and_unexpired_proofs(
    db_session: AsyncSession,
) -> None:
    owner = await seed_user(db_session)
    now = datetime.now(UTC)
    expired = AccountChangeChallenge(
        id=uuid4(),
        user_id=owner.id,
        phone=owner.phone,
        purpose="PHONE_CHANGE",
        auth_version=0,
        expires_at=now - timedelta(seconds=1),
    )
    active = AccountChangeChallenge(
        id=uuid4(),
        user_id=owner.id,
        phone=owner.phone,
        purpose="PHONE_CHANGE",
        auth_version=0,
        expires_at=now + timedelta(seconds=60),
    )
    usable_proof = AccountChangeChallenge(
        id=uuid4(),
        user_id=owner.id,
        phone=owner.phone,
        purpose="PHONE_CHANGE",
        auth_version=0,
        expires_at=now - timedelta(seconds=1),
        verified_at=now - timedelta(seconds=10),
        proof_hash=sha256_hex(str(uuid4())),
        proof_expires_at=now + timedelta(seconds=300),
    )
    db_session.add_all([expired, active, usable_proof])
    await db_session.flush()
    repo = AccountChangeRepository(db_session)
    assert await repo.purge_expired_challenges(now, limit=1000) == 1
    remaining = set(
        await db_session.scalars(
            select(AccountChangeChallenge.id).where(AccountChangeChallenge.user_id == owner.id)
        )
    )
    assert remaining == {active.id, usable_proof.id}


@pytest.mark.asyncio
async def test_full_current_and_new_phone_proof_flow_with_real_redis(
    db_session: AsyncSession,
) -> None:
    from app.core.jwt import create_access_token, decode_access_token
    from app.core.ratelimit import RateLimiter
    from app.core.redis import build_redis
    from app.services.auth_service import validate_access_claims
    from app.services.otp_service import OtpService

    from tests.integration.test_account_change_races import settings

    active_settings = settings()
    redis = build_redis(active_settings)
    owner = await seed_user(db_session)
    old_phone = owner.phone
    new_phone = f"+9665{uuid4().int % 100_000_000:08d}"
    sms = AsyncMock()
    workflow = AccountChangeService(
        users=UserRepository(db_session),
        repo=AccountChangeRepository(db_session),
        auth=AuthRepository(db_session),
        audit=AuditRepository(db_session),
        otp=OtpService(redis, sms, active_settings),
        limiter=RateLimiter(
            redis,
            max_requests=active_settings.OTP_MAX_PER_WINDOW,
            window_seconds=active_settings.OTP_WINDOW_SECONDS,
        ),
    )
    challenges = []
    try:
        token, _, _ = create_access_token(active_settings, user_id=owner.id, role=owner.role.value)
        claims = decode_access_token(active_settings, token)
        current, _ = await workflow.request_reauthentication(owner.id, "PHONE_CHANGE")
        challenges.append((current.id, old_phone))
        proof_token = await workflow.confirm_reauthentication(
            owner.id, current.id, sms.send_otp.await_args.args[1]
        )
        assert len(proof_token) >= 32
        stored = await workflow._repo.proof(sha256_hex(proof_token))
        assert stored is not None and stored.proof_hash != proof_token
        with pytest.raises(UnauthorizedError):
            await workflow._consume_proof(owner, proof_token, "ACCOUNT_REQUEST")
        target, _ = await workflow.request_phone(owner.id, new_phone, proof_token)
        challenges.append((target.id, new_phone))
        assert (
            await workflow.confirm_phone(owner.id, target.id, sms.send_otp.await_args.args[1])
            == new_phone
        )
        with pytest.raises(UnauthorizedError):
            await validate_access_claims(claims, redis=redis, users=UserRepository(db_session))
    finally:
        keys = [f"ratelimit:account-change:{owner.id}"]
        for phone in (old_phone, new_phone):
            keys.extend([f"otp:cooldown:{phone}", f"otp:rate:{phone}", f"otp:block:{phone}"])
        for challenge_id, phone in challenges:
            keys.extend(
                [
                    f"otp:code:account-change:{challenge_id}:{phone}",
                    f"otp:attempts:account-change:{challenge_id}:{phone}",
                ]
            )
        await redis.delete(*keys)
        await redis.aclose()
