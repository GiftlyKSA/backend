"""Direct payment-service and money-hold tests for the error and edge branches."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.core.config import Environment, Settings
from app.core.exceptions import (
    ConflictError,
    InsufficientFundsError,
    InvalidWebhookSignatureError,
    NotFoundError,
    PaymentAmountMismatchError,
    ValidationDomainError,
)
from app.core.redis import build_redis
from app.integrations.payments.fake import FakePaymentClient
from app.models import Invoice, Order, PaymentIntent, User, Wallet
from app.models.enums import (
    InvoiceStatus,
    OrderStatus,
    PaymentIntentStatus,
    PaymentPurpose,
    UserRole,
    WalletType,
)
from app.repositories.wallet_repository import WalletRepository
from app.services.expiry_service import ExpiryService
from app.services.money_service import MoneyService
from app.services.payment_service import build_payment_service
from geoalchemy2 import WKTElement
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_test_settings


def _settings(**extra_overrides: object) -> Settings:
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    if os.environ.get("REDIS_URL"):
        overrides["REDIS_URL"] = os.environ["REDIS_URL"]
    overrides.update(extra_overrides)
    return make_test_settings(**overrides)


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(_settings())
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"redis unavailable: {exc}")
    yield client
    await client.aclose()


def _service(db: AsyncSession, redis: Redis, *, settings: Settings | None = None) -> object:
    settings = settings or _settings()
    return build_payment_service(
        session=db,
        gateway=FakePaymentClient(settings.ENVIRONMENT),
        redis=redis,
        settings=settings,
    )


async def _customer_with_wallet(db: AsyncSession) -> tuple[User, Wallet]:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db.add(user)
    await db.flush()
    wallet = Wallet(user_id=user.id, type=WalletType.CUSTOMER)
    db.add(wallet)
    await db.flush()
    return user, wallet


async def test_topup_rejects_out_of_bounds(db_session: AsyncSession, redis_client: Redis) -> None:
    user, _wallet = await _customer_with_wallet(db_session)
    svc = _service(db_session, redis_client)
    with pytest.raises(ValidationDomainError):
        await svc.create_topup(user_id=user.id, amount=Decimal("10.00"))  # below the min
    with pytest.raises(ValidationDomainError):
        await svc.create_topup(user_id=user.id, amount=Decimal("999999.00"))  # above the max


async def test_topup_requires_wallet(db_session: AsyncSession, redis_client: Redis) -> None:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()  # no wallet created
    with pytest.raises(NotFoundError):
        await _service(db_session, redis_client).create_topup(
            user_id=user.id, amount=Decimal("500.00")
        )


async def _issued_invoice(db: AsyncSession) -> tuple[User, Order, Invoice]:
    user, _wallet = await _customer_with_wallet(db)
    courier = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db.add(courier)
    await db.flush()
    order = Order(
        customer_id=user.id,
        courier_id=courier.id,
        delivery_city="Jeddah",
        delivery_location=WKTElement("POINT(39.2 21.5)", srid=4326),
        delivery_date=datetime.now(UTC).date() + timedelta(days=10),
        status=OrderStatus.WAITING_PAYMENT,
    )
    db.add(order)
    await db.flush()
    invoice = Invoice(
        order_id=order.id,
        issued_by_courier_id=courier.id,
        status=InvoiceStatus.ISSUED,
        items_net_amount=Decimal("500.00"),
        courier_fee_amount=Decimal("100.00"),
        service_fee_amount=Decimal("30.00"),
        discount_amount=Decimal("0.00"),
        net_after_discount_amount=Decimal("630.00"),
        tax_amount=Decimal("94.50"),
        total_amount=Decimal("724.50"),
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db.add(invoice)
    await db.flush()
    return user, order, invoice


async def test_pay_rejects_non_issued_invoice(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    user, _order, invoice = await _issued_invoice(db_session)
    invoice.status = InvoiceStatus.PAID
    await db_session.flush()
    with pytest.raises(ConflictError):
        await _service(db_session, redis_client).pay_invoice(
            invoice_id=invoice.id, customer_id=user.id
        )


async def test_pay_rejects_expired_invoice(db_session: AsyncSession, redis_client: Redis) -> None:
    user, _order, invoice = await _issued_invoice(db_session)
    invoice.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()
    with pytest.raises(ConflictError):
        await _service(db_session, redis_client).pay_invoice(
            invoice_id=invoice.id, customer_id=user.id
        )


async def test_pay_unknown_invoice_is_404(db_session: AsyncSession, redis_client: Redis) -> None:
    user, _wallet = await _customer_with_wallet(db_session)
    with pytest.raises(NotFoundError):
        await _service(db_session, redis_client).pay_invoice(
            invoice_id=uuid.uuid4(), customer_id=user.id
        )


async def test_hold_funds_rejects_insufficient(db_session: AsyncSession) -> None:
    _user, wallet = await _customer_with_wallet(db_session)
    money = MoneyService(WalletRepository(db_session))
    with pytest.raises(InsufficientFundsError):
        await money.hold_funds(wallet_id=wallet.id, amount=Decimal("50.00"))


async def test_webhook_rejects_bad_signature(db_session: AsyncSession, redis_client: Redis) -> None:
    with pytest.raises(InvalidWebhookSignatureError):
        await _service(db_session, redis_client).handle_webhook(
            raw_body=_body("x", "1.00"),
            signature="bad",
        )


async def test_webhook_unknown_transaction_is_404(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    svc = _service(db_session, redis_client)
    gateway = FakePaymentClient(_settings().ENVIRONMENT)
    body = _body("UNKNOWN-LINK", "1.00")
    with pytest.raises(NotFoundError):
        # Sign with the same test secret the service's gateway verifies against.
        await svc.handle_webhook(raw_body=body, signature=gateway.sign(body))


async def test_webhook_amount_mismatch_rejected(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    from app.repositories.payment_repository import PaymentRepository

    user, _wallet = await _customer_with_wallet(db_session)
    payments = PaymentRepository(db_session)
    intent = await payments.create_intent(
        user_id=user.id,
        purpose=PaymentPurpose.WALLET_TOPUP,
        amount=Decimal("500.00"),
        reference_invoice_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    await payments.attach_simulated_checkout(
        intent, payment_link_id="LINK-MISMATCH", url="http://x"
    )

    gateway = FakePaymentClient(_settings().ENVIRONMENT)
    body = _body("LINK-MISMATCH", "999.00")
    with pytest.raises(PaymentAmountMismatchError):
        await _service(db_session, redis_client).handle_webhook(
            raw_body=body, signature=gateway.sign(body)
        )


def _signed(body: bytes) -> str:
    return FakePaymentClient(_settings().ENVIRONMENT).sign(body)


def _body(payment_link_id: str, amount: str, status: str = "PAID") -> bytes:
    import json as _json

    return _json.dumps(
        {
            "event_type": "PAYMENT_SUCCEEDED" if status == "PAID" else "PAYMENT_FAILED",
            "data": {
                "payment_link": {"id": payment_link_id},
                "payment": {"status": status, "amount": amount},
            },
        }
    ).encode("utf-8")


async def test_create_topup_and_settle_via_webhook(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    user, wallet = await _customer_with_wallet(db_session)
    svc = _service(db_session, redis_client)
    result = await svc.create_topup(user_id=user.id, amount=Decimal("500.00"))
    assert result.payment_url and result.amount == Decimal("500.00")

    intent = await svc._payments.get_intent(result.intent_id)  # type: ignore[attr-defined]
    assert intent.gateway_reference is not None
    assert intent.checkout_provider == "SIMULATED"
    body = _body(intent.gateway_reference, "500.00")
    out = await svc.handle_webhook(raw_body=body, signature=_signed(body))
    assert out.outcome == "processed"
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("500.00")


async def test_development_topup_settles_without_a_payment_link(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    from app.repositories.payment_repository import PaymentRepository

    user, wallet = await _customer_with_wallet(db_session)
    settings = _settings(ENVIRONMENT=Environment.DEVELOPMENT.value)

    result = await _service(db_session, redis_client, settings=settings).create_topup(
        user_id=user.id, amount=Decimal("500.00")
    )

    assert result.payment_url is None
    intent = await PaymentRepository(db_session).get_intent(result.intent_id)
    assert intent is not None
    assert intent.status is PaymentIntentStatus.PAID
    assert intent.gateway_reference is None
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("500.00")


async def test_pay_invoice_from_wallet_settles(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    user, order, invoice = await _issued_invoice(db_session)
    wallet = await WalletRepository(db_session).get_by_user(user.id)
    assert wallet is not None
    wallet.balance = Decimal("1000.00")  # fund the wallet directly for this branch test
    await db_session.flush()

    svc = _service(db_session, redis_client)
    result = await svc.pay_invoice(invoice_id=invoice.id, customer_id=user.id)
    assert result.status == "PAID"
    assert result.amount_from_wallet == Decimal("724.50")
    assert invoice.status is InvoiceStatus.PAID
    assert order.status is OrderStatus.IN_PROGRESS
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("275.50")


async def test_pay_invoice_via_gateway_then_webhook_settles(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    from app.repositories.payment_repository import PaymentRepository

    user, order, invoice = await _issued_invoice(db_session)  # wallet balance 0
    svc = _service(db_session, redis_client)
    result = await svc.pay_invoice(invoice_id=invoice.id, customer_id=user.id)
    assert result.status == "PENDING"
    assert result.amount_from_gateway == Decimal("724.50")

    intent = await PaymentRepository(db_session).get_open_intent_for_invoice(invoice.id)
    assert intent is not None
    assert intent.gateway_reference is not None
    body = _body(intent.gateway_reference, "724.50")
    out = await svc.handle_webhook(raw_body=body, signature=_signed(body))
    assert out.outcome == "processed"
    assert invoice.status is InvoiceStatus.PAID
    assert order.status is OrderStatus.IN_PROGRESS


async def test_development_invoice_payment_settles_without_a_payment_link(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    user, order, invoice = await _issued_invoice(db_session)
    settings = _settings(ENVIRONMENT=Environment.DEVELOPMENT.value)

    result = await _service(db_session, redis_client, settings=settings).pay_invoice(
        invoice_id=invoice.id, customer_id=user.id
    )

    assert result.status == "PAID"
    assert result.payment_url is None
    assert result.amount_from_gateway == Decimal("724.50")
    intent = await db_session.scalar(
        select(PaymentIntent).where(PaymentIntent.reference_invoice_id == invoice.id)
    )
    assert intent is not None
    assert intent.status is PaymentIntentStatus.PAID
    assert intent.gateway_reference is None
    await db_session.refresh(invoice)
    await db_session.refresh(order)
    assert invoice.status is InvoiceStatus.PAID
    assert order.status is OrderStatus.IN_PROGRESS


async def test_webhook_marks_intent_failed_on_non_paid(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    from app.repositories.payment_repository import PaymentRepository

    user, _wallet = await _customer_with_wallet(db_session)
    payments = PaymentRepository(db_session)
    intent = await payments.create_intent(
        user_id=user.id,
        purpose=PaymentPurpose.WALLET_TOPUP,
        amount=Decimal("500.00"),
        reference_invoice_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    await payments.attach_simulated_checkout(intent, payment_link_id="LINK-FAIL", url="http://x")
    body = _body("LINK-FAIL", "500.00", status="FAILED")
    out = await _service(db_session, redis_client).handle_webhook(
        raw_body=body, signature=_signed(body)
    )
    assert out.outcome == "failed"


@pytest.mark.parametrize("retry_outcome", ["PAID", "EXPIRED", "FAILED", "CANCELLED"])
async def test_split_failure_retry_preserves_other_holds_and_exact_balances(
    db_session: AsyncSession, redis_client: Redis, retry_outcome: str
) -> None:
    from app.repositories.payment_repository import PaymentRepository

    user, order, invoice = await _issued_invoice(db_session)
    wallets = WalletRepository(db_session)
    wallet = await wallets.get_by_user(user.id)
    assert wallet is not None
    money = MoneyService(wallets)
    await _service(
        db_session, redis_client, settings=_settings(ENVIRONMENT=Environment.DEVELOPMENT.value)
    ).create_topup(user_id=user.id, amount=Decimal("350.00"))
    await money.hold_funds(wallet_id=wallet.id, amount=Decimal("50.00"))
    service = _service(db_session, redis_client)
    payments = PaymentRepository(db_session)
    await service.pay_invoice(invoice_id=invoice.id, customer_id=user.id)
    first = await payments.get_open_intent_for_invoice(invoice.id)
    assert first is not None and first.gateway_reference is not None
    assert first.wallet_reserved_amount == Decimal("300.00")
    await db_session.refresh(wallet)
    assert (wallet.balance, wallet.held_balance) == (Decimal("350.00"), Decimal("350.00"))

    failed_body = _body(first.gateway_reference, "424.50", "FAILED")
    assert (
        await service.handle_webhook(raw_body=failed_body, signature=_signed(failed_body))
    ).outcome == "failed"
    await db_session.refresh(wallet)
    assert (wallet.balance, wallet.held_balance) == (Decimal("350.00"), Decimal("50.00"))
    assert await money.available_balance(user.id) == Decimal("300.00")

    # A retry owns a different amount; stale failure/success callbacks must not release it.
    await money.hold_funds(wallet_id=wallet.id, amount=Decimal("25.00"))
    await service.pay_invoice(invoice_id=invoice.id, customer_id=user.id)
    second = await payments.get_open_intent_for_invoice(invoice.id)
    assert second is not None and second.id != first.id and second.gateway_reference is not None
    assert second.wallet_reserved_amount == Decimal("275.00")
    for status in ("FAILED", "PAID"):
        stale_body = _body(first.gateway_reference, "424.50", status)
        assert (
            await service.handle_webhook(raw_body=stale_body, signature=_signed(stale_body))
        ).outcome == "already_processed"
    await db_session.refresh(wallet)
    assert wallet.held_balance == Decimal("350.00")

    if retry_outcome == "CANCELLED":
        from unittest.mock import AsyncMock

        from app.repositories.invoice_repository import InvoiceRepository
        from app.repositories.order_repository import OrderRepository
        from app.repositories.promo_repository import PromoRepository
        from app.services.invoice_service import InvoiceService
        from app.services.payment_reservation_service import build_payment_reservation_service
        from app.services.promo_service import PromoService

        invoices = InvoiceService(
            invoices=InvoiceRepository(db_session),
            orders=OrderRepository(db_session),
            promos=PromoService(PromoRepository(db_session)),
            eligibility=AsyncMock(),
            reservations=build_payment_reservation_service(db_session),
            settings=_settings(),
        )
        await invoices.cancel_invoice(invoice_id=invoice.id, courier_id=order.courier_id)
        assert invoice.status is InvoiceStatus.CANCELLED
        assert order.status is OrderStatus.ASSIGNED
        assert second.status is PaymentIntentStatus.EXPIRED
        assert not await ExpiryService(db_session).expire_invoice(invoice.id)
        body = _body(second.gateway_reference, "449.50", "PAID")
        assert (
            await service.handle_webhook(raw_body=body, signature=_signed(body))
        ).outcome == "already_processed"
    elif retry_outcome == "EXPIRED":
        invoice.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db_session.flush()
        assert await ExpiryService(db_session).expire_invoice(invoice.id)
        assert not await ExpiryService(db_session).expire_invoice(invoice.id)
        assert order.status is OrderStatus.ASSIGNED
    else:
        body = _body(second.gateway_reference, "449.50", retry_outcome)
        await service.handle_webhook(raw_body=body, signature=_signed(body))
        assert (
            await service.handle_webhook(raw_body=body, signature=_signed(body))
        ).outcome == "already_processed"
        if retry_outcome == "FAILED":
            invoice.expires_at = datetime.now(UTC) - timedelta(hours=1)
            await db_session.flush()
            assert await ExpiryService(db_session).expire_invoice(invoice.id)
        else:
            assert invoice.status is InvoiceStatus.PAID
            assert order.status is OrderStatus.IN_PROGRESS
    await db_session.refresh(wallet)
    assert wallet.held_balance == Decimal("75.00")
    assert wallet.balance == Decimal("75.00" if retry_outcome == "PAID" else "350.00")
    assert await money.available_balance(user.id) == Decimal(
        "0.00" if retry_outcome == "PAID" else "275.00"
    )


@pytest.mark.parametrize("operation", ["failure", "expiry"])
async def test_reservation_release_rolls_back_with_terminal_transition_failure(
    db_session: AsyncSession, redis_client: Redis, monkeypatch, operation: str
) -> None:
    from unittest.mock import AsyncMock

    from app.repositories.payment_repository import PaymentRepository

    user, order, invoice = await _issued_invoice(db_session)
    wallet = await WalletRepository(db_session).get_by_user(user.id)
    assert wallet is not None
    await _service(
        db_session, redis_client, settings=_settings(ENVIRONMENT=Environment.DEVELOPMENT.value)
    ).create_topup(user_id=user.id, amount=Decimal("300.00"))
    service = _service(db_session, redis_client)
    await service.pay_invoice(invoice_id=invoice.id, customer_id=user.id)
    intent = await PaymentRepository(db_session).get_open_intent_for_invoice(invoice.id)
    assert intent is not None and intent.gateway_reference is not None
    invoice.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()

    with pytest.raises(RuntimeError, match="storage failed"):
        async with db_session.begin_nested():
            if operation == "failure":
                monkeypatch.setattr(
                    service._payments,
                    "mark_failed",
                    AsyncMock(side_effect=RuntimeError("storage failed")),
                )
                body = _body(intent.gateway_reference, "424.50", "FAILED")
                await service.handle_webhook(raw_body=body, signature=_signed(body))
            else:
                expiry = ExpiryService(db_session)
                monkeypatch.setattr(
                    expiry._promos, "release", AsyncMock(side_effect=RuntimeError("storage failed"))
                )
                await expiry.expire_invoice(invoice.id)
    for record in (wallet, intent, invoice, order):
        await db_session.refresh(record)
    assert wallet.balance == Decimal("300.00")
    assert wallet.held_balance == Decimal("300.00")
    assert intent.status is PaymentIntentStatus.NEW
    assert invoice.status is InvoiceStatus.ISSUED
    assert order.status is OrderStatus.WAITING_PAYMENT
