import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.deps import require_auth
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.jwt import create_access_token
from app.core.ws_connections import _ACQUIRE, _RELEASE, _RENEW
from app.models.enums import UserRole, UserStatus
from app.repositories.chat_repository import ChatRepository
from app.repositories.user_repository import UserRepository
from app.routers import chat
from app.services.auth_service import AuthService
from freezegun import freeze_time
from redis.exceptions import MaxConnectionsError
from starlette.requests import Request

from tests.conftest import make_test_settings


def socket_context(monkeypatch):
    settings = make_test_settings()
    user = SimpleNamespace(
        id=uuid4(),
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
        deleted_at=None,
        auth_version=0,
    )
    token, _, _ = create_access_token(settings, user_id=user.id, role=user.role.value)
    redis = AsyncMock()
    redis.get.return_value = None
    redis.eval.return_value = 0
    session = AsyncMock()
    session.info = {}
    session.add = Mock()
    session.__aenter__.return_value = session
    state = SimpleNamespace(
        settings=settings,
        redis=redis,
        subscription_redis=Mock(),
        session_factory=Mock(return_value=session),
    )
    websocket = SimpleNamespace(
        headers={},
        scope={"subprotocols": []},
        query_params={"token": token},
        app=SimpleNamespace(state=state),
        send_text=AsyncMock(),
        close=AsyncMock(),
        receive_text=AsyncMock(),
    )
    monkeypatch.setattr(UserRepository, "get", AsyncMock(return_value=user))
    conversation = SimpleNamespace(customer_id=user.id, courier_id=uuid4())
    service = SimpleNamespace(get_conversation_for_actor=AsyncMock(return_value=conversation))
    monkeypatch.setattr(chat, "_session_service", Mock(return_value=service))

    async def live_state(_repository, conversation_id, actor_id):
        current = await service.get_conversation_for_actor(
            conversation_id=conversation_id, actor_id=actor_id
        )
        return SimpleNamespace(
            user=user,
            courier_verified=True,
            conversation_id=conversation_id,
            customer_id=current.customer_id,
            courier_id=current.courier_id,
        )

    monkeypatch.setattr(ChatRepository, "get_live_state", live_state)
    return websocket, user, service


@pytest.mark.parametrize("change", ["ban", "delete", "version", "role", "logout", "missing"])
async def test_http_rejects_revoked_credentials(monkeypatch, change):
    websocket, user, _ = socket_context(monkeypatch)
    if change == "ban":
        user.status = UserStatus.BANNED
    elif change == "delete":
        user.deleted_at = datetime.now(UTC)
    elif change == "version":
        user.auth_version += 1
    elif change == "role":
        user.role = UserRole.COURIER
    elif change == "logout":
        websocket.app.state.redis.get.return_value = "1"
    else:
        monkeypatch.setattr(UserRepository, "get", AsyncMock(return_value=None))
    request = Request(
        {
            "type": "http",
            "app": websocket.app,
            "headers": [(b"authorization", f"Bearer {websocket.query_params['token']}".encode())],
        }
    )
    with pytest.raises(UnauthorizedError):
        await require_auth(request, AsyncMock())


@pytest.mark.parametrize("change", ["ban", "delete", "version", "logout", "expiry", "membership"])
async def test_socket_rechecks_before_outbound_delivery(monkeypatch, change):
    websocket, user, service = socket_context(monkeypatch)
    assert await chat._authenticate_ws(websocket) is not None
    if change == "ban":
        user.status = UserStatus.BANNED
    elif change == "delete":
        user.deleted_at = datetime.now(UTC)
    elif change == "version":
        user.auth_version += 1
    elif change == "logout":
        websocket.app.state.redis.get.return_value = "1"
    elif change == "membership":
        service.get_conversation_for_actor.side_effect = NotFoundError()

    async def events():
        yield {"type": "message", "data": "private message"}

    pubsub = SimpleNamespace(listen=events)
    instant = datetime.now(UTC) + (timedelta(hours=1) if change == "expiry" else timedelta())
    with freeze_time(instant), pytest.raises((UnauthorizedError, NotFoundError)):
        await chat._pump_pubsub_to_socket(pubsub, websocket, uuid4())
    websocket.send_text.assert_not_awaited()


async def test_idle_socket_revocation_cancels_reader_and_writer(monkeypatch):
    websocket, user, _ = socket_context(monkeypatch)
    subscribed = asyncio.Event()
    websocket.accept = AsyncMock()
    websocket.receive_text.side_effect = asyncio.Event().wait

    async def events():
        await asyncio.Event().wait()
        yield {}

    pubsub = SimpleNamespace(
        subscribe=AsyncMock(side_effect=lambda channel: subscribed.set()),
        listen=events,
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    websocket.app.state.subscription_redis.pubsub = Mock(return_value=pubsub)
    websocket.app.state.redis.eval.side_effect = lambda script, *args: (
        2 if script == _ACQUIRE else 1 if script == _RENEW else 0
    )
    checked = []

    async def expire_while_idle(delay):
        checked.append(delay)
        user.status = UserStatus.BANNED

    monkeypatch.setattr(chat.asyncio, "sleep", expire_while_idle)
    await asyncio.wait_for(chat.conversation_ws(websocket, uuid4()), timeout=1)
    assert subscribed.is_set() and checked == [5]
    websocket.close.assert_awaited_once_with(code=4401)
    websocket.send_text.assert_not_awaited()
    pubsub.unsubscribe.assert_awaited_once()
    pubsub.aclose.assert_awaited_once()
    websocket.app.state.redis.pubsub.assert_not_called()
    assert any(call.args[0] == _RELEASE for call in websocket.app.state.redis.eval.await_args_list)


@pytest.mark.parametrize("result,code", [(0, 4429), (1, 4429)])
async def test_socket_cap_rejects_before_allocating_pubsub(monkeypatch, result, code):
    websocket, _, _ = socket_context(monkeypatch)
    websocket.app.state.redis.eval.return_value = result
    await chat.conversation_ws(websocket, uuid4())
    websocket.close.assert_awaited_once_with(code=code)
    websocket.app.state.redis.pubsub.assert_not_called()


async def test_socket_cap_fails_closed_on_redis_error(monkeypatch):
    websocket, _, _ = socket_context(monkeypatch)
    websocket.app.state.redis.eval.side_effect = OSError("redis unavailable")
    await chat.conversation_ws(websocket, uuid4())
    websocket.close.assert_awaited_once_with(code=1013)
    websocket.app.state.redis.pubsub.assert_not_called()


async def test_subscription_pool_full_closes_socket_and_releases_lease(monkeypatch):
    websocket, _, _ = socket_context(monkeypatch)
    pubsub = SimpleNamespace(
        subscribe=AsyncMock(side_effect=MaxConnectionsError("Subscription pool full")),
        unsubscribe=AsyncMock(side_effect=MaxConnectionsError("Subscription pool full")),
        aclose=AsyncMock(),
    )
    websocket.app.state.subscription_redis.pubsub = Mock(return_value=pubsub)
    websocket.app.state.redis.eval.side_effect = lambda script, *args: (
        2 if script == _ACQUIRE else 0
    )
    await chat.conversation_ws(websocket, uuid4())
    websocket.close.assert_awaited_once_with(code=1013)
    websocket.app.state.redis.pubsub.assert_not_called()
    pubsub.aclose.assert_awaited_once()
    assert any(call.args[0] == _RELEASE for call in websocket.app.state.redis.eval.await_args_list)


async def test_refresh_replay_revocation_is_committed_before_unauthorized():
    repo, session = AsyncMock(), AsyncMock()
    user_id, family_id = uuid4(), uuid4()
    repo.lock_refresh_owner.return_value = SimpleNamespace(id=user_id)
    repo.get_refresh_token.return_value = SimpleNamespace(
        user_id=user_id,
        revoked_at=None,
        used_at=datetime.now(UTC),
        family_id=family_id,
    )
    sequence = Mock()
    sequence.attach_mock(repo.invalidate_user_credentials, "revoke")
    sequence.attach_mock(session.commit, "commit")
    service = AuthService(
        settings=make_test_settings(),
        redis=AsyncMock(),
        otp=AsyncMock(),
        users=AsyncMock(),
        auth_repo=repo,
        session=session,
    )
    with pytest.raises(UnauthorizedError, match="reuse"):
        await service.refresh("already-used-token")
    assert [entry[0] for entry in sequence.mock_calls] == ["revoke", "commit"]
    repo.add_refresh_token.assert_not_awaited()


@pytest.mark.parametrize("change", ["expired", "banned", "deleted", "owner"])
async def test_invalid_refresh_cannot_issue_successor(change):
    repo, session = AsyncMock(), AsyncMock()
    user = SimpleNamespace(id=uuid4(), status=UserStatus.ACTIVE, deleted_at=None)
    repo.lock_refresh_owner.return_value = user
    row = SimpleNamespace(
        user_id=user.id,
        revoked_at=None,
        used_at=None,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    if change == "expired":
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif change == "banned":
        user.status = UserStatus.BANNED
    elif change == "deleted":
        user.deleted_at = datetime.now(UTC)
    else:
        row.user_id = uuid4()
    repo.get_refresh_token.return_value = row
    service = AuthService(
        settings=make_test_settings(),
        redis=AsyncMock(),
        otp=AsyncMock(),
        users=AsyncMock(),
        auth_repo=repo,
        session=session,
    )
    with pytest.raises(UnauthorizedError):
        await service.refresh("unusable-token")
    repo.add_refresh_token.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_refresh_replay_invalidates_access_across_devices_and_live_socket(monkeypatch):
    from app.core.jwt import decode_access_token
    from app.services.auth_service import validate_access_claims

    websocket, user, _ = socket_context(monkeypatch)
    assert await chat._authenticate_ws(websocket) is not None
    repo, session = AsyncMock(), AsyncMock()
    repo.lock_refresh_owner.return_value = user
    repo.get_refresh_token.return_value = SimpleNamespace(
        user_id=user.id, revoked_at=None, used_at=datetime.now(UTC), family_id=uuid4()
    )

    async def invalidate(user_id, now):
        assert user_id == user.id
        user.auth_version += 1

    repo.invalidate_user_credentials.side_effect = invalidate
    service = AuthService(
        settings=websocket.app.state.settings,
        redis=websocket.app.state.redis,
        otp=AsyncMock(),
        users=AsyncMock(),
        auth_repo=repo,
        session=session,
    )
    other_access, _, _ = create_access_token(
        websocket.app.state.settings, user_id=user.id, role=user.role.value
    )
    with pytest.raises(UnauthorizedError, match="reuse"):
        await service.refresh("already-used-token")
    for access in (websocket.query_params["token"], other_access):
        with pytest.raises(UnauthorizedError):
            await validate_access_claims(
                decode_access_token(websocket.app.state.settings, access),
                redis=websocket.app.state.redis,
                users=UserRepository(session),
            )
    with pytest.raises(UnauthorizedError):
        await chat._require_live_authorization(websocket, uuid4())
    assert user.auth_version == 1
