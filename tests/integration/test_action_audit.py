"""PostgreSQL action-audit checks; the database fixture skips outside CI."""

from __future__ import annotations

from uuid import uuid4

from app.core.audit_context import mark_request_transaction, set_audit_actor
from app.models import AuditLog, FeaturedGift, User
from app.models.enums import UserRole
from app.repositories.user_repository import UserRepository
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def test_orm_and_core_changes_share_the_actor_transaction(db_session: AsyncSession) -> None:
    """Record row changes without copying values, including direct Core SQL."""
    actor = User(phone=f"audit:{uuid4().hex[:12]}", role=UserRole.ADMIN)
    db_session.add(actor)
    await db_session.flush()
    await mark_request_transaction(db_session)
    await set_audit_actor(db_session, category="ADMIN", actor_user_id=actor.id)

    gift = FeaturedGift(title="Audit test", image_storage_key="audit/test", category="Test")
    db_session.add(gift)
    await db_session.flush()
    await db_session.execute(
        update(FeaturedGift).where(FeaturedGift.id == gift.id).values(title="Changed title")
    )
    await db_session.delete(gift)
    await db_session.flush()

    rows = list(
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "featured_gifts", AuditLog.entity_id == gift.id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )
    assert {row.action for row in rows} == {"CREATE", "UPDATE", "DELETE"}
    assert len(rows) == 3
    assert all(row.actor_user_id == actor.id for row in rows)
    assert all(row.audit_metadata == {"actor_category": "ADMIN"} for row in rows)
    assert "Changed title" not in str([row.audit_metadata for row in rows])


async def test_rolled_back_change_has_no_audit_record(db_session: AsyncSession) -> None:
    """Audit rows roll back with the data they describe."""
    gift_id = uuid4()
    async with db_session.begin_nested() as nested:
        db_session.add(
            FeaturedGift(
                id=gift_id,
                title="Rolled back",
                image_storage_key="audit/rollback",
                category="Test",
            )
        )
        await db_session.flush()
        await nested.rollback()

    assert await db_session.scalar(select(AuditLog.id).where(AuditLog.entity_id == gift_id)) is None


async def test_first_dashboard_admin_insert_has_admin_actor(db_session: AsyncSession) -> None:
    """Bootstrap the first dashboard user without an unattributed request write."""
    await mark_request_transaction(db_session)
    user = await UserRepository(db_session).ensure_dashboard_admin(f"audit-{uuid4()}")
    assert user is not None
    row = await db_session.scalar(
        select(AuditLog).where(AuditLog.entity_type == "users", AuditLog.entity_id == user.id)
    )
    assert row is not None
    assert row.actor_user_id == user.id
    assert row.audit_metadata == {"actor_category": "ADMIN"}
