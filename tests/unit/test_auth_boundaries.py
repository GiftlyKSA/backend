import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.deps import require_auth
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.jwt import create_access_token
from app.models.enums import UserRole, UserStatus
from app.repositories.user_repository import UserRepository
from app.routers import chat
from app.services.auth_service import AuthService
from freezegun import freeze_time
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
    state = SimpleNamespace(
        settings=settings, redis=redis, session_factory=Mock(return_value=AsyncMock())
    )
    websocket = SimpleNamespace(
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
    websocket.app.state.redis.pubsub = Mock(return_value=pubsub)
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
    sequence.attach_mock(repo.revoke_family, "revoke")
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
