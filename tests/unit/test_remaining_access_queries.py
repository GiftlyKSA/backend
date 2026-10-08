from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models.enums import OrderStatus, UserRole, UserStatus
from app.repositories.chat_repository import ChatRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from sqlalchemy.dialects import postgresql


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


async def test_chat_live_projection_is_one_owned_query_without_private_columns():
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    assert await ChatRepository(session).get_live_state(uuid4(), uuid4()) is None
    statement = sql(session.execute.await_args.args[0])
    assert session.execute.await_count == 1
    assert "LEFT OUTER JOIN conversations" in statement
    assert "conversations.customer_id" in statement and "conversations.courier_id" in statement
    assert "auth_version" in statement and "deleted_at" in statement
    assert "is_verified" in statement
    assert "cities" not in statement and "phone" not in statement
    assert "national_id" not in statement and "last_message_preview" not in statement


async def test_account_and_verification_projections_do_not_load_profile_or_city():
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    session.scalar.return_value = None
    assert await UserRepository(session).get_account(uuid4()) is None
    statement = sql(session.execute.await_args.args[0])
    assert "auth_version" in statement and "phone" not in statement
    assert await CourierRepository(session).is_verified(uuid4()) is False
    statement = sql(session.scalar.await_args.args[0])
    assert "is_verified" in statement and "cities" not in statement
    assert "national_id" not in statement


@pytest.mark.parametrize("owner", ["customer", "courier", "city"])
@pytest.mark.parametrize("count", [1, 100])
async def test_order_cursor_projects_only_keys_preserving_list_predicates(owner, count):
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = Mock(
        one_or_none=Mock(
            return_value=SimpleNamespace(created_at=datetime(2026, 10, 1, tzinfo=UTC), id=uuid4())
        )
    )
    session.scalars.return_value = [object()] * count
    repository = OrderRepository(session)
    owner_id, cursor = uuid4(), uuid4()
    if owner == "city":
        result = await repository.list_available(owner_id, limit=count, before_id=cursor)
    else:
        method = getattr(repository, f"list_for_{owner}")
        result = await method(
            owner_id,
            status=OrderStatus.NEW,
            limit=count,
            before_id=cursor,
            from_date=date(2026, 9, 1),
            to_date=date(2026, 11, 1),
        )
    assert len(result) == count
    assert session.execute.await_count == 1 and session.scalars.await_count == 1
    anchor = sql(session.execute.await_args.args[0])
    page = sql(session.scalars.await_args.args[0])
    assert anchor.startswith("SELECT orders.created_at, orders.id ")
    assert "delivery_address_note" not in anchor and "cities" not in anchor
    field = "delivery_city_id" if owner == "city" else f"{owner}_id"
    assert field in anchor and field in page
    assert "orders.status =" in anchor and "orders.status =" in page
    if owner != "city":
        assert "orders.delivery_date >=" in anchor and "orders.delivery_date <=" in anchor
    assert "(orders.created_at, orders.id) <" in page
    assert "ORDER BY orders.created_at DESC, orders.id DESC" in page


async def test_missing_order_cursor_still_raises_without_fetching_page():
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    session.scalar.return_value = None
    with pytest.raises(NotFoundError, match="Pagination cursor not found in this list"):
        await OrderRepository(session).list_available(uuid4(), limit=20, before_id=uuid4())
    session.scalars.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "missing",
        "ban",
        "delete",
        "role",
        "version",
        "logout",
        "rejected",
        "unverified",
        "membership",
    ],
)
async def test_compact_chat_live_check_preserves_current_authorization(monkeypatch, change):
    from app.core.exceptions import ForbiddenError, UnauthorizedError
    from app.core.jwt import create_access_token
    from app.models.enums import UserRole, UserStatus
    from app.routers import chat

    from tests.conftest import make_test_settings

    settings = make_test_settings()
    actor_id, conversation_id = uuid4(), uuid4()
    user = SimpleNamespace(
        id=actor_id,
        role=UserRole.COURIER,
        status=UserStatus.ACTIVE,
        deleted_at=None,
        auth_version=0,
    )
    state = SimpleNamespace(
        user=user,
        courier_verified=True,
        conversation_id=conversation_id,
        customer_id=uuid4(),
        courier_id=actor_id,
    )
    token, _, _ = create_access_token(settings, user_id=actor_id, role="COURIER")
    redis = AsyncMock()
    redis.get.return_value = "1" if change == "logout" else None
    if change == "ban":
        user.status = UserStatus.BANNED
    elif change == "delete":
        user.deleted_at = datetime.now(UTC)
    elif change == "role":
        user.role = UserRole.CUSTOMER
    elif change == "version":
        user.auth_version = 1
    elif change == "rejected":
        user.status = UserStatus.REJECTED
    elif change == "unverified":
        state.courier_verified = False
    elif change == "membership":
        state.conversation_id = None
    session = AsyncMock()
    session.info = {}
    session.__aenter__.return_value = session
    repository = AsyncMock(return_value=None if change == "missing" else state)
    monkeypatch.setattr(ChatRepository, "get_live_state", repository, raising=False)
    websocket = SimpleNamespace(
        query_params={"token": token},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings, redis=redis, session_factory=Mock(return_value=session)
            )
        ),
    )
    if change == "none":
        assert await chat._authenticate_ws(websocket, conversation_id) is not None
        repository.assert_awaited_once_with(conversation_id, actor_id)
    else:
        with pytest.raises((UnauthorizedError, ForbiddenError, NotFoundError)):
            await chat._require_live_authorization(websocket, conversation_id)


async def test_live_projection_maps_current_account_and_membership_fields():
    actor_id, conversation_id, customer_id = uuid4(), uuid4(), uuid4()
    session = AsyncMock()
    session.info = {}
    row = (
        actor_id,
        UserRole.COURIER,
        UserStatus.ACTIVE,
        None,
        7,
        True,
        conversation_id,
        customer_id,
        actor_id,
    )
    session.execute.return_value = Mock(one_or_none=Mock(return_value=row))
    state = await ChatRepository(session).get_live_state(conversation_id, actor_id)
    assert state is not None
    assert state.user.id == actor_id and state.user.auth_version == 7
    assert state.user.role is UserRole.COURIER and state.user.status is UserStatus.ACTIVE
    assert state.user.deleted_at is None and state.courier_verified
    assert (state.conversation_id, state.customer_id, state.courier_id) == (
        conversation_id,
        customer_id,
        actor_id,
    )


async def test_eligibility_uses_only_current_account_and_verification_columns():
    from app.services.courier_eligibility_service import CourierEligibilityService

    actor_id = uuid4()
    session = AsyncMock()
    session.info = {}
    session.execute.return_value = Mock(
        one_or_none=Mock(return_value=(actor_id, UserRole.COURIER, UserStatus.ACTIVE, None, 0))
    )
    session.scalar.return_value = True
    service = CourierEligibilityService(
        users=UserRepository(session), couriers=CourierRepository(session)
    )
    await service.require_courier(actor_id)
    assert session.execute.await_count == 1 and session.scalar.await_count == 1
    assert "phone" not in sql(session.execute.await_args.args[0])
    assert "cities" not in sql(session.scalar.await_args.args[0])


async def test_read_only_auth_and_eligibility_reuse_one_account_snapshot():
    from app.services.courier_eligibility_service import CourierEligibilityService

    actor_id = uuid4()
    session = AsyncMock()
    session.info = {}
    session.info = {"read_only_request": True}
    session.execute.return_value = Mock(
        one_or_none=Mock(return_value=(actor_id, UserRole.COURIER, UserStatus.ACTIVE, None, 0))
    )
    transaction = Mock()
    session.sync_session = Mock(get_transaction=Mock(return_value=transaction))
    session.get.return_value = SimpleNamespace(
        id=actor_id,
        role=UserRole.COURIER,
        status=UserStatus.ACTIVE,
        deleted_at=None,
        auth_version=0,
    )
    session.scalar.return_value = True
    users = UserRepository(session)
    assert await users.get(actor_id) is not None
    await CourierEligibilityService(
        users=users, couriers=CourierRepository(session)
    ).require_courier(actor_id)
    assert session.get.await_count == 1
    session.execute.assert_not_awaited()
    assert session.scalar.await_count == 1
