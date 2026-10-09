"""New-order pushes run after claims commit and outside database sessions."""

from __future__ import annotations

import uuid

import pytest
from app.repositories.order_notification_repository import ClaimedNotification
from app.services import order_notification_service as delivery


class _Session:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory

    async def __aenter__(self) -> _Session:
        self.factory.open_sessions += 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.factory.open_sessions -= 1

    async def commit(self) -> None:
        self.factory.commits += 1


class _Factory:
    def __init__(self) -> None:
        self.open_sessions = 0
        self.commits = 0

    def __call__(self) -> _Session:
        return _Session(self)


class _Repository:
    def __init__(self, claim: ClaimedNotification, page: list[tuple[uuid.UUID, str]]) -> None:
        self.claim = claim
        self.page = page
        self.claimed = False
        self.advanced: tuple[uuid.UUID | None, bool] | None = None
        self.retried = False
        self.page_limit = 0

    async def claim_pending(self, **_kwargs: object) -> list[ClaimedNotification]:
        if self.claimed:
            return []
        self.claimed = True
        return [self.claim]

    async def order_is_open(self, _order_id: uuid.UUID) -> bool:
        return True

    async def token_page(self, **kwargs: object) -> list[tuple[uuid.UUID, str]]:
        self.page_limit = int(kwargs["limit"])
        return self.page

    async def advance(self, _claim: ClaimedNotification, **kwargs: object) -> bool:
        self.advanced = (kwargs["cursor"], bool(kwargs["complete"]))
        return True

    async def retry(self, _claim: ClaimedNotification, **_kwargs: object) -> None:
        self.retried = True


class _Push:
    def __init__(self, factory: _Factory, *, fail: bool = False) -> None:
        self.factory = factory
        self.fail = fail
        self.sent: list[str] = []
        self.data: dict[str, str] | None = None

    async def send_push(
        self, tokens: list[str], _title: str, _body: str, *, data: dict[str, str] | None = None
    ) -> None:
        assert self.factory.open_sessions == 0
        if self.fail:
            raise RuntimeError("provider failed")
        self.sent.extend(tokens)
        self.data = data


@pytest.mark.parametrize("fail", [False, True])
async def test_notification_page_commits_claim_before_push_and_retries_failure(
    monkeypatch: pytest.MonkeyPatch, test_settings: object, fail: bool
) -> None:
    factory = _Factory()
    token_id = uuid.uuid4()
    claim = ClaimedNotification(uuid.uuid4(), uuid.uuid4(), None, uuid.uuid4(), 1)
    repo = _Repository(claim, [(token_id, "courier-token")])
    monkeypatch.setattr(delivery, "OrderNotificationRepository", lambda _session: repo)
    push = _Push(factory, fail=fail)

    processed = await delivery.send_pending_order_notifications(
        limit=1, push=push, factory=factory, settings=test_settings
    )

    assert repo.page_limit == 500
    assert factory.open_sessions == 0
    if fail:
        assert processed == 0
        assert repo.retried
        assert repo.advanced is None
    else:
        assert processed == 1
        assert push.sent == ["courier-token"]
        assert push.data == {"type": "ORDER_AVAILABLE", "order_id": str(claim.order_id)}
        assert repo.advanced == (token_id, True)
        assert factory.commits == 2
