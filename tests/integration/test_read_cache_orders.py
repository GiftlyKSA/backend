"""Private order-summary cache preserves live eligibility and related-data invalidation."""

import os
from datetime import date
from uuid import uuid4

import pytest
from app.core.deps import get_db
from app.core.jwt import create_access_token
from app.main import create_app
from app.models import City, CourierProfile, Order, Rating, User
from app.models.enums import OrderStatus, UserRole
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text, update

from tests.conftest import make_test_settings


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER])
async def test_order_pages_cache_filters_and_invalidate_owned_changes(db_session, role):
    settings = make_test_settings(REDIS_URL=os.environ["REDIS_URL"])
    customer, courier, foreign = [
        User(phone=f"+96650{uuid4().int % 10000000:07d}", role=kind)
        for kind in (UserRole.CUSTOMER, UserRole.COURIER, role)
    ]
    db_session.add_all([customer, courier, foreign])
    await db_session.flush()
    city = await db_session.scalar(select(City).where(City.name == "Riyadh"))
    profile = CourierProfile(
        user_id=courier.id,
        city_of_residence_id=city.id,
        national_id_encrypted="synthetic-fixture",
        is_verified=True,
    )
    db_session.add(profile)
    if role == UserRole.COURIER:
        db_session.add(
            CourierProfile(
                user_id=foreign.id,
                city_of_residence_id=city.id,
                national_id_encrypted="synthetic-fixture",
                is_verified=True,
            )
        )
    orders = [
        Order(
            customer_id=customer.id,
            courier_id=courier.id,
            city=city,
            delivery_date=date.today(),
            status=OrderStatus.COMPLETED,
            description=f"Gift {i}",
        )
        for i in range(2)
    ]
    db_session.add_all(orders)
    await db_session.flush()
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    actor = customer if role == UserRole.CUSTOMER else courier
    headers = {
        "Authorization": "Bearer "
        + create_access_token(settings, user_id=actor.id, role=role.value)[0]
    }
    app = create_app(settings)

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    connection = await db_session.connection()
    statements = []

    def capture(conn, cursor, statement, arguments, context, many):
        if "FROM orders" in statement:
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    params = {
        "from_date": date.today().isoformat(),
        "to_date": date.today().isoformat(),
        "status": "COMPLETED",
        "limit": 1,
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/orders", params=params, headers=headers)
            second = await client.get("/api/orders", params=params, headers=headers)
            assert first.status_code == second.status_code == 200
            assert first.json() == second.json() and len(statements) == 1
            assert second.headers["cache-control"] == "private, no-store"
            cursor = first.json()["next_cursor"]
            page = await client.get(
                "/api/orders", params={**params, "cursor": cursor}, headers=headers
            )
            assert page.status_code == 200
            assert page.json()["items"][0]["id"] != first.json()["items"][0]["id"]
            empty = await client.get(
                "/api/orders", params={**params, "status": "NEW"}, headers=headers
            )
            assert empty.json() == {"items": [], "next_cursor": None}
            foreign_headers = {
                "Authorization": "Bearer "
                + create_access_token(settings, user_id=foreign.id, role=foreign.role.value)[0]
            }
            assert (
                await client.get("/api/orders", params=params, headers=foreign_headers)
            ).json() == {"items": [], "next_cursor": None}
            item = next(
                order for order in orders if str(order.id) == first.json()["items"][0]["id"]
            )
            await db_session.execute(
                update(Order)
                .where(Order.id == item.id)
                .values(description="<script>literal</script>")
            )
            changed = await client.get("/api/orders", params=params, headers=headers)
            assert changed.json()["items"][0]["description"] == "<script>literal</script>"
            await db_session.execute(
                update(City).where(City.id == city.id).values(name="Cache city label")
            )
            relabeled = await client.get("/api/orders", params=params, headers=headers)
            assert relabeled.json()["items"][0]["delivery_city"] == "Cache city label"
            if role == UserRole.CUSTOMER:
                searched = await client.get(
                    "/api/orders", params={**params, "q": "no-match"}, headers=headers
                )
                assert searched.json() == {"items": [], "next_cursor": None}
                db_session.add(
                    Rating(
                        order_id=item.id, rater_id=customer.id, rated_user_id=courier.id, score=5
                    )
                )
                await db_session.flush()
                rated = await client.get("/api/orders", params=params, headers=headers)
                assert rated.json()["items"][0]["current_actor_has_rated"] is True
                await db_session.execute(
                    update(Order).where(Order.id == item.id).values(customer_id=foreign.id)
                )
                moved = await client.get("/api/orders", params=params, headers=headers)
                assert all(row["id"] != str(item.id) for row in moved.json()["items"])
                received = await client.get("/api/orders", params=params, headers=foreign_headers)
                assert received.json()["items"][0]["id"] == str(item.id)
            else:
                await db_session.execute(
                    update(Order).where(Order.id == item.id).values(courier_id=foreign.id)
                )
                unassigned = await client.get("/api/orders", params=params, headers=headers)
                assert all(row["id"] != str(item.id) for row in unassigned.json()["items"])
                received = await client.get("/api/orders", params=params, headers=foreign_headers)
                assert received.json()["items"][0]["id"] == str(item.id)
                profile.is_verified = False
                await db_session.flush()
                assert (
                    await client.get("/api/orders", params=params, headers=headers)
                ).status_code == 403
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
        await app.state.redis.aclose()
        await app.state.subscription_redis.aclose()
        await app.state.engine.dispose()


async def test_rating_mutations_invalidate_historical_rater_after_owner_transfer(db_session):
    from app.repositories.read_cache_repository import ReadCacheRepository
    from sqlalchemy import delete

    users = [
        User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER) for _ in range(3)
    ]
    db_session.add_all(users)
    await db_session.flush()
    city = await db_session.scalar(select(City).where(City.name == "Riyadh"))
    order = Order(
        customer_id=users[0].id,
        courier_id=users[2].id,
        delivery_date=date.today(),
        city=city,
        status=OrderStatus.COMPLETED,
    )
    db_session.add(order)
    await db_session.flush()
    rating = Rating(order_id=order.id, rater_id=users[1].id, rated_user_id=users[2].id, score=5)
    db_session.add(rating)
    await db_session.flush()
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    repository = ReadCacheRepository(db_session)
    keys = [("orders", users[1].id)]
    before = await repository.versions(keys)
    await db_session.execute(delete(Rating).where(Rating.id == rating.id))
    assert await repository.versions(keys) != before


async def test_rating_commit_waiting_for_owner_transfer_invalidates_restored_owner(db_connection):
    import asyncio

    from app.repositories.read_cache_repository import ReadCacheRepository
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = db_connection.engine
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        users = [
            User(phone=f"+96650{uuid4().int % 10000000:07d}", role=kind)
            for kind in (UserRole.CUSTOMER, UserRole.CUSTOMER, UserRole.COURIER)
        ]
        setup.add_all(users)
        await setup.flush()
        city_id = await setup.scalar(select(City.id).where(City.name == "Riyadh"))
        order = Order(
            customer_id=users[0].id,
            courier_id=users[2].id,
            delivery_city_id=city_id,
            delivery_date=date.today(),
            status=OrderStatus.COMPLETED,
        )
        setup.add(order)
        await setup.flush()
        rating = Rating(order_id=order.id, rater_id=users[1].id, rated_user_id=users[2].id, score=5)
        setup.add(rating)
        await setup.commit()
        owner_ids, order_id, rating_id = [user.id for user in users], order.id, rating.id

    async def delete_rating():
        async with engine.begin() as writer:
            pid = await writer.scalar(text("SELECT pg_backend_pid()"))
            deleting.set_result(pid)
            await writer.execute(delete(Rating).where(Rating.id == rating_id))

    task = None
    try:
        async with engine.connect() as transfer:
            transaction = await transfer.begin()
            await transfer.execute(text("SELECT pg_advisory_xact_lock(7243195821438)"))
            await transfer.execute(
                update(Order).where(Order.id == order_id).values(customer_id=owner_ids[1])
            )
            await transfer.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            epoch = await transfer.scalar(
                text(
                    "SELECT version FROM read_cache_versions WHERE scope='orders' AND owner_id=:id"
                ),
                {"id": owner_ids[1]},
            )
            deleting = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(delete_rating())
            pid = await deleting
            async with engine.connect() as observer:
                async with asyncio.timeout(5):
                    for _ in range(300):
                        waiting = await observer.scalar(
                            text(
                                "SELECT EXISTS(SELECT FROM pg_locks "
                                "WHERE locktype='advisory' AND NOT granted AND pid=:pid)"
                            ),
                            {"pid": pid},
                        )
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("Rating commit did not reach the invalidation gate.")
            await transaction.commit()
        async with asyncio.timeout(5):
            await task
        async with AsyncSession(engine) as reader:
            versions = await ReadCacheRepository(reader).versions([("orders", owner_ids[1])])
            assert versions != (str(epoch),)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with engine.begin() as cleanup:
            await cleanup.execute(delete(Order).where(Order.id == order_id))
            await cleanup.execute(delete(User).where(User.id.in_(owner_ids)))
