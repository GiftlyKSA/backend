"""Fast service-boundary tests for rejected and banned courier accounts."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.core.exceptions import ConflictError, ForbiddenError
from app.models.enums import OrderStatus, UserRole, UserStatus
from app.services.admin_service import AdminService
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.invoice_service import InvoiceService, NewInvoiceInput
from app.services.order_service import OrderService
from app.services.rating_service import RatingService
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
    service = RatingService(orders=Any, ratings=_BatchRatings(rated_id))  # type: ignore[arg-type]
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
