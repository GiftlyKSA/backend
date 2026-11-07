"""Financial cleanup at the order cancellation boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, RateLimitedError
from app.models.enums import InvoiceStatus, OrderStatus
from app.services.order_service import OrderService

from tests.conftest import make_test_settings


def _service() -> OrderService:
    service = OrderService(
        session=AsyncMock(),
        orders=AsyncMock(),
        couriers=AsyncMock(),
        eligibility=AsyncMock(),
        media=AsyncMock(),
        messages=AsyncMock(),
        ratings=AsyncMock(),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    service._invoices = AsyncMock()
    service._reservations = AsyncMock()
    service._promos = AsyncMock()
    service._orders.recent_cancellations.return_value = []
    return service


async def test_cancellation_block_lasts_an_hour_from_the_fifth_cancellation() -> None:
    service = _service()
    now = datetime.now(UTC)
    actor_id = uuid4()
    service._orders.recent_cancellations.return_value = [
        now - timedelta(minutes=minutes) for minutes in (10, 23, 35, 50, 65)
    ]
    with pytest.raises(RateLimitedError) as error:
        await service._check_cancellation_limit(actor_id, now)
    assert 3000 <= error.value.retry_after_seconds <= 3001
    assert await service._check_cancellation_limit(actor_id, now + timedelta(minutes=50)) == 0


async def test_customer_reorder_copies_request_and_returns_same_replacement_on_retry() -> None:
    service = _service()
    actor_id, order_id = uuid4(), uuid4()
    city, replacement = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    order = SimpleNamespace(
        id=order_id,
        status=OrderStatus.ASSIGNED,
        courier_id=uuid4(),
        customer_id=actor_id,
        delivery_city_id=city.id,
        delivery_date=datetime.now(UTC).date(),
        delivery_address_note=None,
        description="Flowers",
        replacement_order_id=None,
    )
    service._orders.get_for_actor.return_value = order
    service._orders.lock_for_actor.return_value = order
    service._orders.count_customer_active.return_value = 1
    service._orders.create.return_value = replacement
    service._invoices.get_active_for_order.return_value = None
    service._cities = AsyncMock()
    service._cities.require_active_id.return_value = city

    for _ in range(2):
        result = await service.cancel_order(
            order_id=order_id, actor_id=actor_id, reason="Change courier", reorder=True
        )
        assert result.replacement_order_id == replacement.id
    assert order.status is OrderStatus.CANCELLED
    assert order.cancelled_reason == "Change courier"
    service._orders.create.assert_awaited_once_with(
        customer_id=actor_id,
        description=order.description,
        delivery_city=city,
        delivery_date=order.delivery_date,
        address_note=None,
    )
    service._orders.copy_request_media.assert_awaited_once_with(order, replacement)


async def test_cancel_waiting_payment_releases_invoice_reservations_in_lock_order() -> None:
    service = _service()
    order_id, actor_id, invoice_id = uuid4(), uuid4(), uuid4()
    order = SimpleNamespace(
        customer_id=actor_id,
        id=order_id,
        status=OrderStatus.WAITING_PAYMENT,
        total_amount=10,
        cancelled_reason=None,
    )
    invoice = SimpleNamespace(id=invoice_id, status=InvoiceStatus.ISSUED)
    service._orders.get_for_actor.return_value = order
    service._orders.lock_for_actor.return_value = order
    service._invoices.get_active_for_order.return_value = invoice
    service._invoices.lock.return_value = invoice
    sequence = Mock()
    sequence.attach_mock(service._invoices.lock, "invoice_lock")
    sequence.attach_mock(service._orders.lock_for_actor, "order_lock")

    result = await service.cancel_order(order_id=order_id, actor_id=actor_id, reason="changed")

    assert result is order
    assert [call[0] for call in sequence.mock_calls] == ["invoice_lock", "order_lock"]
    service._reservations.expire_for_invoice.assert_awaited_once_with(invoice_id)
    service._promos.release.assert_awaited_once_with(invoice_id=invoice_id)
    assert invoice.status is InvoiceStatus.CANCELLED
    assert order.status is OrderStatus.CANCELLED
    assert order.total_amount == 0


async def test_cancel_does_not_mutate_order_when_reservation_release_fails() -> None:
    service = _service()
    order_id, actor_id = uuid4(), uuid4()
    order = SimpleNamespace(
        customer_id=actor_id, id=order_id, status=OrderStatus.WAITING_PAYMENT, total_amount=10
    )
    invoice = SimpleNamespace(id=uuid4(), status=InvoiceStatus.ISSUED)
    service._orders.get_for_actor.return_value = order
    service._orders.lock_for_actor.return_value = order
    service._invoices.get_active_for_order.return_value = invoice
    service._invoices.lock.return_value = invoice
    service._reservations.expire_for_invoice.side_effect = RuntimeError("release failed")

    with pytest.raises(RuntimeError, match="release failed"):
        await service.cancel_order(order_id=order_id, actor_id=actor_id, reason=None)

    assert order.status is OrderStatus.WAITING_PAYMENT
    assert invoice.status is InvoiceStatus.ISSUED
    service._promos.release.assert_not_awaited()


async def test_cancel_retries_when_new_invoice_appears_after_lookup() -> None:
    service = _service()
    order_id, actor_id = uuid4(), uuid4()
    order = SimpleNamespace(customer_id=actor_id, id=order_id, status=OrderStatus.WAITING_PAYMENT)
    service._orders.get_for_actor.return_value = order
    service._orders.lock_for_actor.return_value = order
    service._invoices.get_active_for_order.side_effect = [None, SimpleNamespace(id=uuid4())]

    with pytest.raises(ConflictError, match="retry cancellation"):
        await service.cancel_order(order_id=order_id, actor_id=actor_id, reason=None)

    assert order.status is OrderStatus.WAITING_PAYMENT
    service._reservations.expire_for_invoice.assert_not_awaited()
