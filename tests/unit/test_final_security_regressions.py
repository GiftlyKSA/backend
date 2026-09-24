"""Regression checks for the final review findings without external services."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.crypto import build_aad, build_cipher
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    InvalidStateTransitionError,
    NotFoundError,
    UnauthorizedError,
    ValidationDomainError,
)
from app.core.jwt import create_access_token, decode_access_token
from app.core.security import hmac_hex
from app.models.enums import OrderStatus, PaymentPurpose, UserRole, UserStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.routers.auth import logout as logout_route
from app.services.auth_service import AuthService, validate_access_claims
from app.services.fulfillment_service import FulfillmentService
from app.services.invoice_service import InvoiceService, NewInvoiceInput
from app.services.order_service import NewOrderInput, OrderService
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings
from tests.unit.test_admin_table_service import setup_service


async def test_logout_locks_owner_before_revoking_all_credentials():
    users, repo, redis = AsyncMock(), AsyncMock(), AsyncMock()
    sequence = Mock()
    sequence.attach_mock(users.get_for_update, "lock")
    sequence.attach_mock(repo.invalidate_user_credentials, "revoke")
    service = AuthService(
        settings=make_test_settings(),
        redis=redis,
        otp=AsyncMock(),
        users=users,
        auth_repo=repo,
        session=AsyncMock(),
    )
    actor_id = uuid4()
    await service.logout(user_id=actor_id, jti="current-token", remaining_ttl_seconds=30)
    assert [call[0] for call in sequence.mock_calls] == ["lock", "revoke"]
    assert repo.invalidate_user_credentials.call_args.args[0] == actor_id


async def test_logout_does_not_return_success_before_revocation_commits(monkeypatch):
    from app.core.deps import Actor

    settings = make_test_settings()
    user_id = uuid4()
    token, jti, _ = create_access_token(settings, user_id=user_id, role="CUSTOMER")
    request = SimpleNamespace(
        headers={"Authorization": f"Bearer {token}"},
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
    )
    service = SimpleNamespace(logout=AsyncMock())
    monkeypatch.setattr("app.routers.auth._service", lambda *_: service)
    session = AsyncMock()
    session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await logout_route(
            request,
            session,
            Actor(id=user_id, role=UserRole.CUSTOMER, jti=jti),
        )
    service.logout.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("changed", ["passport", "clear_national"])
async def test_admin_identity_edit_uses_final_canonical_document(changed):
    service, repo, _, settings = setup_service()
    identifier = uuid4()
    cipher = build_cipher(settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION)
    old = {
        "national_id_encrypted": cipher.encrypt(
            "national", build_aad("courier_profiles", "national_id", str(identifier))
        ),
        "passport_id_encrypted": cipher.encrypt(
            "passport", build_aad("courier_profiles", "passport_id", str(identifier))
        ),
    }
    repo.get_record.side_effect = [
        {"role": "ADMIN", "status": "ACTIVE", "deleted_at": None},
        old,
    ]
    submitted = (
        {"passport_id_encrypted": " replacement "}
        if changed == "passport"
        else {"null__national_id_encrypted": "1"}
    )
    await service.save(
        "courier_profiles",
        submitted,
        admin_id=uuid4(),
        session_id=uuid4(),
        record_id=identifier,
        revision=service._revision(old),
    )
    values = repo.save.call_args.args[2]
    expected = "national" if changed == "passport" else "passport"
    assert values["identity_fingerprint"] == hmac_hex(
        expected, settings.IDENTITY_FINGERPRINT_PEPPER.get_secret_value()
    )


def order_service():
    return OrderService(
        session=AsyncMock(),
        orders=AsyncMock(),
        couriers=AsyncMock(),
        eligibility=AsyncMock(),
        media=AsyncMock(),
        messages=AsyncMock(),
        ratings=AsyncMock(),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )


async def test_cancel_checks_locked_state_after_concurrent_payment():
    service = order_service()
    service._orders.get_for_actor.return_value = SimpleNamespace(status=OrderStatus.WAITING_PAYMENT)
    service._orders.lock_for_actor.return_value = SimpleNamespace(status=OrderStatus.IN_PROGRESS)
    with pytest.raises(InvalidStateTransitionError):
        await service.cancel_order(order_id=uuid4(), actor_id=uuid4(), reason=None)
    service._orders.flush.assert_not_awaited()


async def test_invoice_creation_checks_locked_state_after_cancellation():
    orders, invoices = AsyncMock(), AsyncMock()
    actor = uuid4()
    orders.get_for_actor.return_value = SimpleNamespace(status=OrderStatus.ASSIGNED)
    orders.lock_for_actor.return_value = SimpleNamespace(
        status=OrderStatus.CANCELLED, courier_id=actor
    )
    service = InvoiceService(
        orders=orders,
        invoices=invoices,
        promos=AsyncMock(),
        eligibility=AsyncMock(),
        reservations=AsyncMock(),
        settings=make_test_settings(),
    )
    with pytest.raises(InvalidStateTransitionError):
        await service.create_invoice(
            order_id=uuid4(),
            courier_id=actor,
            data=NewInvoiceInput(items=[], courier_fee_amount=Decimal("10"), promo_code=None),
        )
    invoices.create.assert_not_awaited()


async def test_create_quota_is_checked_under_actor_lock():
    service = order_service()

    async def count(actor):
        service._orders.lock_actor.assert_awaited_once_with(actor)
        return 5

    service._orders.count_customer_active.side_effect = count
    with pytest.raises(ConflictError):
        await service.create_order(
            customer_id=uuid4(),
            data=NewOrderInput(
                description=None,
                delivery_city="Jeddah",
                latitude=21.5,
                longitude=39.2,
                delivery_date=date.today(),
                request_media_keys=[],
            ),
        )
    service._orders.create.assert_not_awaited()


async def test_locked_order_reload_refreshes_existing_identity_map_state():
    session = AsyncMock()
    await OrderRepository(session).lock(uuid4())
    statement = session.scalar.call_args.args[0]
    assert statement.get_execution_options().get("populate_existing") is True


async def test_topup_expiry_filters_purpose_before_limit():
    session = AsyncMock()
    session.scalars.return_value = []
    await PaymentRepository(session).list_expired_new(now=datetime.now(UTC), limit=200)
    statement = session.scalars.call_args.args[0]
    sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert f"payment_intents.purpose = '{PaymentPurpose.WALLET_TOPUP.value}'" in sql
    assert sql.index("payment_intents.purpose") < sql.index("LIMIT")


async def test_cancel_rechecks_participation_under_lock():
    service = order_service()
    service._orders.get_for_actor.return_value = SimpleNamespace(status=OrderStatus.ASSIGNED)
    service._orders.lock_for_actor.return_value = None
    with pytest.raises(NotFoundError):
        await service.cancel_order(order_id=uuid4(), actor_id=uuid4(), reason=None)
    service._orders.flush.assert_not_awaited()


async def test_logout_invalidates_other_device_access_and_refresh():
    settings = make_test_settings()
    user = SimpleNamespace(
        id=uuid4(),
        auth_version=0,
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
        deleted_at=None,
    )
    users, repo, redis = AsyncMock(), AsyncMock(), AsyncMock()
    users.get.return_value = users.get_for_update.return_value = user
    redis.get.return_value = None
    refresh = SimpleNamespace(
        user_id=user.id,
        family_id=uuid4(),
        revoked_at=None,
        used_at=None,
    )
    repo.lock_refresh_owner.return_value = user
    repo.get_refresh_token.return_value = refresh

    async def invalidate(identifier, when):
        assert identifier == user.id
        user.auth_version += 1
        refresh.revoked_at = when

    repo.invalidate_user_credentials.side_effect = invalidate
    service = AuthService(
        settings=settings,
        redis=redis,
        otp=AsyncMock(),
        users=users,
        auth_repo=repo,
        session=AsyncMock(),
    )
    other_access, _, _ = create_access_token(settings, user_id=user.id, role=user.role.value)
    claims = decode_access_token(settings, other_access)
    await validate_access_claims(claims, redis=redis, users=users)
    await service.logout(user_id=user.id, jti="logging-out-device", remaining_ttl_seconds=30)
    with pytest.raises(UnauthorizedError):
        await validate_access_claims(claims, redis=redis, users=users)
    with pytest.raises(UnauthorizedError):
        await service.refresh("other-device-refresh")
    repo.add_refresh_token.assert_not_awaited()


async def test_accept_quota_is_checked_under_actor_lock():
    service = order_service()

    async def count(actor):
        service._orders.lock_actor.assert_awaited_once_with(actor)
        return 3

    service._orders.count_courier_active.side_effect = count
    with pytest.raises(ForbiddenError, match="maximum"):
        await service.accept_order(order_id=uuid4(), courier_id=uuid4())
    service._orders.lock.assert_not_awaited()


async def test_quota_lock_does_not_block_foreign_key_references():
    session = AsyncMock()
    await OrderRepository(session).lock_actor(uuid4())
    statement = session.scalar.call_args.args[0]
    assert "FOR NO KEY UPDATE" in str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("method", ["submit_delivery", "approve_order", "raise_dispute"])
async def test_fulfillment_revalidates_current_participation(method):
    orders = AsyncMock()
    orders.get_for_actor.return_value = SimpleNamespace(status=OrderStatus.IN_PROGRESS)
    orders.lock_for_actor.return_value = None
    service = FulfillmentService(
        orders=orders,
        invoices=AsyncMock(),
        disputes=AsyncMock(),
        wallets=AsyncMock(),
        money=AsyncMock(),
        media=AsyncMock(),
        settings=make_test_settings(),
    )
    arguments = {"order_id": uuid4()}
    if method == "submit_delivery":
        arguments.update(courier_id=uuid4(), data=None)
    elif method == "approve_order":
        arguments.update(customer_id=uuid4())
    else:
        arguments.update(actor_id=uuid4(), reason="test")
    with pytest.raises(NotFoundError):
        await getattr(service, method)(**arguments)
    service._orders.flush.assert_not_awaited()


@pytest.mark.parametrize(
    "national,passport,expected",
    [
        (" national ", None, "national"),
        (None, " passport ", "passport"),
        (" national ", "passport", "national"),
    ],
)
async def test_admin_profile_creation_derives_identity(national, passport, expected):
    service, repo, _, settings = setup_service()
    submitted = {"user_id": str(uuid4()), "city_of_residence": "Jeddah"}
    if national:
        submitted["national_id_encrypted"] = national
    if passport:
        submitted["passport_id_encrypted"] = passport
    await service.save("courier_profiles", submitted, admin_id=uuid4(), session_id=uuid4())
    assert repo.save.call_args.args[2]["identity_fingerprint"] == hmac_hex(
        expected, settings.IDENTITY_FINGERPRINT_PEPPER.get_secret_value()
    )


async def test_admin_identity_rejects_removing_last_document_and_preserves_explicit_override():
    service, _, _, _ = setup_service()
    with pytest.raises(ValidationDomainError):
        service._encrypt("courier_profiles", uuid4(), {"national_id_encrypted": None}, {})
    values = {"national_id_encrypted": "national", "identity_fingerprint": "explicit-override"}
    service._encrypt("courier_profiles", uuid4(), values, None)
    assert values["identity_fingerprint"] == "explicit-override"
