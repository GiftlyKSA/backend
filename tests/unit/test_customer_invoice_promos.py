"""Invoice promo ownership, lifecycle, retry and frozen-policy boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PromoExpiredError,
    ValidationDomainError,
)
from app.core.pricing import PricingConfig, PricingItem, calculate_invoice_totals
from app.main import create_app
from app.models.enums import InvoiceStatus, OrderStatus, PromoDiscountType, UserRole, UserStatus
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.promo_repository import PromoRepository
from app.routers import invoices as invoice_routes
from app.schemas.invoices import ApplyInvoicePromoRequest, PromoValidateRequest
from app.services.invoice_promo_service import InvoicePromoService, stored_pricing_config
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


def context():
    customer_id = uuid4()
    config = PricingConfig(Decimal("0.05"), Decimal("5"), Decimal("500"), Decimal("50000"))
    item = SimpleNamespace(
        title="Gift",
        description=None,
        unit_price_amount=Decimal("500"),
        quantity=1,
        position=1,
    )
    result = calculate_invoice_totals([PricingItem(**vars(item))], Decimal("100"), None, config)
    invoice = SimpleNamespace(
        id=uuid4(),
        order_id=uuid4(),
        issued_by_courier_id=uuid4(),
        status=InvoiceStatus.ISSUED,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        promo_code_snapshot=None,
        promo_id=None,
        pricing_breakdown=result.breakdown,
        courier_fee_amount=result.courier_fee_amount,
        service_fee_amount=result.service_fee_amount,
        items_net_amount=result.items_net_amount,
        total_amount=result.total_amount,
    )
    order = SimpleNamespace(
        id=invoice.order_id,
        customer_id=customer_id,
        status=OrderStatus.WAITING_PAYMENT,
        total_amount=invoice.total_amount,
    )
    invoices, orders, payments, promos, operations, eligibility = [AsyncMock() for _ in range(6)]
    operations.get.return_value = None
    operations.savepoint = Mock(return_value=AsyncMock())
    invoices.lock_for_customer.return_value = invoice
    invoices.get_active_for_order.return_value = invoice
    invoices.list_items.return_value = [item]
    invoices.create_draft.return_value = SimpleNamespace(id=uuid4())
    orders.lock.return_value = order
    payments.get_open_intent_for_invoice.return_value = None
    promo = SimpleNamespace(
        id=uuid4(),
        code="GIFT10",
        discount_type=PromoDiscountType.PERCENT,
        percent_value=Decimal("10"),
        fixed_amount=None,
        max_discount_amount=Decimal("100"),
        min_order_amount=Decimal("0"),
    )
    promos.validate.return_value = SimpleNamespace(promo=promo)
    service = InvoicePromoService(
        invoices=invoices,
        orders=orders,
        payments=payments,
        promos=promos,
        operations=operations,
        eligibility=eligibility,
    )
    return service, invoice, order, customer_id


@pytest.mark.parametrize("code", ["gift10", "GiFt10", " GIFT10 "])
async def test_apply_case_insensitive_preserves_policy_expiry_and_history(code):
    service, invoice, order, customer = context()
    original_total = invoice.total_amount
    await service.apply(invoice_id=invoice.id, customer_id=customer, code=code, key="operation")
    assert invoice.status is InvoiceStatus.CANCELLED
    assert invoice.total_amount == original_total == Decimal("630.00")
    result = service._invoices.create_draft.call_args.kwargs["result"]
    assert result.total_amount == Decimal("570.00")
    assert result.discount_amount == Decimal("60.00")
    assert result.service_fee_amount == Decimal("30.00")
    assert order.status is OrderStatus.WAITING_PAYMENT and order.total_amount == result.total_amount
    assert service._invoices.issue.call_args.kwargs["expires_at"] == invoice.expires_at
    assert service._promos.validate.call_args.kwargs["code"] == "GIFT10"
    service._promos.consume.assert_not_awaited()


@pytest.mark.parametrize("current,requested", [(None, None), ("GIFT10", "gift10")])
async def test_noop_does_not_release_or_reserve(current, requested):
    service, invoice, _, customer = context()
    invoice.promo_code_snapshot = current
    returned, _ = await service.apply(
        invoice_id=invoice.id, customer_id=customer, code=requested, key="k"
    )
    assert returned is invoice
    service._invoices.create_draft.assert_not_awaited()
    service._promos.release.assert_not_awaited()
    service._operations.record.assert_awaited_once()


async def test_remove_returns_original_price_without_reserving():
    service, invoice, order, customer = context()
    invoice.promo_code_snapshot, invoice.promo_id = "GIFT10", uuid4()
    await service.apply(invoice_id=invoice.id, customer_id=customer, code=None, key="remove")
    assert order.total_amount == Decimal("630.00")
    service._promos.reserve.assert_not_awaited()
    assert service._invoices.create_draft.call_args.kwargs["promo_id"] is None


async def test_foreign_invoice_hides_existence():
    service, invoice, _, customer = context()
    service._invoices.lock_for_customer.return_value = None
    with pytest.raises(NotFoundError):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code="GIFT10", key="k")
    service._promos.release.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [
        InvoiceStatus.PAID,
        InvoiceStatus.CANCELLED,
        InvoiceStatus.EXPIRED,
        InvoiceStatus.DRAFT,
        InvoiceStatus.REFUNDED,
    ],
)
async def test_invalid_status_never_reprices(status):
    service, invoice, _, customer = context()
    invoice.status = status
    with pytest.raises(ConflictError):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code="GIFT10", key="k")
    service._invoices.create_draft.assert_not_awaited()


async def test_payment_in_progress_is_not_cancelled_or_repriced():
    service, invoice, _, customer = context()
    service._payments.get_open_intent_for_invoice.return_value = SimpleNamespace(id=uuid4())
    with pytest.raises(ConflictError, match="payment is in progress"):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code="GIFT10", key="k")
    service._promos.release.assert_not_awaited()
    service._payments.mark_expired.assert_not_awaited()


async def test_expiry_while_waiting_for_locks_rejects_noop(monkeypatch):
    from app.services import invoice_promo_service

    service, invoice, _, customer = context()
    clock = Mock()
    clock.now.side_effect = [
        invoice.expires_at - timedelta(seconds=1),
        invoice.expires_at + timedelta(seconds=1),
    ]
    monkeypatch.setattr(invoice_promo_service, "datetime", clock)
    with pytest.raises(ConflictError, match="expired"):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code=None, key="late")
    service._operations.record.assert_not_awaited()


async def test_invalid_replacement_does_not_cancel_original():
    service, invoice, _, customer = context()
    service._promos.validate.side_effect = PromoExpiredError()
    with pytest.raises(PromoExpiredError):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code="GIFT10", key="k")
    assert invoice.status is InvoiceStatus.ISSUED
    service._invoices.create_draft.assert_not_awaited()
    service._operations.record.assert_not_awaited()


async def test_zero_discount_never_creates_revision():
    service, invoice, _, customer = context()
    service._promos.validate.return_value.promo.percent_value = Decimal("0")
    with pytest.raises(ValidationDomainError, match="no discount"):
        await service.apply(invoice_id=invoice.id, customer_id=customer, code="GIFT10", key="zero")
    assert invoice.status is InvoiceStatus.ISSUED
    service._invoices.create_draft.assert_not_awaited()


async def test_superseded_invoice_cannot_settle():
    from app.services.payment_service import PaymentService

    service = object.__new__(PaymentService)
    service._invoices = AsyncMock()
    service._orders = AsyncMock()
    service._invoices.lock.return_value = SimpleNamespace(status=InvoiceStatus.CANCELLED)
    with pytest.raises(ConflictError):
        await service._settle_invoice(SimpleNamespace(reference_invoice_id=uuid4()))
    service._orders.lock.assert_not_awaited()


@pytest.mark.parametrize("changed", [False, True])
async def test_replay_is_customer_scoped_and_binds_invoice_and_normalized_code(changed):
    service, invoice, _, customer = context()
    service._operations.get.return_value = SimpleNamespace(
        invoice_id=invoice.id, code="GIFT10", result_invoice_id=uuid4()
    )
    service._invoices.get_for_customer.return_value = invoice
    if changed:
        with pytest.raises(ConflictError):
            await service.apply(invoice_id=invoice.id, customer_id=customer, code=None, key="k")
    else:
        returned, _ = await service.apply(
            invoice_id=invoice.id, customer_id=customer, code="gift10", key="k"
        )
        assert returned is invoice
    service._operations.get.assert_awaited_once_with(customer, "k")
    service._invoices.lock_for_customer.assert_not_awaited()
    service._promos.reserve.assert_not_awaited()


def test_legacy_or_malformed_policy_fails_closed():
    _, invoice, _, _ = context()
    for policy in [None, {}, {"pricing_policy": {"service_fee_rate": "NaN"}}]:
        invoice.pricing_breakdown = policy
        with pytest.raises(ConflictError):
            stored_pricing_config(invoice)


@pytest.mark.parametrize(
    "body",
    [{}, {"code": ""}, {"code": " "}, {"code": 12}, {"code": None, "customer_id": str(uuid4())}],
)
def test_application_input_rejects_malformed_and_mass_assignment(body):
    with pytest.raises(ValidationError):
        ApplyInvoicePromoRequest.model_validate(body)
    assert ApplyInvoicePromoRequest(code="gIfT10").code == "GIFT10"
    assert ApplyInvoicePromoRequest(code=None).code is None


def test_preview_validates_uuid_and_normalizes_case():
    with pytest.raises(ValidationError):
        PromoValidateRequest(order_id="invalid", code="GIFT10")
    assert PromoValidateRequest(order_id=uuid4(), code="gift10").code == "GIFT10"


async def test_invoice_ownership_lock_and_promo_lookup_are_parameterized():
    session = AsyncMock()
    await InvoiceRepository(session).lock_for_customer(uuid4(), uuid4())
    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "orders.customer_id =" in sql and "FOR UPDATE OF invoices" in sql
    await PromoRepository(session).get_by_code("gIfT10")
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "GIFT10" in compiled.params.values()
    assert "GIFT10" not in str(compiled)
    await InvoiceRepository(session).get_active_for_customer(uuid4(), uuid4())
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "orders.customer_id =" in sql and "orders.courier_id =" not in sql


@pytest.mark.parametrize(
    "role,status,deleted",
    [
        (UserRole.CUSTOMER, UserStatus.ACTIVE, None),
        (UserRole.CUSTOMER, UserStatus.BANNED, None),
        (UserRole.CUSTOMER, UserStatus.PENDING_VERIFICATION, None),
        (UserRole.CUSTOMER, UserStatus.ACTIVE, datetime.now(UTC)),
        (UserRole.COURIER, UserStatus.ACTIVE, None),
        (UserRole.ADMIN, UserStatus.ACTIVE, None),
    ],
)
async def test_customer_eligibility_rejects_other_roles_and_inactive_accounts(
    role, status, deleted
):
    from app.core.exceptions import ForbiddenError
    from app.services.courier_eligibility_service import CourierEligibilityService

    users = AsyncMock()
    users.get.return_value = SimpleNamespace(role=role, status=status, deleted_at=deleted)
    service = CourierEligibilityService(users=users, couriers=AsyncMock())
    if role is UserRole.CUSTOMER and status is UserStatus.ACTIVE and deleted is None:
        await service.require_customer(uuid4())
    else:
        with pytest.raises(ForbiddenError):
            await service.require_customer(uuid4())


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER, UserRole.ADMIN])
async def test_application_route_enforces_customer_and_required_key(monkeypatch, role):
    app = create_app(make_test_settings())
    actor = Actor(id=uuid4(), role=role, jti="test")
    service = AsyncMock()
    service.apply.return_value = (Mock(), [])
    monkeypatch.setattr(invoice_routes, "InvoicePromoService", Mock(return_value=service))
    response_body = {
        "id": str(uuid4()),
        "order_id": str(uuid4()),
        "status": "ISSUED",
        "currency": "SAR",
        "items_net_amount": "500.00",
        "courier_fee_amount": "100.00",
        "service_fee_amount": "30.00",
        "discount_amount": "60.00",
        "net_after_discount_amount": "570.00",
        "total_amount": "570.00",
        "promo_code": "GIFT10",
        "issued_at": "2026-10-03T10:00:00Z",
        "expires_at": "2026-10-04T10:00:00Z",
        "items": [],
    }
    monkeypatch.setattr(invoice_routes, "_detail", lambda *args: response_body)

    async def no_database():
        yield AsyncMock()

    app.dependency_overrides[get_db] = no_database
    app.dependency_overrides[require_auth] = lambda: actor
    invoice_id = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/invoices/{invoice_id}/promo",
            json={"code": "gift10"},
            headers={"Idempotency-Key": "key"},
        )
        if role is not UserRole.CUSTOMER:
            assert response.status_code == 403
            service.apply.assert_not_awaited()
            return
        assert response.status_code == 200 and response.json() == response_body
        service.apply.assert_awaited_once_with(
            invoice_id=invoice_id, customer_id=actor.id, code="GIFT10", key="key"
        )
        missing_key = await client.post(f"/api/invoices/{invoice_id}/promo", json={"code": None})
        assert missing_key.status_code == 422


async def test_preview_own_reserved_code_does_not_double_count_usage():
    from app.models.enums import PromoRedemptionStatus
    from app.services.promo_service import PromoService

    repo = AsyncMock()
    actor_id, invoice_id, promo_id = uuid4(), uuid4(), uuid4()
    promo = SimpleNamespace(
        id=promo_id,
        is_active=True,
        starts_at=None,
        ends_at=None,
        min_order_amount=Decimal("0"),
        max_total_usages=1,
        used_count=1,
        max_usages_per_user=1,
        discount_type=PromoDiscountType.PERCENT,
        percent_value=Decimal("10"),
        fixed_amount=None,
        max_discount_amount=None,
    )
    repo.get_by_code.return_value = promo
    repo.get_redemption_by_invoice.return_value = SimpleNamespace(
        promo_id=promo_id, user_id=actor_id, status=PromoRedemptionStatus.RESERVED
    )
    repo.count_user_redemptions.return_value = 1
    result = await PromoService(repo).validate(
        code="gift10",
        discountable_base=Decimal("600"),
        user_id=actor_id,
        current_invoice_id=invoice_id,
    )
    assert result.discount_amount == Decimal("60.00")
    repo.atomic_reserve.assert_not_awaited()
