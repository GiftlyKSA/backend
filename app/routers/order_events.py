"""Read-only, authenticated order status WebSockets."""

import asyncio
import contextlib
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio.client import PubSub

from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.jwt import JwtError, decode_access_token
from app.core.ws_connections import WebSocketLease
from app.schemas.order_events import OrderStatusEvent
from app.services.order_realtime_service import OrderRealtimeService, order_channel

router = APIRouter(prefix="/api", tags=["orders"])
logger = logging.getLogger(__name__)


def _token(websocket: WebSocket) -> str:
    header = websocket.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    for protocol in websocket.scope.get("subprotocols", []):
        if protocol.startswith("bearer."):
            return str(protocol[7:])
    return ""


@router.websocket("/ws/orders/{order_id}")
async def order_ws(websocket: WebSocket, order_id: UUID) -> None:
    """Send snapshots to current participants, with bounded shared connection leases."""
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if settings.is_production and origin is not None and origin not in settings.cors_origins:
        await websocket.close(code=4403)
        return
    token = _token(websocket)
    if not token or len(token) > 8192:
        await websocket.close(code=4401)
        return
    service = OrderRealtimeService(
        settings, websocket.app.state.redis, websocket.app.state.session_factory
    )
    lease: WebSocketLease | None = None
    try:
        async with asyncio.timeout(5):
            await service.snapshot(order_id, token)
        claims = decode_access_token(settings, token)
        lease = WebSocketLease(websocket.app.state.redis, UUID(claims.sub))
        if not await lease.acquire():
            await websocket.close(code=4429)
            return
        await _serve(websocket, order_id, token, service, lease)
    except (UnauthorizedError, JwtError):
        await websocket.close(code=4401)
    except (ForbiddenError, NotFoundError):
        await websocket.close(code=4403)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("Order live connection could not continue", exc_info=True)
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.close(code=1013)
    finally:
        if lease is not None:
            with contextlib.suppress(Exception):
                await lease.release()


async def _serve(
    websocket: WebSocket,
    order_id: UUID,
    token: str,
    service: OrderRealtimeService,
    lease: WebSocketLease,
) -> None:
    """Subscribe before loading state so acceptance during connection is not missed."""
    pubsub = websocket.app.state.subscription_redis.pubsub()
    tasks: list[asyncio.Task[None]] = []
    try:
        async with asyncio.timeout(5):
            await pubsub.subscribe(order_channel(order_id))
        async with asyncio.timeout(5):
            snapshot = await service.snapshot(order_id, token)
        protocols = websocket.scope.get("subprotocols", [])
        await websocket.accept(
            subprotocol="giftly.orders" if "giftly.orders" in protocols else None
        )
        async with asyncio.timeout(5):
            await websocket.send_text(snapshot.model_dump_json())
        tasks = [
            asyncio.create_task(
                _updates(websocket, order_id, token, service, lease, snapshot, pubsub)
            ),
            asyncio.create_task(_receive(websocket)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            async with asyncio.timeout(3):
                await pubsub.unsubscribe(order_channel(order_id))
        with contextlib.suppress(Exception):
            async with asyncio.timeout(3):
                await pubsub.aclose()


async def _updates(
    websocket: WebSocket,
    order_id: UUID,
    token: str,
    service: OrderRealtimeService,
    lease: WebSocketLease,
    snapshot: OrderStatusEvent,
    pubsub: PubSub,
) -> None:
    """Use Redis hints for instant updates and periodically reconcile missed hints."""
    loop = asyncio.get_running_loop()
    next_check = loop.time() + 5
    while True:
        event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if event is None and loop.time() < next_check:
            continue
        async with asyncio.timeout(5):
            current = await service.snapshot(order_id, token)
            if loop.time() >= next_check:
                if not await lease.renew():
                    raise UnauthorizedError()
                next_check = loop.time() + 5
        if current != snapshot:
            snapshot = current
            updated = current.model_copy(update={"type": "order.updated"})
            async with asyncio.timeout(5):
                await websocket.send_text(updated.model_dump_json())


async def _receive(websocket: WebSocket) -> None:
    """Reject application frames; this stream does not accept order mutations."""
    frame = await websocket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    await websocket.close(code=4400)
