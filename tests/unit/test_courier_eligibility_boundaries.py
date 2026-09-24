"""Fast service-boundary tests for rejected and banned courier accounts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.core.exceptions import ConflictError, ForbiddenError
from app.models.enums import OrderStatus, UserRole, UserStatus
from app.services.admin_service import AdminService
from app.services.chat_service import ChatService
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.invoice_service import InvoiceService, NewInvoiceInput
from app.services.order_service import OrderService
from app.services.rating_service import RatingService
from app.services.user_service import UserService
from app.services.withdrawal_service import WithdrawalService


class _RejectedUsers:
    def __init__(self) -> None:
        self.id = uuid.uuid4()

    async def get(self, _user_id: uuid.UUID) -> object:
        return SimpleNamespace(id=self.id, role=UserRole.COURIER, status=UserStatus.REJECTED)


class _VerifiedCourier:
    async def get(self, _user_id: uuid.UUID) -> object:
        return SimpleNamespace(is_verified=True, city_of_residence="Jeddah")


class _OwnedOrder:
    async def get_for_actor(self, _order_id: uuid.UUID, _actor_id: uuid.UUID) -> object:
        return SimpleNamespace(id=_order_id)


async def test_rejected_courier_cannot_read_owned_order_detail() -> None:
    """Removing the shared eligibility gate must expose an assigned order and fail."""
    users = _RejectedUsers()
    eligibility = CourierEligibilityService(
        users=users,  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
    )
    service = OrderService(
        session=Any,
        orders=_OwnedOrder(),  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        eligibility=eligibility,
        media=Any,
        messages=Any,
        ratings=Any,
        redis=Any,
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.get_order_for_actor(order_id=uuid.uuid4(), actor_id=uuid.uuid4())


async def test_rejected_courier_cannot_cancel_owned_order() -> None:
    """Cancellation must apply eligibility before loading or mutating the order."""
    eligibility = _rejected_eligibility()
    service = OrderService(
        session=Any,
        orders=_OwnedOrder(),  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        eligibility=eligibility,
        media=Any,
        messages=Any,
        ratings=Any,
        redis=Any,
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.cancel_order(order_id=uuid.uuid4(), actor_id=uuid.uuid4(), reason=None)


async def test_rejected_courier_cannot_enrich_order_coordinates() -> None:
    """Detail enrichment must deny before any exact-coordinate query can run."""
    eligibility = _rejected_eligibility()
    service = OrderService(
        session=Any,
        orders=_OwnedOrder(),  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        eligibility=eligibility,
        media=Any,
        messages=Any,
        ratings=Any,
        redis=Any,
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.get_order_view_for_actor(
            order_id=uuid.uuid4(), actor_id=uuid.uuid4(), role=UserRole.COURIER
        )


class _DecisionUsers:
    def __init__(self, status: UserStatus) -> None:
        self.user = SimpleNamespace(role=UserRole.COURIER, status=status)

    async def get(self, _user_id: uuid.UUID) -> object:
        return self.user

    async def get_for_update(self, _user_id: uuid.UUID) -> object:
        return self.user

    async def set_status(self, _user: object, status: UserStatus) -> None:
        self.user.status = status


class _CourierDecision:
    def __init__(self) -> None:
        self.profile = SimpleNamespace(is_verified=False)

    async def get(self, _user_id: uuid.UUID) -> object:
        return self.profile

    async def set_verified(self, profile: object, **changes: object) -> None:
        profile.is_verified = changes["is_verified"]


class _AuditCapture:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    async def record(self, **values: object) -> None:
        self.rows.append(values)


async def test_admin_rejection_cannot_change_banned_courier_status() -> None:
    """A verification decision must never restore access to a banned account."""
    users = _DecisionUsers(UserStatus.BANNED)
    audit = _AuditCapture()
    service = AdminService(
        reads=Any,
        users=users,  # type: ignore[arg-type]
        couriers=_CourierDecision(),  # type: ignore[arg-type]
        orders=Any,
        promos=Any,
        audit=audit,  # type: ignore[arg-type]
        auth_repo=Any,
        redis=Any,
        settings=Any,
    )

    with pytest.raises(ConflictError):
        await service.verify_courier(
            admin_id=uuid.uuid4(),
            courier_user_id=uuid.uuid4(),
            approve=False,
            note="private rejection detail",
            ip="127.0.0.1",
        )

    assert users.user.status is UserStatus.BANNED
    assert audit.rows == []


async def test_admin_audit_omits_private_rejection_detail() -> None:
    """The audit event identifies the decision without duplicating its private reason."""
    users = _DecisionUsers(UserStatus.PENDING_VERIFICATION)
    audit = _AuditCapture()
    service = AdminService(
        reads=Any,
        users=users,  # type: ignore[arg-type]
        couriers=_CourierDecision(),  # type: ignore[arg-type]
        orders=Any,
        promos=Any,
        audit=audit,  # type: ignore[arg-type]
        auth_repo=Any,
        redis=Any,
        settings=Any,
    )

    await service.verify_courier(
        admin_id=uuid.uuid4(),
        courier_user_id=uuid.uuid4(),
        approve=False,
        note="private rejection detail",
        ip="127.0.0.1",
    )

    assert len(audit.rows) == 1
    assert audit.rows[0]["metadata"] is None


class _ConcurrentVerificationUsers:
    def __init__(self) -> None:
        self.stale = SimpleNamespace(
            id=uuid.uuid4(), role=UserRole.COURIER, status=UserStatus.PENDING_VERIFICATION
        )
        self.locked = SimpleNamespace(
            id=self.stale.id, role=UserRole.COURIER, status=UserStatus.BANNED
        )

    async def get(self, _user_id: uuid.UUID) -> object:
        return self.stale

    async def get_for_update(self, _user_id: uuid.UUID) -> object:
        return self.locked

    async def set_status(self, user: object, status: UserStatus) -> None:
        user.status = status  # type: ignore[attr-defined]


async def test_admin_verification_checks_status_on_locked_user_row() -> None:
    """A concurrent ban observed under lock must not be overwritten by rejection."""
    users = _ConcurrentVerificationUsers()
    service = AdminService(
        reads=Any,
        users=users,  # type: ignore[arg-type]
        couriers=_CourierDecision(),  # type: ignore[arg-type]
        orders=Any,
        promos=Any,
        audit=_AuditCapture(),  # type: ignore[arg-type]
        auth_repo=Any,
        redis=Any,
        settings=Any,
    )

    with pytest.raises(ConflictError):
        await service.verify_courier(
            admin_id=uuid.uuid4(),
            courier_user_id=users.locked.id,
            approve=False,
            note="private rejection detail",
            ip=None,
        )

    assert users.locked.status is UserStatus.BANNED


class _LockRequiredUsers:
    def __init__(self) -> None:
        self.stale = SimpleNamespace(id=uuid.uuid4(), status=UserStatus.ACTIVE)
        self.locked = SimpleNamespace(id=self.stale.id, status=UserStatus.ACTIVE)

    async def get(self, _user_id: uuid.UUID) -> object:
        return self.stale

    async def get_for_update(self, _user_id: uuid.UUID) -> object:
        return self.locked

    async def set_status(self, user: object, status: UserStatus) -> None:
        if user is not self.locked:
            raise AssertionError("status transition did not use the locked row")
        user.status = status  # type: ignore[attr-defined]


class _NoopAuth:
    def __init__(self) -> None:
        self.invalidated: list[uuid.UUID] = []
        self.revoked: list[uuid.UUID] = []

    async def invalidate_user_credentials(self, user_id: uuid.UUID, _now: datetime) -> None:
        self.invalidated.append(user_id)

    async def revoke_all_for_user(self, user_id: uuid.UUID, _now: datetime) -> None:
        self.revoked.append(user_id)


class _NoopRedis:
    async def set(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def delete(self, *_args: object) -> None:
        return None


async def test_admin_ban_transition_uses_locked_user_row() -> None:
    """Ban and verification must serialize through the same user-row lock."""
    users = _LockRequiredUsers()
    auth = _NoopAuth()
    service = AdminService(
        reads=Any,
        users=users,  # type: ignore[arg-type]
        couriers=Any,
        orders=Any,
        promos=Any,
        audit=_AuditCapture(),  # type: ignore[arg-type]
        auth_repo=auth,  # type: ignore[arg-type]
        redis=_NoopRedis(),  # type: ignore[arg-type]
        settings=SimpleNamespace(JWT_ACCESS_TTL_MINUTES=30),
    )

    await service.set_user_banned(
        admin_id=uuid.uuid4(), user_id=users.locked.id, banned=True, ip=None
    )

    assert users.locked.status is UserStatus.BANNED
    assert auth.invalidated == [users.locked.id]
    assert auth.revoked == []


class _AssignedOrder:
    async def get_for_actor(self, _order_id: uuid.UUID, _actor_id: uuid.UUID) -> object:
        return SimpleNamespace(status=OrderStatus.ASSIGNED, customer_id=uuid.uuid4())


class _EmptyInvoices:
    async def get_active_for_order(self, _order_id: uuid.UUID) -> None:
        return None

    async def lock_for_courier(self, _invoice_id: uuid.UUID, _courier_id: uuid.UUID) -> None:
        return None


class _NoWithdrawal:
    async def get_by_idempotency(self, **_values: object) -> None:
        return None


class _NoWallet:
    async def get_by_user(self, _user_id: uuid.UUID) -> None:
        return None


def _rejected_eligibility() -> CourierEligibilityService:
    return CourierEligibilityService(
        users=_RejectedUsers(),  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
    )


async def test_rejected_courier_cannot_create_invoice() -> None:
    """Invoice creation must stop at eligibility before order or pricing work."""
    service = InvoiceService(
        reservations=Any,
        invoices=_EmptyInvoices(),  # type: ignore[arg-type]
        orders=_AssignedOrder(),  # type: ignore[arg-type]
        promos=Any,
        eligibility=_rejected_eligibility(),
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.create_invoice(
            order_id=uuid.uuid4(),
            courier_id=uuid.uuid4(),
            data=NewInvoiceInput(items=[], courier_fee_amount=Decimal("0.00"), promo_code=None),
        )


async def test_rejected_courier_cannot_cancel_invoice() -> None:
    """Invoice cancellation must stop at eligibility before invoice lookup."""
    service = InvoiceService(
        reservations=Any,
        invoices=_EmptyInvoices(),  # type: ignore[arg-type]
        orders=_AssignedOrder(),  # type: ignore[arg-type]
        promos=Any,
        eligibility=_rejected_eligibility(),
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.cancel_invoice(invoice_id=uuid.uuid4(), courier_id=uuid.uuid4())


async def test_rejected_courier_cannot_create_withdrawal() -> None:
    """Withdrawal creation must stop at eligibility before wallet or encryption work."""
    service = WithdrawalService(
        withdrawals=_NoWithdrawal(),  # type: ignore[arg-type]
        wallets=_NoWallet(),  # type: ignore[arg-type]
        money=Any,
        audit=Any,
        eligibility=_rejected_eligibility(),
        settings=Any,
    )

    with pytest.raises(ForbiddenError):
        await service.request_withdrawal(
            courier_id=uuid.uuid4(),
            amount=Decimal("100.00"),
            iban="SA0380000000608010167519",
            idempotency_key="one",
        )


class _BatchRatings:
    def __init__(self, rated_id: uuid.UUID) -> None:
        self.rated_id = rated_id

    async def rated_order_ids_for_actor(
        self, order_ids: list[uuid.UUID], _actor_id: uuid.UUID
    ) -> set[uuid.UUID]:
        return {self.rated_id} & set(order_ids)


class _CustomerUsers:
    async def get(self, user_id: uuid.UUID) -> object:
        return SimpleNamespace(id=user_id, role=UserRole.CUSTOMER, status=UserStatus.ACTIVE)


class _ListedOrders:
    def __init__(self, order_ids: list[uuid.UUID]) -> None:
        self.orders = [SimpleNamespace(id=order_id) for order_id in order_ids]

    async def list_for_customer(self, *_args: object, **_kwargs: object) -> list[object]:
        return self.orders


async def test_rating_states_are_assembled_from_one_batch_contract() -> None:
    """List enrichment maps one batch result without sequential per-order checks."""
    unrated_id, rated_id = uuid.uuid4(), uuid.uuid4()
    service = RatingService(
        orders=Any,
        ratings=_BatchRatings(rated_id),  # type: ignore[arg-type]
        eligibility=CourierEligibilityService(
            users=_CustomerUsers(),  # type: ignore[arg-type]
            couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        ),
    )
    assert hasattr(service, "current_actor_rating_states")

    states = await service.current_actor_rating_states([unrated_id, rated_id], uuid.uuid4())

    assert states == {unrated_id: False, rated_id: True}


async def test_order_list_enrichment_uses_batch_rating_states() -> None:
    """Order list assembly consumes the batch map instead of per-row rating queries."""
    actor_id = uuid.uuid4()
    unrated_id, rated_id = uuid.uuid4(), uuid.uuid4()
    rating_service = RatingService(
        orders=Any,
        ratings=_BatchRatings(rated_id),  # type: ignore[arg-type]
        eligibility=CourierEligibilityService(
            users=_CustomerUsers(),  # type: ignore[arg-type]
            couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        ),
    )
    service = OrderService(
        session=Any,
        orders=_ListedOrders([unrated_id, rated_id]),  # type: ignore[arg-type]
        couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        eligibility=CourierEligibilityService(
            users=_CustomerUsers(),  # type: ignore[arg-type]
            couriers=_VerifiedCourier(),  # type: ignore[arg-type]
        ),
        media=Any,
        messages=Any,
        ratings=rating_service,
        redis=Any,
        settings=Any,
    )

    views = await service.list_views_for_actor(
        actor_id=actor_id,
        role=UserRole.CUSTOMER,
        status=None,
        limit=20,
        before_id=None,
    )

    assert [(view.order.id, view.current_actor_has_rated) for view in views] == [
        (unrated_id, False),
        (rated_id, True),
    ]


class _ParticipantProjection:
    async def get_participant_for_actor(
        self, _actor_id: uuid.UUID, _participant_id: uuid.UUID
    ) -> object:
        return SimpleNamespace(
            id=uuid.uuid4(),
            full_name="Sensitive Participant",
            role=UserRole.CUSTOMER,
            rating=Decimal("5.00"),
            rating_count=0,
            courier_city=None,
            courier_bio=None,
        )


async def test_rejected_courier_cannot_read_participant_profile() -> None:
    """Participant projection must remain hidden after courier rejection."""
    service = UserService(
        users=_ParticipantProjection(),  # type: ignore[arg-type]
        audit=Any,
        eligibility=_rejected_eligibility(),
    )

    with pytest.raises(ForbiddenError):
        await service.get_participant(actor_id=uuid.uuid4(), participant_id=uuid.uuid4())


class _CompletedOrder:
    async def get_for_actor(self, _order_id: uuid.UUID, _actor_id: uuid.UUID) -> object:
        return SimpleNamespace(
            status=OrderStatus.COMPLETED,
            customer_id=uuid.uuid4(),
            courier_id=uuid.uuid4(),
        )


async def test_rejected_courier_cannot_create_rating() -> None:
    """Rating creation must stop before loading a completed participant order."""
    service = RatingService(
        orders=_CompletedOrder(),  # type: ignore[arg-type]
        ratings=Any,
        eligibility=_rejected_eligibility(),
    )

    with pytest.raises(ForbiddenError):
        await service.rate(
            order_id=uuid.uuid4(),
            rater_id=uuid.uuid4(),
            score=5,
            comment=None,
        )


class _VisibleChat:
    async def get_for_actor(self, conversation_id: uuid.UUID, actor_id: uuid.UUID) -> object:
        return SimpleNamespace(
            id=conversation_id,
            customer_id=actor_id,
            courier_id=uuid.uuid4(),
        )

    async def list_for_user(self, *_args: object, **_kwargs: object) -> list[object]:
        return []

    async def list_messages(self, *_args: object, **_kwargs: object) -> list[object]:
        return []


def _rejected_chat() -> ChatService:
    return ChatService(
        chat=_VisibleChat(),  # type: ignore[arg-type]
        redis=Any,
        settings=Any,
        eligibility=_rejected_eligibility(),
    )


async def test_rejected_courier_cannot_list_chat_inbox() -> None:
    """Inbox listing must not disclose participant activity after rejection."""
    with pytest.raises(ForbiddenError):
        await _rejected_chat().list_inbox(user_id=uuid.uuid4(), limit=20, before=None)


async def test_rejected_courier_cannot_read_chat_messages() -> None:
    """Message reads must stop before conversation or ciphertext access."""
    with pytest.raises(ForbiddenError):
        await _rejected_chat().list_messages(
            conversation_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            limit=20,
            before_id=None,
        )


async def test_rejected_courier_cannot_send_chat_message() -> None:
    """Message sends must stop before conversation mutation or encryption."""
    with pytest.raises(ForbiddenError):
        await _rejected_chat().send_message(
            conversation_id=uuid.uuid4(), sender_id=uuid.uuid4(), text="still here"
        )


async def test_rejected_courier_cannot_mark_chat_read() -> None:
    """Read-state mutation must stop before loading the conversation."""
    with pytest.raises(ForbiddenError):
        await _rejected_chat().mark_read(conversation_id=uuid.uuid4(), actor_id=uuid.uuid4())


async def test_rejected_courier_cannot_open_websocket_conversation() -> None:
    """WebSocket admission must use the same eligibility-aware conversation read."""
    with pytest.raises(ForbiddenError):
        await _rejected_chat().get_conversation_for_actor(
            conversation_id=uuid.uuid4(), actor_id=uuid.uuid4()
        )
