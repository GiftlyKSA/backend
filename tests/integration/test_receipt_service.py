"""Receipt-service and sweeper tests: exactly one receipt on PAID, idempotent, no-email.

The system sends exactly ONE email — the invoice-paid receipt. These assert it fires once
on a PAID invoice, never twice, and never for an unpaid one, using the in-memory
FakeEmailClient so nothing touches the network.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

import httpx
import pytest
from app.core.config import Environment, Settings
from app.integrations.email.base import EmailClient, EmailRecipientSuppressedError
from app.integrations.email.fake import FakeEmailClient
from app.integrations.email.sndr_client import SndrEmailClient
from app.models import Invoice, Order, User
from app.models.enums import InvoiceStatus, OrderStatus, UserRole
from app.repositories import invoice_repository
from app.repositories.invoice_repository import InvoiceRepository
from app.services import receipt_service
from app.services.receipt_service import ReceiptService
from app.workers.receipts import send_pending_receipts
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import make_test_settings
from tests.integration.conftest import city_by_name


def _settings() -> Settings:
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    return make_test_settings(**overrides)


def _service(db: AsyncSession, email: EmailClient) -> ReceiptService:
    return ReceiptService(
        factory=async_sessionmaker(db.bind, expire_on_commit=False),
        email=email,
        settings=_settings(),
    )


@pytest.fixture
def advance_receipt_time(monkeypatch: pytest.MonkeyPatch) -> Callable[[timedelta], datetime]:
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return now.astimezone(tz)

    def advance(delta: timedelta) -> datetime:
        nonlocal now
        now += delta
        return now

    monkeypatch.setattr(invoice_repository, "datetime", Clock)
    monkeypatch.setattr(receipt_service, "datetime", Clock)
    return advance


class _ControlledEmail(FakeEmailClient):
    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(Environment.TEST)
        self.error = error
        self.attempted_invoice_ids: list[str] = []

    async def send_transactional(
        self, to_email: str, template_key: str, variables: dict[str, object]
    ) -> None:
        self.attempted_invoice_ids.append(str(variables["invoice_id"]))
        if self.error is not None:
            raise self.error
        await super().send_transactional(to_email, template_key, variables)


async def _paid_invoice(db: AsyncSession, *, email: str | None) -> Invoice:
    customer = User(
        phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}",
        role=UserRole.CUSTOMER,
        email=email,
    )
    courier = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db.add_all([customer, courier])
    await db.flush()
    order = Order(
        customer_id=customer.id,
        courier_id=courier.id,
        city=await city_by_name(db, "Jeddah"),
        delivery_date=datetime.now(UTC).date() + timedelta(days=10),
        status=OrderStatus.IN_PROGRESS,
    )
    db.add(order)
    await db.flush()
    invoice = Invoice(
        order_id=order.id,
        issued_by_courier_id=courier.id,
        status=InvoiceStatus.PAID,
        items_net_amount=Decimal("500.00"),
        courier_fee_amount=Decimal("100.00"),
        service_fee_amount=Decimal("30.00"),
        discount_amount=Decimal("0.00"),
        net_after_discount_amount=Decimal("630.00"),
        total_amount=Decimal("630.00"),
        issued_at=datetime.now(UTC),
        paid_at=datetime.now(UTC),
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def test_receipt_sent_once_on_paid(db_session: AsyncSession) -> None:
    email = FakeEmailClient(Environment.TEST)
    invoice = await _paid_invoice(db_session, email="buyer@example.com")

    assert await _service(db_session, email).send_receipt(invoice.id) is True
    assert len(email.sent) == 1
    sent = email.sent[0]
    assert sent.to_email == "buyer@example.com"
    assert sent.variables["total_amount"] == "630.00"
    assert sent.variables["order_id"] == str(invoice.order_id)
    # No Restricted data leaks into the template variables.
    assert "phone" not in sent.variables
    await db_session.refresh(invoice)
    assert invoice.receipt_email_sent_at is not None

    # A second attempt is a no-op — never two receipts.
    assert await _service(db_session, email).send_receipt(invoice.id) is False
    assert len(email.sent) == 1


async def test_receipt_skipped_when_no_email(db_session: AsyncSession) -> None:
    email = FakeEmailClient(Environment.TEST)
    invoice = await _paid_invoice(db_session, email=None)
    assert await _service(db_session, email).send_receipt(invoice.id) is False
    assert email.sent == []
    await db_session.refresh(invoice)
    assert invoice.receipt_email_sent_at is None
    assert invoice.receipt_first_attempt_at is None
    assert invoice.receipt_claim_token is None


async def test_receipt_not_sent_for_unpaid_invoice(db_session: AsyncSession) -> None:
    email = FakeEmailClient(Environment.TEST)
    invoice = await _paid_invoice(db_session, email="buyer@example.com")
    invoice.status = InvoiceStatus.ISSUED
    invoice.receipt_email_sent_at = None
    await db_session.flush()
    assert await _service(db_session, email).send_receipt(invoice.id) is False
    assert email.sent == []


async def test_receipt_missing_invoice_is_noop(db_session: AsyncSession) -> None:
    email = FakeEmailClient(Environment.TEST)
    assert await _service(db_session, email).send_receipt(uuid.uuid4()) is False
    assert email.sent == []


async def test_old_failed_receipts_do_not_starve_new_receipts(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    now = advance_receipt_time(timedelta())

    class Email(FakeEmailClient):
        async def send_transactional(
            self, to_email: str, template_key: str, variables: dict[str, object]
        ) -> None:
            if to_email in failed_addresses:
                raise RuntimeError("synthetic provider failure")
            await super().send_transactional(to_email, template_key, variables)

    failed_addresses = {f"{uuid.uuid4()}@example.com" for _ in range(2)}
    for address in failed_addresses:
        invoice = await _paid_invoice(db_session, email=address)
        invoice.paid_at = now - timedelta(days=2)
    deliverable_address = f"{uuid.uuid4()}@example.com"
    deliverable = await _paid_invoice(db_session, email=deliverable_address)
    deliverable.paid_at = now - timedelta(days=1)
    await db_session.flush()

    email = Email(Environment.TEST)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    assert (
        await send_pending_receipts(limit=2, email=email, factory=factory, settings=_settings())
        == 0
    )
    # Make retries due before the second sweep. The newer first send still takes precedence.
    advance_receipt_time(timedelta(minutes=15))
    await send_pending_receipts(limit=2, email=email, factory=factory, settings=_settings())
    assert [message.to_email for message in email.sent] == [deliverable_address]
    await db_session.refresh(deliverable)
    assert deliverable.receipt_email_sent_at is not None


async def test_receipt_retries_after_backoff_with_same_delivery_identity(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    email = _ControlledEmail(TimeoutError())
    service = _service(db_session, email)

    with pytest.raises(TimeoutError):
        await service.send_receipt(invoice.id)
    await db_session.refresh(invoice)
    assert invoice.receipt_failed_attempts == 1
    assert invoice.receipt_claim_token is None
    assert invoice.receipt_email_sent_at is None
    assert invoice.receipt_next_attempt_at is not None
    assert invoice.receipt_claimed_until == invoice.receipt_next_attempt_at
    email.error = None
    advance_receipt_time(timedelta(minutes=14))
    assert not await service.send_receipt(invoice.id)
    advance_receipt_time(timedelta(minutes=1))
    assert await service.send_receipt(invoice.id)
    assert email.attempted_invoice_ids == [str(invoice.id)] * 2
    await db_session.refresh(invoice)
    assert invoice.receipt_email_sent_at is not None
    assert invoice.receipt_next_attempt_at is None


async def test_receipt_failures_exhaust_until_guarded_requeue(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    email = _ControlledEmail(RuntimeError("synthetic provider failure"))
    service = _service(db_session, email)
    for delay_minutes in (0, 15, 30, 60, 120, 240, 240, 240):
        advance_receipt_time(timedelta(minutes=delay_minutes))
        with pytest.raises(RuntimeError, match="synthetic provider failure"):
            await service.send_receipt(invoice.id)
    await db_session.refresh(invoice)
    assert invoice.receipt_failed_attempts == 8
    assert invoice.receipt_exhausted_at is not None
    assert invoice.receipt_next_attempt_at is None
    assert invoice.receipt_claim_token is None
    now = advance_receipt_time(timedelta(days=1))
    assert invoice.receipt_claimed_until is not None and invoice.receipt_claimed_until > now
    assert not await service.send_receipt(invoice.id)
    assert email.attempted_invoice_ids == [str(invoice.id)] * 8
    assert invoice.id not in await InvoiceRepository(db_session).list_due_receipt_ids(100)

    # Requeue only after checking the provider outcome and correcting the cause.
    requeue = (
        update(Invoice)
        .where(
            Invoice.id == invoice.id,
            Invoice.status == InvoiceStatus.PAID,
            Invoice.receipt_email_sent_at.is_(None),
            Invoice.receipt_claim_token.is_(None),
            Invoice.receipt_exhausted_at.is_not(None),
        )
        .values(
            receipt_first_attempt_at=None,
            receipt_failed_attempts=0,
            receipt_next_attempt_at=now,
            receipt_exhausted_at=None,
            receipt_claimed_until=None,
        )
        .returning(Invoice.id)
    )
    assert await db_session.scalar(requeue) == invoice.id
    email.error = None
    assert await service.send_receipt(invoice.id)
    assert await db_session.scalar(requeue) is None
    await db_session.refresh(invoice)
    assert invoice.receipt_failed_attempts == 0
    assert invoice.receipt_exhausted_at is None
    assert invoice.receipt_email_sent_at is not None


async def test_explicit_recipient_suppression_exhausts_without_retries(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    email = _ControlledEmail(EmailRecipientSuppressedError())
    service = _service(db_session, email)
    with pytest.raises(EmailRecipientSuppressedError):
        await service.send_receipt(invoice.id)
    advance_receipt_time(timedelta(hours=1))
    assert not await service.send_receipt(invoice.id)
    assert email.attempted_invoice_ids == [str(invoice.id)]
    await db_session.refresh(invoice)
    assert invoice.receipt_failed_attempts == 1
    assert invoice.receipt_exhausted_at is not None


async def test_expired_crash_claim_preserves_fencing_and_replay_window(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    repository = InvoiceRepository(db_session)
    first, second = uuid.uuid4(), uuid.uuid4()
    started = advance_receipt_time(timedelta())
    lease = timedelta(minutes=1)
    assert await repository.claim_receipt(invoice.id, token=first, now=started, lease=lease)
    assert not await repository.claim_receipt(invoice.id, token=second, now=started, lease=lease)
    now = advance_receipt_time(lease)
    assert await repository.claim_receipt(invoice.id, token=second, now=now, lease=lease)
    assert not await repository.complete_receipt(invoice.id, token=first, when=now)
    assert not await repository.finish_receipt_failure(
        invoice.id, token=first, when=now, failed_attempts=1, retry_at=None
    )
    await db_session.refresh(invoice)
    assert invoice.receipt_claim_token == second
    assert invoice.receipt_first_attempt_at == started

    # If the worker never recorded provider acceptance, stop replays after a long outage.
    advance_receipt_time(timedelta(hours=24))
    email = _ControlledEmail()
    assert not await _service(db_session, email).send_receipt(invoice.id)
    assert email.attempted_invoice_ids == []
    await db_session.refresh(invoice)
    assert invoice.receipt_exhausted_at is not None
    assert invoice.receipt_failed_attempts == 0
    assert invoice.receipt_email_sent_at is None


async def test_legacy_claim_with_unknown_first_send_does_not_replay_old_receipt(
    db_session: AsyncSession, advance_receipt_time: Callable[[timedelta], datetime]
) -> None:
    now = advance_receipt_time(timedelta())
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    invoice.paid_at = now - timedelta(days=2)
    invoice.receipt_claim_token = uuid.uuid4()
    invoice.receipt_claimed_until = now - timedelta(minutes=1)
    await db_session.flush()

    email = _ControlledEmail()
    assert not await _service(db_session, email).send_receipt(invoice.id)
    assert email.attempted_invoice_ids == []
    await db_session.refresh(invoice)
    assert invoice.receipt_exhausted_at == now
    assert invoice.receipt_email_sent_at is None


async def test_receipt_attempt_cap_is_checked_before_sending(db_session: AsyncSession) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    invoice.receipt_failed_attempts = 32767
    await db_session.flush()

    email = _ControlledEmail()
    assert not await _service(db_session, email).send_receipt(invoice.id)
    assert email.attempted_invoice_ids == []
    await db_session.refresh(invoice)
    assert invoice.receipt_exhausted_at is not None


async def test_receipt_replays_same_provider_key_after_stamp_failure(
    db_session: AsyncSession,
    advance_receipt_time: Callable[[timedelta], datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice = await _paid_invoice(db_session, email=f"{uuid.uuid4()}@example.com")
    provider_keys: list[str] = []

    def accept_email(request: httpx.Request) -> httpx.Response:
        provider_keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(202, json={"id": "fake-acceptance", "status": "queued"})

    original_complete = InvoiceRepository.complete_receipt

    async def fail_stamp_once(
        repository: InvoiceRepository, invoice_id: uuid.UUID, *, token: uuid.UUID, when: datetime
    ) -> bool:
        if len(provider_keys) == 1:
            raise RuntimeError("simulated failure after provider acceptance")
        return await original_complete(repository, invoice_id, token=token, when=when)

    monkeypatch.setattr(InvoiceRepository, "complete_receipt", fail_stamp_once)
    email = SndrEmailClient("https://api.sndr.sh", "fake-key", "receipts@example.test", "Giftly")
    await email.aclose()
    email._client = httpx.AsyncClient(transport=httpx.MockTransport(accept_email))
    try:
        service = _service(db_session, email)
        with pytest.raises(RuntimeError, match="simulated failure after provider acceptance"):
            await service.send_receipt(invoice.id)
        assert not await service.send_receipt(invoice.id)
        advance_receipt_time(timedelta(minutes=1))
        assert await service.send_receipt(invoice.id)
    finally:
        await email.aclose()

    assert provider_keys == [f"giftly-invoice-receipt-{invoice.id}"] * 2
    await db_session.refresh(invoice)
    assert invoice.receipt_email_sent_at is not None
    assert invoice.receipt_failed_attempts == 0
