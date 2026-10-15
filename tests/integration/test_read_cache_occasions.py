"""Private calendar cache respects fresh authentication, ownership, cursors and edits."""

import os
from datetime import date
from uuid import UUID, uuid4

import pytest
from app.core.deps import get_db
from app.core.jwt import create_access_token
from app.main import create_app
from app.models import Occasion, User
from app.models.enums import UserRole, UserStatus
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text

from tests.conftest import make_test_settings


@pytest.mark.parametrize("path", ["/api/occasions", "/api/occasions/occurrences"])
async def test_occasion_pages_cache_owned_data_and_invalidate_edits_and_cursors(db_session, path):
    settings = make_test_settings(REDIS_URL=os.environ["REDIS_URL"])
    users = [
        User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER) for _ in range(2)
    ]
    db_session.add_all(users)
    await db_session.flush()
    first = Occasion(
        user_id=users[0].id,
        title="First private",
        occasion_date=date(2028, 5, 10),
        reminder_days_before=0,
    )
    second = Occasion(
        user_id=users[0].id,
        title="Second private",
        occasion_date=date(2028, 5, 11),
        reminder_days_before=0,
    )
    other = Occasion(
        user_id=users[1].id,
        title="Other private",
        occasion_date=date(2028, 5, 10),
        reminder_days_before=0,
    )
    db_session.add_all([first, second, other])
    await db_session.flush()
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    app = create_app(settings)

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    headers = [
        {
            "Authorization": "Bearer "
            + create_access_token(settings, user_id=user.id, role=user.role.value)[0]
        }
        for user in users
    ]
    params = {"from_date": "2028-05-01", "to_date": "2028-05-31", "limit": 1}
    connection = await db_session.connection()
    statements = []

    def capture(conn, cursor, statement, arguments, context, many):
        if "FROM occasions" in statement:
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            a = await client.get(path, params=params, headers=headers[0])
            b = await client.get(path, params=params, headers=headers[0])
            assert a.status_code == b.status_code == 200
            assert a.json() == b.json() and len(statements) == 1
            assert b.headers["cache-control"] == "private, no-store"
            foreign = await client.get(path, params=params, headers=headers[1])
            assert foreign.json()["items"][0]["title"] == "Other private"
            cursor = a.json()["next_cursor"]
            page = await client.get(path, params={**params, "cursor": cursor}, headers=headers[0])
            assert page.status_code == 200 and page.json()["items"][0]["title"] == "Second private"
            changed = await client.patch(
                f"/api/occasions/{first.id}",
                json={"title": "<script>literal</script>"},
                headers=headers[0],
            )
            assert changed.status_code == 200
            fresh = await client.get(path, params=params, headers=headers[0])
            assert fresh.json()["items"][0]["title"] == "<script>literal</script>"
            deleted = await client.delete(f"/api/occasions/{first.id}", headers=headers[0])
            assert deleted.status_code == 204
            stale = await client.get(path, params={**params, "cursor": cursor}, headers=headers[0])
            assert stale.status_code == 404
            users[0].status = UserStatus.BANNED
            await db_session.flush()
            assert (await client.get(path, params=params, headers=headers[0])).status_code == 401
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
        await app.state.redis.aclose()
        await app.state.subscription_redis.aclose()
        await app.state.engine.dispose()


async def test_occasion_owner_changes_and_orders_rotate_only_affected_customer_versions(db_session):
    from app.models import City, Order
    from app.repositories.read_cache_repository import ReadCacheRepository
    from sqlalchemy import select, update

    users = [
        User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER) for _ in range(2)
    ]
    db_session.add_all(users)
    await db_session.flush()
    repository = ReadCacheRepository(db_session)
    keys = [("occasions", user.id) for user in users]
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    before = await repository.versions(keys)
    occasion = Occasion(
        user_id=users[0].id, title="Private", occasion_date=date(2028, 1, 1), reminder_days_before=0
    )
    db_session.add(occasion)
    await db_session.flush()
    created = await repository.versions(keys)
    assert created[0] != before[0] and created[1] == before[1]
    await db_session.execute(
        update(Occasion).where(Occasion.id == occasion.id).values(user_id=users[1].id)
    )
    transferred = await repository.versions(keys)
    assert all(a != b for a, b in zip(created, transferred, strict=True))
    city = await db_session.scalar(select(City).where(City.name == "Riyadh"))
    order = Order(customer_id=users[0].id, city=city, delivery_date=date.today())
    db_session.add(order)
    await db_session.flush()
    ordered = await repository.versions(keys)
    assert ordered[0] != transferred[0] and ordered[1] == transferred[1]


async def test_disjoint_calendar_batches_with_opposite_owner_order_commit(db_connection):
    import asyncio

    from sqlalchemy import delete, update
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = db_connection.engine
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        users = [
            User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
            for _ in range(2)
        ]
        setup.add_all(users)
        await setup.flush()
        rows = [
            Occasion(
                user_id=user.id,
                title="Batch",
                occasion_date=date(2028, 1, 1),
                reminder_days_before=0,
            )
            for user in users
            for _ in range(2)
        ]
        setup.add_all(rows)
        await setup.commit()
        ids = [row.id for row in rows]
        owners = [user.id for user in users]
    ready = [asyncio.Event(), asyncio.Event()]

    async def mutate(slot, ordered_ids):
        async with engine.begin() as writer:
            await writer.execute(
                update(Occasion).where(Occasion.id == ordered_ids[0]).values(title="First mutation")
            )
            ready[slot].set()
            await ready[1 - slot].wait()
            await writer.execute(
                update(Occasion)
                .where(Occasion.id == ordered_ids[1])
                .values(title="Second mutation")
            )

    try:
        async with asyncio.timeout(10):
            outcomes = await asyncio.gather(
                mutate(0, [ids[2], ids[0]]),
                mutate(1, [ids[1], ids[3]]),
                return_exceptions=True,
            )
        assert outcomes == [None, None], [type(item).__name__ for item in outcomes]
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Occasion).where(Occasion.id.in_(ids)))
            await cleanup.execute(delete(User).where(User.id.in_(owners)))
            await cleanup.commit()


async def test_calendar_epochs_commit_rollback_and_truncate_visibility(db_connection):
    from app.repositories.read_cache_repository import ReadCacheRepository
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = db_connection.engine
    user_id = uuid4()
    occasion_id = uuid4()
    keys = [("occasions", user_id)]
    async with AsyncSession(engine) as reader:
        before = await ReadCacheRepository(reader).versions(keys)
        async with AsyncSession(engine) as writer:
            user = User(
                id=user_id, phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER
            )
            writer.add(user)
            await writer.flush()
            writer.add(
                Occasion(
                    id=occasion_id,
                    user_id=user_id,
                    title="Committed",
                    occasion_date=date(2028, 1, 1),
                    reminder_days_before=0,
                )
            )
            await writer.flush()
            assert await ReadCacheRepository(reader).versions(keys) == before
            await writer.commit()
        committed = await ReadCacheRepository(reader).versions(keys)
        assert committed != before
        try:
            async with engine.connect() as writer:
                transaction = await writer.begin()
                await writer.execute(
                    text("UPDATE occasions SET title='Rolled back' WHERE id=:id"),
                    {"id": occasion_id},
                )
                await transaction.rollback()
            assert await ReadCacheRepository(reader).versions(keys) == committed
            global_key = [("occasions_all", UUID(int=0))]
            global_before = await ReadCacheRepository(reader).versions(global_key)
            async with engine.connect() as writer:
                transaction = await writer.begin()
                await writer.execute(text("TRUNCATE occasions CASCADE"))
                async with AsyncSession(bind=writer) as session:
                    assert await ReadCacheRepository(session).versions(global_key) != global_before
                assert await ReadCacheRepository(reader).versions(global_key) == global_before
                await transaction.rollback()
        finally:
            async with engine.begin() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM occasions WHERE id=:id"), {"id": occasion_id}
                )
                await cleanup.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
