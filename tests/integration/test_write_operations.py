"""Database claims serialize retries and never expose another owner's result."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import ConflictError
from app.models import User, WriteOperation
from app.models.enums import UserRole
from app.repositories.operation_repository import OperationRepository
from app.services.operation_service import OperationService
from pydantic import BaseModel
from sqlalchemy import func, select

from tests.integration.test_chat_service import _settings


class Result(BaseModel):
    value: str


async def test_claim_replay_ownership_and_cleanup(db_session):
    owner = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(owner)
    await db_session.flush()
    repo = OperationRepository(db_session)
    service = OperationService(repo, _settings())
    key = uuid4()
    row = await service.begin(owner.id, "occasion.create", key, {"text": "private"})
    # No resource binding here: this exercises claim persistence separately from CRUD.
    row.result_encrypted = service._cipher.encrypt(
        Result(value="private").model_dump_json(), f"write_operations:result:{row.id}"
    )
    await repo.save(row)
    replay = await service.begin(owner.id, "occasion.create", key, {"text": "private"})
    assert service.result(replay) == '{"value":"private"}'
    assert await service.find(uuid4(), "occasion.create", key) is None
    with pytest.raises(ConflictError):
        await service.begin(owner.id, "occasion.create", key, {"text": "changed"})
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    pending = await service.begin(owner.id, "order.create", uuid4(), {"text": "pending"})
    pending.expires_at = row.expires_at
    await db_session.flush()
    assert await repo.purge(datetime.now(UTC), limit=1) == 1
    assert await repo.get(owner.id, "occasion.create", key) is None
    assert await repo.get(owner.id, "order.create", pending.operation_key) is not None


async def test_independent_sessions_replay_once():
    engine = build_engine(_settings())
    factory = build_session_factory(engine)
    owner_id, key = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            User(id=owner_id, phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
        )
        await session.commit()
    created = 0

    async def submit():
        nonlocal created
        async with factory() as session:
            service = OperationService(OperationRepository(session), _settings())
            row = await service.begin(owner_id, "order.create", key, {"text": "same"})
            if row.result_encrypted is None:
                created += 1
                await asyncio.sleep(0.02)
                row.result_encrypted = service._cipher.encrypt(
                    Result(value="one").model_dump_json(), f"write_operations:result:{row.id}"
                )
                await session.flush()
            value = service.result(row)
            await session.commit()
            return value

    try:
        results = await asyncio.gather(*(submit() for _ in range(6)))
        assert len(set(results)) == 1 and created == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WriteOperation)
                    .where(WriteOperation.user_id == owner_id)
                )
                == 1
            )
    finally:
        async with factory() as session:
            from sqlalchemy import delete

            await session.execute(delete(WriteOperation).where(WriteOperation.user_id == owner_id))
            await session.execute(delete(User).where(User.id == owner_id))
            await session.commit()
        await engine.dispose()
