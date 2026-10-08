"""Current ownership, priority, and date boundaries for measured read optimizations."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models import Invoice, Occasion, Order, PaymentIntent, User
from app.models.enums import InvoiceStatus, PaymentIntentStatus, PaymentPurpose, UserRole
from app.repositories.payment_repository import PaymentRepository
from app.repositories.planning_repository import PlanningRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import city_by_name


async def payer(session: AsyncSession) -> User:
    user = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    session.add(user)
    await session.flush()
    return user


def attempt(user: User, status: PaymentIntentStatus, state: str, age: int) -> PaymentIntent:
    return PaymentIntent(
        user_id=user.id,
        purpose=PaymentPurpose.WALLET_TOPUP,
        amount=Decimal("12.34"),
        status=status,
        checkout_provider="DHAMEN",
        checkout_state=state,
        expires_at=datetime(2028, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=age),
    )


async def test_owned_topup_review_new_terminal_priority_and_open_only(db_session: AsyncSession):
    user, foreign = await payer(db_session), await payer(db_session)
    review = attempt(user, PaymentIntentStatus.PAID, "REVIEW", 1)
    active = attempt(user, PaymentIntentStatus.NEW, "ACTIVE", 2)
    terminal = attempt(user, PaymentIntentStatus.PAID, "CLOSED", 3)
    excluded = attempt(user, PaymentIntentStatus.NEW, "REVIEW", 4)
    excluded.checkout_provider = "SIMULATED"
    db_session.add_all([review, active, terminal, excluded])
    db_session.add(attempt(foreign, PaymentIntentStatus.PAID, "REVIEW", 5))
    await db_session.flush()
    repo = PaymentRepository(db_session)
    for open_only in (True, False):
        assert (await repo.get_topup_for_actor(user.id, open_only=open_only)).id == review.id
    review.checkout_state = "CLOSED"
    await db_session.flush()
    assert (await repo.get_topup_for_actor(user.id)).id == active.id
    active.status = PaymentIntentStatus.FAILED
    await db_session.flush()
    assert await repo.get_topup_for_actor(user.id, open_only=True) is None
    assert (await repo.get_topup_for_actor(user.id)).id == terminal.id
    assert await repo.get_topup_for_actor(uuid4()) is None


async def test_owned_order_recovery_review_precedes_new_across_invoice_revisions(
    db_session: AsyncSession,
):
    user, foreign = await payer(db_session), await payer(db_session)
    courier = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db_session.add(courier)
    await db_session.flush()
    order = Order(
        customer_id=user.id,
        courier_id=courier.id,
        city=await city_by_name(db_session, "Riyadh"),
        delivery_date=date.today(),
    )
    db_session.add(order)
    await db_session.flush()
    intents = []
    for age, status, state in (
        (1, PaymentIntentStatus.PAID, "REVIEW"),
        (2, PaymentIntentStatus.NEW, "ACTIVE"),
        (3, PaymentIntentStatus.PAID, "CLOSED"),
    ):
        invoice = Invoice(
            order_id=order.id,
            issued_by_courier_id=courier.id,
            items_net_amount=Decimal("12.34"),
            net_after_discount_amount=Decimal("12.34"),
            total_amount=Decimal("12.34"),
            status=InvoiceStatus.ISSUED
            if status == PaymentIntentStatus.NEW
            else InvoiceStatus.CANCELLED,
        )
        db_session.add(invoice)
        await db_session.flush()
        intent = attempt(user, status, state, age)
        intent.purpose = PaymentPurpose.ORDER_INVOICE
        intent.reference_invoice_id = invoice.id
        intent.order_id = order.id
        intents.append(intent)
        db_session.add(intent)
    await db_session.flush()
    repo = PaymentRepository(db_session)
    assert (
        await repo.get_latest_intent_for_order(order_id=order.id, user_id=user.id)
    ).id == intents[0].id
    assert await repo.get_latest_intent_for_order(order_id=order.id, user_id=foreign.id) is None
    intents[0].checkout_state = "CLOSED"
    await db_session.flush()
    assert (
        await repo.get_latest_intent_for_order(order_id=order.id, user_id=user.id)
    ).id == intents[1].id


async def test_occasion_equal_dates_keyset_and_inclusive_owned_range(db_session: AsyncSession):
    user, foreign = await payer(db_session), await payer(db_session)
    day = date(2026, 10, 8)
    records = [Occasion(user_id=user.id, title="Owned", occasion_date=day) for _ in range(5)]
    db_session.add_all(records)
    db_session.add(Occasion(user_id=foreign.id, title="Foreign", occasion_date=day))
    db_session.add(
        Occasion(user_id=user.id, title="Excluded", occasion_date=day + timedelta(days=1))
    )
    await db_session.flush()
    repo = PlanningRepository(db_session)
    found, after = [], None
    for _ in range(4):
        rows = await repo.list_occasions_for_actor(
            user.id, limit=2, from_date=day, to_date=day, after=after
        )
        found.extend(row.id for row in rows)
        if len(rows) < 2:
            break
        after = rows[-1]
    assert found == sorted(record.id for record in records)
