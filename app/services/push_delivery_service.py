"""Worker-only Expo sends with durable per-device outcomes and shared backpressure."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.push.expo import ExpoOutcome, ExpoPushClient, ExpoSendError
from app.repositories.push_delivery_repository import PushDeliveryRepository

_logger = logging.getLogger(__name__)
_MAX_BATCHES = 5
_MAX_SENDS_PER_SECOND = 500
_SEND_TIMEOUT_SECONDS = 20
_RATE_LUA = """
local now = redis.call('TIME')
local millis = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', millis - 1000)
if redis.call('ZCARD', KEYS[1]) + tonumber(ARGV[1]) > tonumber(ARGV[2]) then return 0 end
for i = 1, tonumber(ARGV[1]) do
 redis.call('ZADD', KEYS[1], millis, ARGV[3] .. ':' .. i)
end
redis.call('EXPIRE', KEYS[1], 2)
return 1
"""
_COPY = {
    "ORDER_AVAILABLE": (
        "New gift request nearby",
        "A customer just posted a new order in your city.",
    ),
    "CHAT_MESSAGE": ("New message", "You have a new message about your order."),
    "OCCASION_REMINDER": ("Occasion reminder", "You have an upcoming saved occasion."),
}


async def deliver_pushes(
    factory: async_sessionmaker[AsyncSession],
    client: ExpoPushClient,
    redis: Redis,
) -> int:
    """Bound each invocation to five batches, without holding a transaction over I/O."""
    processed = 0
    for _ in range(_MAX_BATCHES):
        async with factory() as session:
            repository = PushDeliveryRepository(session)
            claims = await repository.claim(now=datetime.now(UTC))
            retired = repository.retired_count
            await session.commit()
        if not claims:
            if retired:
                continue
            break
        retry_after = 0.0
        try:
            async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
                allowed = await redis.eval(
                    _RATE_LUA,
                    1,
                    "giftly:expo:send-budget",
                    len(claims),
                    _MAX_SENDS_PER_SECOND,
                    str(uuid.uuid4()),
                )
                if not allowed:
                    raise ExpoSendError("RateDeferred", retry_after=1)
                messages: list[dict[str, str | dict[str, str]]] = []
                for claim in claims:
                    title, body = _COPY[claim.event.event_type]
                    messages.append(
                        {
                            "to": claim.device.token,
                            "title": title,
                            "body": body,
                            "sound": "default",
                            "data": claim.event.wire_data(),
                        }
                    )
                outcomes = await client.send_messages(messages)
        except ExpoSendError as exc:
            retry_after = exc.retry_after
            outcomes = [ExpoOutcome(status="error", details={"error": exc.code}) for _ in claims]
        except RedisError:
            outcomes = [
                ExpoOutcome(status="error", details={"error": "CoordinationUnavailable"})
                for _ in claims
            ]
        except TimeoutError, ConnectionError:
            outcomes = [
                ExpoOutcome(status="error", details={"error": "ProviderUnavailable"})
                for _ in claims
            ]
        async with factory() as session:
            await PushDeliveryRepository(session).finish(
                claims, outcomes, now=datetime.now(UTC), retry_after=retry_after
            )
            await session.commit()
        processed += len(claims)
        failed = sum(outcome.status == "error" for outcome in outcomes)
        age = max(
            (
                (datetime.now(UTC) - claim.queued_at).total_seconds()
                for claim in claims
                if claim.queued_at is not None
            ),
            default=0,
        )
        invalid = sum(outcome.details.get("error") == "DeviceNotRegistered" for outcome in outcomes)
        _logger.info(
            "Push batch processed: targets=%d, provider_errors=%d, invalid_devices=%d, "
            "oldest_age_seconds=%.0f, max_attempt=%d.",
            len(claims),
            failed,
            invalid,
            age,
            max(claim.attempts for claim in claims),
        )
    return processed
