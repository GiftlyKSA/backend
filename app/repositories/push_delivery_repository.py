"""Batched device delivery claims and current-state recipient authorization."""

import logging
import random
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    String,
    and_,
    case,
    cast,
    column,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.integrations.push.expo import DeviceSnapshot, ExpoOutcome
from app.models import Conversation, CourierProfile, DeviceToken, Message, Occasion, Order, User
from app.models.enums import OrderStatus, UserRole, UserStatus
from app.models.expo_push_receipt import ExpoPushReceipt
from app.models.notification_models import OccasionReminder
from app.models.push_delivery import PUSH_DELIVERY_DUE_PREDICATE, PushDelivery
from app.schemas.push_event import PushEvent

_logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 8
_DEVICE_PAGE_SIZE = 100
_UUID_PATTERN = "^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"


def _payload_uuid(
    name: Literal["order_id", "conversation_id", "occasion_id"],
) -> ColumnElement[uuid.UUID]:
    value = PushDelivery.payload[name].astext
    return cast(case((value.op("~")(_UUID_PATTERN), value)), UUID(as_uuid=True))


RETRYABLE_ERRORS = {"MessageRateExceeded", "ExpoServerError", "ProviderUnavailable"}
SAFE_ERRORS = RETRYABLE_ERRORS | {
    "DeviceNotRegistered",
    "MessageTooBig",
    "MismatchSenderId",
    "InvalidCredentials",
    "ProviderError",
    "InvalidTicket",
}


def retry_delay(attempts: int, retry_after: float = 0) -> timedelta:
    """Bound retries and honor provider cooldowns without synchronized retries."""
    seconds = max(min(max(retry_after, 0), 86400), min(3600, 2 ** min(attempts, 10)))
    return timedelta(seconds=seconds + random.uniform(0, min(seconds * 0.2, 30)))  # noqa: S311


@dataclass(frozen=True)
class DeliveryClaim:
    """Detached transport input with a fenced database lease."""

    id: uuid.UUID
    lease_id: uuid.UUID
    event: PushEvent
    device: DeviceSnapshot
    attempts: int
    expires_at: datetime
    queued_at: datetime | None = None


class PushDeliveryRepository:
    """Keep preparation, claims and outcomes bounded to one hundred devices."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind a caller-owned transaction."""
        self._session = session
        self.retired_count = 0

    @staticmethod
    def eligibility(now: datetime) -> ColumnElement[bool]:
        """Reauthorize the original recipient against the current domain state."""
        payload = PushDelivery.payload
        courier = exists().where(
            CourierProfile.user_id == User.id, CourierProfile.is_verified.is_(True)
        )
        order = exists().where(
            Order.id == _payload_uuid("order_id"),
            Order.status == OrderStatus.NEW,
            CourierProfile.user_id == User.id,
            CourierProfile.city_of_residence_id == Order.delivery_city_id,
            CourierProfile.is_verified.is_(True),
            User.role == UserRole.COURIER,
        )
        chat = exists().where(
            Conversation.id == _payload_uuid("conversation_id"),
            Message.id == PushDelivery.event_id,
            Message.conversation_id == Conversation.id,
            Message.sender_id != User.id,
            Order.id == Conversation.order_id,
            Order.customer_id == Conversation.customer_id,
            Order.courier_id == Conversation.courier_id,
            or_(
                and_(User.id == Conversation.customer_id, User.role == UserRole.CUSTOMER),
                and_(User.id == Conversation.courier_id, User.role == UserRole.COURIER),
            ),
        )
        reminder = exists().where(
            Occasion.id == _payload_uuid("occasion_id"),
            Occasion.user_id == User.id,
            User.role == UserRole.CUSTOMER,
            Occasion.reminder_enabled.is_(True),
            OccasionReminder.occasion_id == Occasion.id,
            OccasionReminder.version == Occasion.reminder_version,
            OccasionReminder.version
            == cast(
                case(
                    (
                        payload["reminder_version"].astext.op("~")("^[1-9][0-9]{0,9}$"),
                        payload["reminder_version"].astext,
                    )
                ),
                BigInteger,
            ),
            OccasionReminder.scheduled_at > now - timedelta(days=1),
            OccasionReminder.enabled.is_(True),
        )
        return and_(
            DeviceToken.user_id == PushDelivery.user_id,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
            or_(User.role == UserRole.CUSTOMER, and_(User.role == UserRole.COURIER, courier)),
            or_(
                and_(payload["event_type"].astext == "ORDER_AVAILABLE", order),
                and_(payload["event_type"].astext == "CHAT_MESSAGE", chat),
                and_(payload["event_type"].astext == "OCCASION_REMINDER", reminder),
            ),
        )

    async def enqueue(self, tokens: list[str], event: PushEvent, *, now: datetime) -> None:
        """Duplicate source pages never reset a completed or accepted delivery."""
        if not tokens or len(tokens) > _DEVICE_PAGE_SIZE:
            raise ValueError("Push preparation requires 1 to 100 devices.")
        expires_at = now + timedelta(hours=24)
        if event.event_type == "OCCASION_REMINDER":
            scheduled_at = await self._session.scalar(
                select(OccasionReminder.scheduled_at).where(
                    OccasionReminder.occasion_id == event.occasion_id,
                    OccasionReminder.version == event.reminder_version,
                )
            )
            if scheduled_at is None:
                return
            expires_at = min(expires_at, scheduled_at + timedelta(days=1))
        devices = (
            await self._session.execute(
                select(DeviceToken.id, DeviceToken.user_id).where(DeviceToken.token.in_(tokens))
            )
        ).all()
        if devices:
            await self._session.execute(
                insert(PushDelivery)
                .values(
                    [
                        {
                            "event_id": event.event_id,
                            "device_id": row.id,
                            "user_id": row.user_id,
                            "payload": event.wire_data(),
                            "available_at": now,
                            "expires_at": expires_at,
                        }
                        for row in devices
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_push_delivery_event_device")
            )

    async def claim(self, *, now: datetime, limit: int = 100) -> list[DeliveryClaim]:
        """Lease a bounded page, retiring obsolete targets without per-row queries."""
        rows = (
            await self._session.execute(
                select(
                    PushDelivery,
                    DeviceToken.token,
                    DeviceToken.last_seen_at,
                    self.eligibility(now).label("eligible"),
                )
                .outerjoin(DeviceToken, DeviceToken.id == PushDelivery.device_id)
                .outerjoin(User, User.id == DeviceToken.user_id)
                .where(
                    text(f"push_deliveries.{PUSH_DELIVERY_DUE_PREDICATE}"),
                    PushDelivery.available_at <= now,
                    or_(PushDelivery.leased_until.is_(None), PushDelivery.leased_until <= now),
                )
                .order_by(PushDelivery.available_at, PushDelivery.id)
                .limit(min(max(limit, 1), _DEVICE_PAGE_SIZE))
                .with_for_update(of=PushDelivery, skip_locked=True)
            )
        ).all()
        claims = []
        retired = 0
        for row, token, registered_at, eligible in rows:
            if (
                row.expires_at <= now
                or row.attempts >= _MAX_ATTEMPTS
                or not eligible
                or token is None
            ):
                retired += 1
                row.state = "FAILED" if row.attempts >= _MAX_ATTEMPTS else "EXPIRED"
                row.error_code = (
                    "RetryExhausted"
                    if row.attempts >= _MAX_ATTEMPTS
                    else "Expired"
                    if row.expires_at <= now
                    else "Ineligible"
                )
                row.lease_id = None
                row.leased_until = None
                continue
            try:
                event = PushEvent.model_validate(row.payload)
            except ValidationError:
                row.state = "FAILED"
                row.error_code = "InvalidPayload"
                row.lease_id = None
                row.leased_until = None
                retired += 1
                continue
            row.state = "LEASED"
            row.lease_id = uuid.uuid4()
            row.leased_until = now + timedelta(seconds=60)
            claims.append(
                DeliveryClaim(
                    row.id,
                    row.lease_id,
                    event,
                    DeviceSnapshot(row.device_id, token, registered_at),
                    row.attempts,
                    row.expires_at,
                    row.created_at,
                )
            )
        await self._session.flush()
        if retired:
            _logger.info("Push targets retired: count=%d.", retired)
        self.retired_count = retired
        return claims

    async def begin_send(
        self, claims: list[DeliveryClaim], *, now: datetime
    ) -> list[DeliveryClaim]:
        """Recheck current registration and permission, then charge a possible transport attempt."""
        if not claims or len(claims) > _DEVICE_PAGE_SIZE:
            raise ValueError("Push transport requires 1 to 100 claims.")
        batch = values(
            column("id", UUID(as_uuid=True)),
            column("lease_id", UUID(as_uuid=True)),
            column("attempts", Integer),
            column("device_id", UUID(as_uuid=True)),
            column("token", String(512)),
            name="push_claims",
        ).data(
            [
                (
                    c.id,
                    c.lease_id,
                    c.attempts,
                    c.device.device_id,
                    c.device.token,
                )
                for c in claims
            ]
        )
        eligibility = and_(
            DeviceToken.id == batch.c.device_id,
            DeviceToken.token == batch.c.token,
            self.eligibility(now),
        )
        checked = (
            select(
                batch.c.id,
                batch.c.lease_id,
                batch.c.attempts,
                DeviceToken.last_seen_at.label("registered_at"),
                case(
                    (PushDelivery.expires_at <= now, "Expired"),
                    (eligibility, None),
                    else_="Ineligible",
                ).label("error_code"),
            )
            .select_from(batch)
            .join(PushDelivery, PushDelivery.id == batch.c.id)
            .outerjoin(DeviceToken, DeviceToken.id == PushDelivery.device_id)
            .outerjoin(User, User.id == DeviceToken.user_id)
            .subquery()
        )
        ready = checked.c.error_code.is_(None)
        changed = await self._session.execute(
            update(PushDelivery)
            .where(
                PushDelivery.id == checked.c.id,
                PushDelivery.lease_id == checked.c.lease_id,
                PushDelivery.state == "LEASED",
                PushDelivery.leased_until > now,
                PushDelivery.attempts == checked.c.attempts,
                PushDelivery.attempts < _MAX_ATTEMPTS,
            )
            .values(
                state=case((ready, "LEASED"), else_="EXPIRED"),
                attempts=PushDelivery.attempts + case((ready, 1), else_=0),
                error_code=checked.c.error_code,
                lease_id=case((ready, PushDelivery.lease_id)),
                leased_until=case((ready, PushDelivery.leased_until)),
                updated_at=now,
            )
            .returning(PushDelivery.id, PushDelivery.state, checked.c.registered_at)
            .execution_options(synchronize_session=False)
        )
        started = {row.id: row.registered_at for row in changed if row.state == "LEASED"}
        return [
            replace(
                claim,
                attempts=claim.attempts + 1,
                device=replace(claim.device, last_seen_at=started[claim.id]),
            )
            for claim in claims
            if claim.id in started
        ]

    async def defer(
        self,
        claims: list[DeliveryClaim],
        code: Literal["RateDeferred", "CoordinationUnavailable"],
        *,
        now: datetime,
    ) -> None:
        """Release leases when shared admission failed before any attempt began."""
        if len(claims) > _DEVICE_PAGE_SIZE:
            raise ValueError("Push deferral batch exceeds 100.")
        await self._finish_updates(
            [
                (claim, "PENDING", code, min(now + retry_delay(claim.attempts), claim.expires_at))
                for claim in claims
            ],
            now=now,
        )

    async def finish(
        self,
        claims: list[DeliveryClaim],
        outcomes: list[ExpoOutcome],
        *,
        now: datetime,
        retry_after: float = 0,
    ) -> None:
        """Fence one batched outcome write and atomically persist accepted tickets."""
        if len(claims) != len(outcomes) or len(claims) > _DEVICE_PAGE_SIZE:
            raise ValueError("Invalid delivery outcome count.")
        from app.repositories.expo_receipt_repository import ExpoReceiptRepository

        updates = []
        tickets = []
        invalid = []
        for claim, outcome in zip(claims, outcomes, strict=True):
            code = outcome.details.get("error", "ProviderError")
            code = code if code in SAFE_ERRORS else "ProviderError"
            accepted = outcome.status == "ok" and bool(outcome.id)
            retry = not accepted and code in RETRYABLE_ERRORS and claim.attempts < _MAX_ATTEMPTS
            state = "ACCEPTED" if accepted else "PENDING" if retry else "FAILED"
            updates.append(
                (
                    claim,
                    state,
                    None if accepted else code,
                    min(now + retry_delay(claim.attempts, retry_after), claim.expires_at),
                )
            )
        matched = await self._finish_updates(updates, now=now)
        for claim, outcome in zip(claims, outcomes, strict=True):
            if claim.id not in matched:
                continue
            if outcome.status == "ok" and outcome.id:
                tickets.append(
                    {
                        "ticket_id": outcome.id,
                        "delivery_id": claim.id,
                        "device_id": claim.device.device_id,
                        "token": "",
                        "device_last_seen_at": claim.device.last_seen_at,
                        "available_at": now + timedelta(minutes=15),
                        "expires_at": now + timedelta(hours=24),
                    }
                )
            elif outcome.details.get("error") == "DeviceNotRegistered":
                invalid.append(claim.device)
        if tickets:
            await self._session.execute(
                insert(ExpoPushReceipt)
                .values(tickets)
                .on_conflict_do_nothing(index_elements=["ticket_id"])
            )
        await ExpoReceiptRepository(self._session).remove_invalid_batch(invalid)

    async def _finish_updates(
        self, outcomes: list[tuple[DeliveryClaim, str, str | None, datetime]], *, now: datetime
    ) -> set[uuid.UUID]:
        if not outcomes:
            return set()
        batch = values(
            column("id", UUID(as_uuid=True)),
            column("lease_id", UUID(as_uuid=True)),
            column("attempts", Integer),
            column("state", String(24)),
            column("error_code", String(64)),
            column("available_at", DateTime(timezone=True)),
            name="delivery_outcomes",
        ).data(
            [(c.id, c.lease_id, c.attempts, state, code, due) for c, state, code, due in outcomes]
        )
        return set(
            await self._session.scalars(
                update(PushDelivery)
                .where(
                    PushDelivery.id == batch.c.id,
                    PushDelivery.lease_id == batch.c.lease_id,
                    PushDelivery.attempts == batch.c.attempts,
                    PushDelivery.state == "LEASED",
                    PushDelivery.leased_until > now,
                )
                .values(
                    state=batch.c.state,
                    error_code=batch.c.error_code,
                    available_at=batch.c.available_at,
                    lease_id=None,
                    leased_until=None,
                    updated_at=now,
                )
                .returning(PushDelivery.id)
                .execution_options(synchronize_session=False)
            )
        )

    async def resolve_receipts(
        self, outcomes: list[tuple[uuid.UUID, str | None]], *, now: datetime
    ) -> None:
        """Final transient platform errors schedule a new send, never the same receipt."""
        if not outcomes:
            return
        batch = values(
            column("id", UUID(as_uuid=True)),
            column("code", String(64)),
            name="platform_outcomes",
        ).data(outcomes)
        retry = and_(
            batch.c.code.in_(RETRYABLE_ERRORS),
            PushDelivery.attempts < _MAX_ATTEMPTS,
            PushDelivery.expires_at > now,
        )
        delay = func.least(3600, func.power(2, PushDelivery.attempts))
        available_at = literal(now) + (
            delay + func.random() * func.least(delay * 0.2, 30)
        ) * literal(timedelta(seconds=1))
        await self._session.execute(
            update(PushDelivery)
            .where(
                PushDelivery.id == batch.c.id,
                PushDelivery.state == "ACCEPTED",
            )
            .values(
                state=case(
                    (batch.c.code.is_(None), "PLATFORM_ACCEPTED"),
                    (retry, "PENDING"),
                    else_="FAILED",
                ),
                error_code=batch.c.code,
                available_at=available_at,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
