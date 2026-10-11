"""Owner-bound account changes and a separate, restricted recovery entry point."""

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_redis, get_settings, require_auth
from app.core.ratelimit import RateLimiter
from app.models.account_change import AccountRequest
from app.repositories.account_change_repository import AccountChangeRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.repositories.user_repository import UserRepository
from app.schemas.account_change import (
    AccountRequestCreate,
    AccountRequestResponse,
    ChallengeConfirm,
    ChallengeResponse,
    PhoneChangeRequest,
    PhoneChangeResponse,
    ReauthenticationRequest,
    ReauthenticationResponse,
    RestorationRequest,
)
from app.services.account_change_service import AccountChangeService
from app.services.otp_service import OtpService


def _private_response(response: Response) -> None:
    """Prevent intermediaries or browsers from retaining sensitive credentials/state."""
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/api/users/me", tags=["account changes"], dependencies=[Depends(_private_response)]
)
DbDep = Annotated[AsyncSession, Depends(get_db)]
ActorDep = Annotated[Actor, Depends(require_auth)]


def _service(request: Request, db: AsyncSession) -> AccountChangeService:
    return AccountChangeService(
        users=UserRepository(db),
        repo=AccountChangeRepository(db),
        auth=AuthRepository(db),
        otp=OtpService(get_redis(request), request.app.state.clients.sms, get_settings(request)),
        audit=AuditRepository(db),
        limiter=RateLimiter(
            get_redis(request),
            max_requests=get_settings(request).OTP_MAX_PER_WINDOW,
            window_seconds=get_settings(request).OTP_WINDOW_SECONDS,
        ),
    )


def _response(row: AccountRequest, receipt: str | None = None) -> AccountRequestResponse:
    status = AccountChangeService.effective_status(row)
    return AccountRequestResponse(
        id=row.id,
        kind=cast(Literal["DELETE_ACCOUNT", "TERMINATE_CONTRACT"], row.kind),
        status=cast(Literal["RECOVERABLE", "PENDING_REVIEW", "RESTORED"], status),
        submitted_at=row.created_at,
        updated_at=row.updated_at,
        restore_deadline=row.restore_deadline,
        receipt=receipt,
    )


@router.post(
    "/reauthentication/request",
    response_model=ChallengeResponse,
    response_model_exclude_none=True,
    status_code=202,
)
async def request_reauthentication(
    request: Request,
    db: DbDep,
    actor: ActorDep,
    body: ReauthenticationRequest | None = None,
) -> ChallengeResponse:
    """Send a current-phone OTP scoped to PHONE_CHANGE or ACCOUNT_REQUEST."""
    row, code = await _service(request, db).request_reauthentication(
        actor.id, body.purpose if body else "PHONE_CHANGE"
    )
    return ChallengeResponse(challenge_id=row.id, otp_dev=int(code) if code else None)


@router.post("/reauthentication/confirm", response_model=ReauthenticationResponse)
async def confirm_reauthentication(
    request: Request, db: DbDep, actor: ActorDep, body: ChallengeConfirm
) -> ReauthenticationResponse:
    """Issue a one-use proof, valid for five minutes for the selected purpose."""
    token = await _service(request, db).confirm_reauthentication(
        actor.id, body.challenge_id, body.otp
    )
    return ReauthenticationResponse(reauthentication_token=token)


@router.post(
    "/phone-change/request",
    response_model=ChallengeResponse,
    response_model_exclude_none=True,
    status_code=202,
)
async def request_phone_change(
    request: Request, db: DbDep, actor: ActorDep, body: PhoneChangeRequest
) -> ChallengeResponse:
    """Consume a PHONE_CHANGE proof and verify the new phone through a separate OTP."""
    row, code = await _service(request, db).request_phone(
        actor.id, body.new_phone, body.reauthentication_token
    )
    return ChallengeResponse(challenge_id=row.id, otp_dev=int(code) if code else None)


@router.post("/phone-change/confirm", response_model=PhoneChangeResponse)
async def confirm_phone_change(
    request: Request, db: DbDep, actor: ActorDep, body: ChallengeConfirm
) -> PhoneChangeResponse:
    """Change the phone and revoke every access, refresh, admin and push credential."""
    return PhoneChangeResponse(
        phone=await _service(request, db).confirm_phone(actor.id, body.challenge_id, body.otp)
    )


@router.post(
    "/account-request",
    response_model=AccountRequestResponse,
    response_model_exclude_none=True,
    status_code=202,
)
async def submit_account_request(
    request: Request, db: DbDep, actor: ActorDep, body: AccountRequestCreate
) -> AccountRequestResponse:
    """Disable access for a 14-day recovery window before administrative review."""
    row, receipt = await _service(request, db).submit_account_request(
        actor.id,
        kind=body.kind,
        confirmation=body.confirmation,
        token=body.reauthentication_token,
        reason=body.reason,
    )
    return _response(row, receipt)


@router.get(
    "/account-request", response_model=AccountRequestResponse, response_model_exclude_none=True
)
async def account_request_status(
    request: Request,
    db: DbDep,
    receipt: Annotated[
        str | None, Header(alias="X-Account-Request-Receipt", min_length=32, max_length=200)
    ] = None,
) -> AccountRequestResponse:
    """Read only own request state using normal auth or the separate restricted receipt."""
    service = _service(request, db)
    if receipt is not None:
        row = await service.get_account_request(receipt=receipt)
    else:
        actor = await require_auth(request, db)
        row = await service.get_account_request(actor_id=actor.id)
    return _response(row)


@router.post(
    "/account-request/restoration/request",
    response_model=ChallengeResponse,
    response_model_exclude_none=True,
    status_code=202,
)
async def request_restoration(
    request: Request, db: DbDep, body: RestorationRequest
) -> ChallengeResponse:
    """Request a scoped recovery OTP without exposing account existence or status."""
    row, code = await _service(request, db).request_restoration(body.phone)
    return ChallengeResponse(challenge_id=row.id, otp_dev=int(code) if code else None)


@router.post(
    "/account-request/restoration/confirm",
    response_model=AccountRequestResponse,
    response_model_exclude_none=True,
)
async def confirm_restoration(
    request: Request, db: DbDep, body: ChallengeConfirm
) -> AccountRequestResponse:
    """Restore within the deadline after scoped OTP proof; sign in separately afterward."""
    row = await _service(request, db).confirm_restoration(body.challenge_id, body.otp)
    request.state.audit_actor_id = row.user_id
    request.state.audit_actor_category = "ACCOUNT_RECOVERY"
    return _response(row)
