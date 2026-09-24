from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, ForbiddenError
from app.models import AdminSession, AuditLog, Base, RefreshToken, Transaction, User, Wallet
from app.models.enums import TransactionType, UserRole, WalletType
from app.repositories.admin_table_repository import AdminTableRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.services.admin_table_service import AdminTableService
from sqlalchemy import func, select
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
