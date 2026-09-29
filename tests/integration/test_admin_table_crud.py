from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, ForbiddenError
from app.models import (
    AdminSession,
    AuditLog,
    Base,
    FeaturedGift,
    RefreshToken,
    Transaction,
    User,
    Wallet,
)
from app.models.enums import TransactionType, UserRole, WalletType
from app.repositories.admin_read_repository import AdminReadRepository
from app.repositories.admin_table_repository import AdminTableRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.services.admin_table_service import AdminTableService
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError

from tests.conftest import make_test_settings


async def admin_service(db_session):
    admin = User(phone=f"admin:{uuid4().hex[:12]}", role=UserRole.ADMIN)
    db_session.add(admin)
    await db_session.flush()
    session = AdminSession(
        admin_user_id=admin.id,
        session_token_hash=uuid4().hex * 2,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.flush()
    service = AdminTableService(
        AdminTableRepository(db_session),
        AuditRepository(db_session),
        make_test_settings(),
        AuthRepository(db_session),
    )
    return service, {"admin_id": admin.id, "session_id": session.id}


async def test_every_table_has_a_form_and_generic_crud_is_audited(db_session):
    service, auth = await admin_service(db_session)
    for name in Base.metadata.tables:
        form = await service.form(name)
        assert form.table_name == name and form.fields
    gift = await service.save(
        "featured_gifts",
        {
            "title": "Test gift",
            "image_storage_key": "test/gift.png",
            "category": "Test",
        },
        **auth,
    )
    form = await service.form("featured_gifts", gift)
    assert next(field.value for field in form.fields if field.name == "title") == "Test gift"
    await service.save(
        "featured_gifts", {"title": "Updated gift"}, record_id=gift, revision=form.revision, **auth
    )
    form = await service.form("featured_gifts", gift)
    assert next(field.value for field in form.fields if field.name == "title") == "Updated gift"
    await service.delete("featured_gifts", gift, revision=form.revision, **auth)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == gift)
        )
        == 3
    )


async def test_browser_cursors_survive_deleted_anchor_and_duplicate_timestamps(db_session):
    marker = uuid4().hex[:8]
    timestamp = datetime.now(UTC)
    rows = [
        FeaturedGift(
            title=f"{marker}-{number}",
            image_storage_key=f"{marker}/{number}",
            category="Test",
            created_at=timestamp,
        )
        for number in range(105)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    repo = AdminReadRepository(db_session)
    first = await repo.list_table_page("featured_gifts")
    assert first is not None and len(first.rows) == 50 and first.next_cursor
    second = await repo.list_table_page("featured_gifts", after=first.next_cursor)
    assert second is not None and len(second.rows) == 50 and second.next_cursor
    third = await repo.list_table_page("featured_gifts", after=second.next_cursor)
    assert third is not None and len(third.rows) >= 5
    assert {row.edit_url for row in first.rows}.isdisjoint({row.edit_url for row in second.rows})
    assert {row.edit_url for row in second.rows}.isdisjoint({row.edit_url for row in third.rows})
    await db_session.execute(delete(FeaturedGift).where(FeaturedGift.id == first.next_cursor))
    await db_session.flush()
    previous = await repo.list_table_page("featured_gifts", before=second.previous_cursor)
    assert previous is not None and previous.rows


async def test_generic_relationship_edit_nullable_clear_and_delete(db_session):
    service, auth = await admin_service(db_session)
    customer = User(phone=f"+966{str(uuid4().int)[:9]}", role=UserRole.CUSTOMER)
    db_session.add(customer)
    await db_session.flush()
    gift_id = await service.save(
        "featured_gifts",
        {"title": "Related gift", "image_storage_key": "test/related.png", "category": "Test"},
        **auth,
    )
    occasion_id = await service.save(
        "occasions",
        {
            "user_id": str(customer.id),
            "title": "Birthday",
            "occasion_date": date.today().isoformat(),
            "featured_gift_id": str(gift_id),
        },
        **auth,
    )
    occasion = await service.form("occasions", occasion_id)
    related = next(field for field in occasion.fields if field.name == "featured_gift_id")
    assert related.selected_label and related.value == str(gift_id)
    await service.save(
        "occasions",
        {"title": "Updated birthday", "null__featured_gift_id": "1"},
        record_id=occasion_id,
        revision=occasion.revision,
        **auth,
    )
    updated = await service.form("occasions", occasion_id)
    assert next(field.value for field in updated.fields if field.name == "featured_gift_id") == ""
    await service.delete("occasions", occasion_id, revision=updated.revision, **auth)
    gift = await service.form("featured_gifts", gift_id)
    await service.delete("featured_gifts", gift_id, revision=gift.revision, **auth)


async def test_table_phone_change_invalidates_refresh_access_and_dashboard_sessions(db_session):
    service, auth = await admin_service(db_session)
    victim = User(phone=f"admin:{uuid4().hex[:12]}", role=UserRole.ADMIN)
    db_session.add(victim)
    await db_session.flush()
    refresh = RefreshToken(
        user_id=victim.id,
        token_hash=uuid4().hex * 2,
        family_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    dashboard = AdminSession(
        admin_user_id=victim.id,
        session_token_hash=uuid4().hex * 2,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([refresh, dashboard])
    await db_session.flush()
    form = await service.form("users", victim.id)
    assert "auth_version" not in {field.name for field in form.fields}
    await service.save(
        "users",
        {"phone": f"new:{uuid4().hex[:15]}"},
        record_id=victim.id,
        revision=form.revision,
        **auth,
    )
    await db_session.refresh(victim)
    await db_session.refresh(refresh)
    await db_session.refresh(dashboard)
    assert victim.auth_version == 1
    assert refresh.revoked_at is not None
    assert dashboard.revoked_at is not None


async def test_unique_choice_is_enforced_during_save_and_failed_write_is_not_audited(db_session):
    service, auth = await admin_service(db_session)
    user = User(phone=f"+966{str(uuid4().int)[:9]}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    await service.save("wallets", {"user_id": str(user.id), "type": "CUSTOMER"}, **auth)
    with pytest.raises(ConflictError):
        await service.save("wallets", {"user_id": str(user.id), "type": "CUSTOMER"}, **auth)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "ADMIN_TABLE_CREATE", AuditLog.actor_user_id == auth["admin_id"]
            )
        )
        == 1
    )
    assert not await db_session.scalar(select(func.giftly_admin_maintenance_allowed()))


async def test_ledger_override_is_scoped_and_requires_a_valid_session(db_session):
    service, auth = await admin_service(db_session)
    user = User(phone=f"+966{str(uuid4().int)[:9]}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    wallet = Wallet(user_id=user.id, type=WalletType.CUSTOMER)
    db_session.add(wallet)
    await db_session.flush()
    entry = Transaction(
        wallet_id=wallet.id,
        amount=1,
        balance_after=1,
        correlation_id=uuid4(),
        type=TransactionType.TOPUP,
    )
    db_session.add(entry)
    await db_session.flush()
    form = await service.form("transactions", entry.id)
    with pytest.raises(ForbiddenError):
        await service.delete(
            "transactions",
            entry.id,
            admin_id=auth["admin_id"],
            session_id=uuid4(),
            revision=form.revision,
        )
    assert not await db_session.scalar(select(func.giftly_admin_maintenance_allowed()))
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                Transaction.__table__.delete().where(Transaction.id == entry.id)
            )
    await service.delete("transactions", entry.id, revision=form.revision, **auth)
    assert not await db_session.scalar(select(func.giftly_admin_maintenance_allowed()))
