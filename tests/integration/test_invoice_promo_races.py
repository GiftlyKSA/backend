"""Invoice changes race on independent disposable PostgreSQL transactions."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.config import Environment
from app.core.exceptions import ConflictError, InvalidStateTransitionError
from app.integrations.payments.fake import FakePaymentClient
from app.models import Invoice, Promo, Wallet
from app.models.enums import InvoiceStatus, PromoDiscountType, WalletType
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.payment_repository import PaymentRepository
from app.services.payment_service import build_payment_service
from sqlalchemy import func, select

from tests.integration.test_invoice_service import (
    _assigned_order,
    _customer_promos,
    _input,
    _service,
)
from tests.integration.test_orders_api import _make_stack


@pytest.mark.parametrize("competitor", ["same_key", "different_key", "pay", "cancel"])
async def test_invoice_promo_competes_atomically(competitor: str) -> None:
    settings, engine, factory = await _make_stack()
    try:
        async with factory() as session:
            order = await _assigned_order(session)
            invoice = await _service(session).create_invoice(
                order_id=order.id, courier_id=order.courier_id, data=_input()
            )
            promo = Promo(
                code=f"RACE{uuid4().hex[:8].upper()}",
                description="race",
                discount_type=PromoDiscountType.PERCENT,
                percent_value=Decimal("10"),
                max_total_usages=1,
                max_usages_per_user=1,
            )
            session.add(promo)
            session.add(Wallet(user_id=order.customer_id, type=WalletType.CUSTOMER))
            await session.flush()
            invoice_id, customer_id, courier_id, order_id, code = (
                invoice.id,
                order.customer_id,
                order.courier_id,
                order.id,
                promo.code,
            )
            await session.commit()
        start = asyncio.Event()

        async def attempt(kind: str):
            await start.wait()
            async with factory() as session:
                try:
                    if kind == "pay":
                        result = await build_payment_service(
                            session=session,
                            gateway=FakePaymentClient(Environment.TEST),
                            redis=AsyncMock(),
                            settings=settings,
                        ).pay_invoice(invoice_id=invoice_id, customer_id=customer_id)
                        identifier = result.invoice_id
                    elif kind == "cancel":
                        result = await _service(session).cancel_invoice(
                            invoice_id=invoice_id, courier_id=courier_id
                        )
                        identifier = result.id
                    else:
                        result, _ = await _customer_promos(session).apply(
                            invoice_id=invoice_id,
                            customer_id=customer_id,
                            code=code.lower(),
                            key="other" if kind == "different_key" else "same",
                        )
                        identifier = result.id
                    await session.commit()
                    return "success", identifier
                except (ConflictError, InvalidStateTransitionError):
                    await session.rollback()
                    return "conflict", None

        tasks = [asyncio.create_task(attempt("same_key")), asyncio.create_task(attempt(competitor))]
        start.set()
        async with asyncio.timeout(20):
            outcomes = await asyncio.gather(*tasks)
        successes = [identifier for status, identifier in outcomes if status == "success"]
        if competitor == "same_key":
            assert len(successes) == 2 and successes[0] == successes[1]
        else:
            assert len(successes) == 1 and sum(status == "conflict" for status, _ in outcomes) == 1
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Invoice)
                .where(
                    Invoice.order_id == order_id,
                    Invoice.status.in_(
                        [InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PAID]
                    ),
                )
            )
            assert count <= 1
            original = await InvoiceRepository(session).get(invoice_id)
            intent = await PaymentRepository(session).get_open_intent_for_invoice(invoice_id)
            if intent is not None:
                assert original.status is InvoiceStatus.ISSUED
    finally:
        await engine.dispose()
