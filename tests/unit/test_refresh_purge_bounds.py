"""Expired refresh cleanup must bound database work per transaction."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from app.models import RefreshToken
from app.repositories.auth_repository import AuthRepository
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_refresh_purge_uses_ordered_limited_ids() -> None:
    session = AsyncMock()
    session.execute.return_value.rowcount = 1000
    deleted = await AuthRepository(session).purge_expired(before=datetime.now(UTC), limit=1000)
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert deleted == 1000
    assert "LIMIT" in sql and "ORDER BY" in sql
    assert "expires_at" in sql and "SKIP LOCKED" in sql
    assert "idx_refresh_tokens_expiry" in {index.name for index in RefreshToken.__table__.indexes}


@pytest.mark.asyncio
async def test_purge_commits_between_full_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import expiry

    purge = AsyncMock(side_effect=[1000, 400])
    monkeypatch.setattr(AuthRepository, "purge_expired", purge)
    session = AsyncMock()
    session.__aenter__.return_value = session
    factory = Mock(return_value=session)

    deleted = await expiry.purge_refresh_tokens(factory=factory, settings=make_test_settings())

    assert deleted == 1400
    assert session.commit.await_count == 2
    assert purge.await_count == 2
