"""Push fanout reads bounded recipient pages before contacting the provider."""

import uuid

from app.core.config import Environment
from app.integrations.push.fake import FakePushClient
from app.services.notification_service import NotificationService


class _Devices:
    async def tokens_for_user(self, user_id):
        raise AssertionError("push fanout must not read the entire recipient set")

    async def tokens_for_city_couriers(self, city):
        raise AssertionError("push fanout must not read the entire recipient set")

    async def token_page(self, *, user_id=None, city=None, after=None, limit):
        assert limit <= 500
        identifiers = [uuid.UUID(int=index + 1) for index in range(501)]
        return [
            (identifier, f"token-{identifier.int}")
            for identifier in identifiers
            if after is None or identifier > after
        ][:limit]


async def test_notification_fanout_reads_bounded_pages():
    push = FakePushClient(Environment.TEST)
    service = NotificationService(devices=_Devices(), push=push)
    assert await service.notify_user(user_id=uuid.uuid4(), title="t", body="b") == 501
    assert [len(message.tokens) for message in push.sent] == [500, 1]
    push.sent.clear()
    assert await service.notify_city_couriers(city="Jeddah", title="t", body="b") == 501
    assert [len(message.tokens) for message in push.sent] == [500, 1]
