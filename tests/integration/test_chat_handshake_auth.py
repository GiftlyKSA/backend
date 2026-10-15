"""Real local HTTP/WebSocket handshakes; no production writes or provider deliveries."""

import asyncio
import os
import socket
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import uvicorn
from app.core.jwt import create_access_token, decode_access_token
from app.core.security import sha256_hex
from app.main import create_app
from app.models import City, Conversation, CourierProfile, Order, RefreshToken, User
from app.models.enums import OrderStatus, UserRole
from freezegun import freeze_time
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from tests.conftest import make_test_settings


async def test_query_free_native_browser_legacy_and_refreshed_handshakes(db_connection):
    settings = make_test_settings(
        DATABASE_URL=os.environ["DATABASE_URL"], REDIS_URL=os.environ["REDIS_URL"]
    )
    engine = db_connection.engine
    raw_refresh = f"synthetic-refresh-{uuid4()}"
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        users = [
            User(phone=f"+96650{uuid4().int % 10000000:07d}", role=role)
            for role in (UserRole.CUSTOMER, UserRole.COURIER, UserRole.CUSTOMER)
        ]
        setup.add_all(users)
        await setup.flush()
        city_id = await setup.scalar(select(City.id).where(City.name == "Riyadh"))
        setup.add(
            CourierProfile(
                user_id=users[1].id,
                city_of_residence_id=city_id,
                national_id_encrypted="synthetic-fixture",
                is_verified=True,
            )
        )
        order = Order(
            customer_id=users[0].id,
            courier_id=users[1].id,
            delivery_city_id=city_id,
            delivery_date=date.today(),
            status=OrderStatus.COMPLETED,
        )
        setup.add(order)
        await setup.flush()
        conversation = Conversation(
            order_id=order.id, customer_id=users[0].id, courier_id=users[1].id
        )
        setup.add_all(
            [
                conversation,
                RefreshToken(
                    user_id=users[0].id,
                    token_hash=sha256_hex(raw_refresh),
                    family_id=uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            ]
        )
        await setup.commit()
        owner_ids, order_id, conversation_id = (
            [user.id for user in users],
            order.id,
            conversation.id,
        )

    app = create_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    credentials = [
        create_access_token(settings, user_id=user.id, role=user.role.value)[0] for user in users
    ]
    path = f"/api/ws/conversations/{conversation_id}"
    url = f"ws://127.0.0.1:{port}{path}"
    revoked_jti = decode_access_token(settings, credentials[0]).jti

    async def accepted(*, token, transport, extra_protocol=None):
        headers = {"Authorization": "Bearer " + token} if transport == "header" else None
        protocols = (
            ["giftly.chat", "bearer." + token] if transport == "protocol" else extra_protocol
        )
        target = url + "?token=" + token if transport == "legacy" else url
        async with connect(
            target, additional_headers=headers, subprotocols=protocols, open_timeout=5
        ) as connection:
            assert connection.subprotocol == ("giftly.chat" if protocols else None)

    async def rejected(*, headers=None, protocols=None):
        with pytest.raises(InvalidStatus) as failure:
            async with connect(
                url, additional_headers=headers, subprotocols=protocols, open_timeout=5
            ):
                raise AssertionError("An unauthorized handshake was accepted.")
        assert failure.value.response.status_code == 403

    try:
        async with asyncio.timeout(5):
            for _ in range(200):
                if server.started:
                    break
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
            assert server.started
        await accepted(token=credentials[0], transport="header")
        await accepted(token=credentials[1], transport="header")
        await accepted(token=credentials[0], transport="protocol")
        await accepted(token=credentials[0], transport="legacy")
        await accepted(
            token=credentials[0],
            transport="header",
            extra_protocol=["giftly.chat", "bearer.synthetic-invalid"],
        )
        await rejected()
        await rejected(headers={"Authorization": "Bearer synthetic-invalid"})
        await rejected(
            headers={"Authorization": "Basic invalid"},
            protocols=["giftly.chat", "bearer." + credentials[0]],
        )
        await rejected(headers={"Authorization": "Bearer " + credentials[2]})
        await rejected(
            protocols=["giftly.chat", "bearer.synthetic-invalid", "bearer." + credentials[0]]
        )
        with freeze_time(datetime.now(UTC) - timedelta(hours=2)):
            expired = create_access_token(settings, user_id=owner_ids[0], role="CUSTOMER")[0]
        await rejected(headers={"Authorization": "Bearer " + expired})
        await app.state.redis.set(f"jwt:denylist:{revoked_jti}", "1", ex=60)
        await rejected(protocols=["giftly.chat", "bearer." + credentials[0]])
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.post("/api/auth/refresh", json={"refresh_token": raw_refresh})
            assert response.status_code == 200
            refreshed = response.json()["access_token"]
        await accepted(token=refreshed, transport="header")
        await accepted(token=refreshed, transport="protocol")
        async with engine.begin() as change:
            await change.execute(
                update(CourierProfile)
                .where(CourierProfile.user_id == owner_ids[1])
                .values(is_verified=False)
            )
        await rejected(headers={"Authorization": "Bearer " + credentials[1]})
    finally:
        await app.state.redis.delete(f"jwt:denylist:{revoked_jti}")
        server.should_exit = True
        try:
            async with asyncio.timeout(10):
                await serving
        finally:
            listener.close()
            async with engine.begin() as cleanup:
                await cleanup.execute(delete(Order).where(Order.id == order_id))
                await cleanup.execute(delete(User).where(User.id.in_(owner_ids)))
