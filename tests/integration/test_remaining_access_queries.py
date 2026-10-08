from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models import Conversation, CourierProfile, Order, User
from app.models.enums import OrderStatus, UserRole
from app.repositories.chat_repository import ChatRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from app.services.courier_eligibility_service import CourierEligibilityService
from sqlalchemy import event, func, select, update

from tests.integration.conftest import city_by_name


@pytest.mark.parametrize("count", [1, 100])
async def test_cursor_page_has_constant_queries_and_keeps_city_serializer(db_session, count):
    delivery_day = await db_session.scalar(select(func.current_date()))
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(customer)
    await db_session.flush()
    city = await city_by_name(db_session, "Riyadh")
    orders = [
        Order(
            customer_id=customer.id,
            city=city,
            delivery_date=delivery_day,
            created_at=datetime(2026, 10, 8, tzinfo=UTC) - timedelta(seconds=i),
        )
        for i in range(count + 1)
    ]
    db_session.add_all(orders)
    await db_session.flush()
    customer_id, cursor = customer.id, orders[0].id
    expected = [order.id for order in orders[1:]]
    db_session.expunge_all()
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = await db_session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", record_statement)
    try:
        page = await OrderRepository(db_session).list_for_customer(
            customer_id,
            status=OrderStatus.NEW,
            limit=count,
            before_id=cursor,
            from_date=delivery_day - timedelta(days=1),
            to_date=delivery_day + timedelta(days=1),
        )
        assert [order.id for order in page] == expected
        assert all(order.delivery_city == "Riyadh" for order in page)
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record_statement)
    assert len(statements) == 3
    assert statements[0].startswith("SELECT orders.created_at, orders.id")
    assert sum("FROM cities" in statement for statement in statements) == 1


async def test_chat_and_eligibility_reads_are_fresh_without_hidden_city_queries(db_session):
    delivery_day = await db_session.scalar(select(func.current_date()))
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db_session.add_all([customer, courier])
    await db_session.flush()
    city = await city_by_name(db_session, "Riyadh")
    profile = CourierProfile(
        user_id=courier.id, city=city, is_verified=True, national_id_encrypted="test-ciphertext"
    )
    order = Order(
        customer_id=customer.id, courier_id=courier.id, city=city, delivery_date=delivery_day
    )
    db_session.add_all([profile, order])
    await db_session.flush()
    conversation = Conversation(order_id=order.id, customer_id=customer.id, courier_id=courier.id)
    db_session.add(conversation)
    await db_session.flush()
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = await db_session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", record_statement)
    try:
        repository = ChatRepository(db_session)
        for _ in range(10):
            state = await repository.get_live_state(conversation.id, courier.id)
            assert state is not None and state.courier_verified
            assert state.conversation_id == conversation.id
        assert len(statements) == 10
        statements.clear()
        await CourierEligibilityService(
            users=UserRepository(db_session), couriers=CourierRepository(db_session)
        ).require_courier(courier.id)
        assert len(statements) == 2
        assert not any("FROM cities" in statement for statement in statements)
        statements.clear()
        db_session.expunge_all()
        db_session.info["read_only_request"] = True
        users = UserRepository(db_session)
        assert await users.get(courier.id) is not None
        await CourierEligibilityService(
            users=users, couriers=CourierRepository(db_session)
        ).require_courier(courier.id)
        assert len(statements) == 2
        assert sum("FROM users" in statement for statement in statements) == 1
    finally:
        db_session.info["read_only_request"] = False
        event.remove(connection.sync_connection, "before_cursor_execute", record_statement)
    await db_session.execute(
        update(User)
        .where(User.id == courier.id)
        .values(auth_version=1)
        .execution_options(synchronize_session=False)
    )
    await db_session.execute(
        update(CourierProfile)
        .where(CourierProfile.user_id == courier.id)
        .values(is_verified=False)
        .execution_options(synchronize_session=False)
    )
    state = await repository.get_live_state(conversation.id, courier.id)
    assert state is not None and state.user.auth_version == 1 and not state.courier_verified
    foreign = await repository.get_live_state(conversation.id, uuid4())
    assert foreign is None
    await db_session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(courier_id=customer.id)
        .execution_options(synchronize_session=False)
    )
    state = await repository.get_live_state(conversation.id, courier.id)
    assert state is not None and state.conversation_id is None


@pytest.mark.parametrize("filter_change", ["owner", "status", "from_date", "to_date", "city"])
async def test_cursor_rejects_anchor_outside_current_list_scope(db_session, filter_change):
    delivery_day = await db_session.scalar(select(func.current_date()))
    customer = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(customer)
    await db_session.flush()
    order = Order(
        customer_id=customer.id,
        city=await city_by_name(db_session, "Riyadh"),
        delivery_date=delivery_day,
    )
    db_session.add(order)
    await db_session.flush()
    repository = OrderRepository(db_session)
    with pytest.raises(NotFoundError, match="Pagination cursor not found in this list"):
        if filter_change == "city":
            await repository.list_available(
                (await city_by_name(db_session, "Jeddah")).id, limit=20, before_id=order.id
            )
        else:
            await repository.list_for_customer(
                uuid4() if filter_change == "owner" else customer.id,
                status=OrderStatus.ASSIGNED if filter_change == "status" else OrderStatus.NEW,
                limit=20,
                before_id=order.id,
                from_date=delivery_day + timedelta(days=1)
                if filter_change == "from_date"
                else None,
                to_date=delivery_day - timedelta(days=1) if filter_change == "to_date" else None,
            )
