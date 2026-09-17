"""Durable persistence contracts for dormant Dhamen and mobile integrations."""

from __future__ import annotations

import inspect as python_inspect
import io
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.models as models
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.migrations.versions import c9d0e1f2a3b4_add_dhamen_and_mobile_contracts as migration
from app.models.enums import (
    MediaType,
    MessageType,
    UserRole,
    UserStatus,
    WalletType,
    WithdrawalStatus,
)
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

_PRIOR_REVISION = "b7c8d9e0f1a2"
_MIGRATION_DATABASE_ENV = "MIGRATION_TEST_DATABASE_URL"


def test_mobile_contract_enum_values_are_available_without_changing_existing_values() -> None:
    """New values extend, rather than renumber or rename, persisted enum contracts."""
    assert UserStatus.REJECTED.value == "REJECTED"
    assert WithdrawalStatus.SUBMITTED.value == "SUBMITTED"
    assert MediaType.PROFILE_AVATAR.value == "PROFILE_AVATAR"
    assert MediaType.CHAT_ATTACHMENT.value == "CHAT_ATTACHMENT"
    assert MessageType.IMAGE.value == "IMAGE"
    assert MessageType.MIXED.value == "MIXED"

    assert [status.value for status in UserStatus][:3] == [
        "ACTIVE",
        "BANNED",
        "PENDING_VERIFICATION",
    ]


def test_models_expose_generic_gateway_and_privacy_scoped_columns() -> None:
    """Dropping a durable generic/profile field breaks the model contract."""
    assert {
        "gateway_reference",
        "gateway_payment_url",
        "gateway_customer_identifier",
    } <= set(models.PaymentIntent.__table__.columns.keys())
    assert {"avatar_storage_key", "gateway_customer_identifier"} <= set(
        models.User.__table__.columns.keys()
    )
    assert {
        "gateway_supplier_id",
        "payout_iban_encrypted",
        "verification_rejection_reason",
    } <= set(models.CourierProfile.__table__.columns.keys())


def test_models_expose_provider_planning_and_attachment_tables() -> None:
    """Removing a new persistence record breaks downstream repository contracts."""
    expected_columns = {
        "dhamen_notification_receipts": {
            "notification_id",
            "batch_id",
            "notification_type",
            "payment_reference",
            "transaction_id",
            "raw_hash",
            "processed_at",
            "processing_outcome",
        },
        "payout_transfers": {
            "withdrawal_id",
            "provider",
            "payment_reference",
            "supplier_id",
            "amount",
            "status",
            "uti",
            "failure_reason",
            "submitted_at",
            "completed_at",
        },
        "featured_gifts": {
            "title",
            "subtitle",
            "image_storage_key",
            "category",
            "price_from_amount",
            "is_active",
            "display_order",
        },
        "occasions": {
            "user_id",
            "title",
            "occasion_date",
            "reminder_days_before",
            "featured_gift_id",
        },
        "message_attachments": {
            "message_id",
            "storage_key",
            "content_type",
            "byte_size",
            "display_order",
        },
    }
    for table_name, columns in expected_columns.items():
        table = models.Base.metadata.tables[table_name]
        assert columns <= set(table.columns.keys())
    receipt_columns = set(models.DhamenNotificationReceipt.__table__.columns.keys())
    assert receipt_columns.isdisjoint(
        {"raw_body", "raw_json", "payload", "customer_identifier", "iban", "identity_number"}
    )


def test_downgrade_sql_guards_durable_payout_state_before_dropping_tables() -> None:
    """Downgrade aborts before erasing transfer audit state or reopening a payout."""
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.downgrade()

    statements = output.getvalue()
    guard = statements.index("Refusing downgrade: payout transfer state exists")
    first_drop = statements.index("DROP TABLE payout_transfers")
    assert guard < first_drop


def test_upgrade_sql_checks_every_active_user_was_backfilled() -> None:
    """Upgrade fails instead of silently leaving an active user without an identifier."""
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()

    statements = output.getvalue()
    assert "Active user gateway customer identifier backfill incomplete" in statements


def test_repository_contracts_are_available_for_later_service_tasks() -> None:
    """Removing a required ownership/provider method breaks the dormant contract."""
    from app.repositories.chat_repository import ChatRepository
    from app.repositories.order_repository import CourierLocationRepository, OrderRepository
    from app.repositories.payment_repository import PaymentRepository
    from app.repositories.planning_repository import PlanningRepository
    from app.repositories.rating_repository import RatingRepository
    from app.repositories.user_repository import UserRepository

    expected = {
        PaymentRepository: {
            "lock_intent_by_gateway_reference",
            "insert_notification_receipt_if_new",
            "list_due_gateway_reconciliation",
            "create_payout_transfer",
            "lock_payout_transfer",
        },
        OrderRepository: {"list_for_courier", "list_order_media_for_actor"},
        UserRepository: {"actor_shares_participant"},
        ChatRepository: {"add_attachment", "list_attachments_for_actor"},
        RatingRepository: {"actor_has_rated"},
        PlanningRepository: {
            "list_active_featured_gifts",
            "create_occasion",
            "get_occasion_for_actor",
            "list_occasions_for_actor",
            "delete_occasion_for_actor",
        },
        CourierLocationRepository: {"save", "get", "delete"},
    }
    for repository, methods in expected.items():
        assert methods <= set(dir(repository))
    assert "actor_id" in python_inspect.signature(ChatRepository.add_attachment).parameters


@pytest.mark.asyncio
async def test_head_schema_has_expected_constraints_and_indexes(
    db_connection: AsyncConnection,
) -> None:
    """Alembic head creates every durable table and named integrity boundary."""

    def reflect(sync_connection: object) -> dict[str, set[str]]:
        schema = inspect(sync_connection)
        return {
            "tables": set(schema.get_table_names()),
            "user_indexes": {item["name"] for item in schema.get_indexes("users")},
            "intent_indexes": {item["name"] for item in schema.get_indexes("payment_intents")},
            "payout_uniques": {
                item["name"] for item in schema.get_unique_constraints("payout_transfers")
            },
            "payout_indexes": {item["name"] for item in schema.get_indexes("payout_transfers")},
            "payout_fks": {item["name"] for item in schema.get_foreign_keys("payout_transfers")},
            "payout_checks": {
                item["name"] for item in schema.get_check_constraints("payout_transfers")
            },
            "receipt_uniques": {
                item["name"]
                for item in schema.get_unique_constraints("dhamen_notification_receipts")
            },
            "receipt_columns": {
                item["name"] for item in schema.get_columns("dhamen_notification_receipts")
            },
            "receipt_indexes": {
                item["name"] for item in schema.get_indexes("dhamen_notification_receipts")
            },
            "receipt_checks": {
                item["name"]
                for item in schema.get_check_constraints("dhamen_notification_receipts")
            },
            "attachment_fks": {
                item["name"] for item in schema.get_foreign_keys("message_attachments")
            },
            "attachment_uniques": {
                item["name"] for item in schema.get_unique_constraints("message_attachments")
            },
            "attachment_indexes": {
                item["name"] for item in schema.get_indexes("message_attachments")
            },
            "occasion_fks": {item["name"] for item in schema.get_foreign_keys("occasions")},
            "occasion_indexes": {item["name"] for item in schema.get_indexes("occasions")},
            "occasion_checks": {item["name"] for item in schema.get_check_constraints("occasions")},
            "featured_indexes": {item["name"] for item in schema.get_indexes("featured_gifts")},
            "featured_checks": {
                item["name"] for item in schema.get_check_constraints("featured_gifts")
            },
            "attachment_checks": {
                item["name"] for item in schema.get_check_constraints("message_attachments")
            },
        }

    reflected = await db_connection.run_sync(reflect)
    assert {
        "dhamen_notification_receipts",
        "payout_transfers",
        "featured_gifts",
        "occasions",
        "message_attachments",
    } <= reflected["tables"]
    assert "uq_users_gateway_customer_identifier" in reflected["user_indexes"]
    assert "uq_payment_intents_gateway_reference" in reflected["intent_indexes"]
    assert {
        "uq_payout_transfers_withdrawal",
        "uq_payout_transfers_provider_reference",
    } <= reflected["payout_uniques"]
    assert "idx_payout_transfers_status_submitted" in reflected["payout_indexes"]
    assert "fk_payout_transfers_withdrawal_id_withdrawals" in reflected["payout_fks"]
    assert {
        "chk_payout_transfers_amount_positive",
        "chk_payout_transfers_provider",
        "chk_payout_transfers_status",
        "chk_payout_transfers_timestamps",
    } <= reflected["payout_checks"]
    assert "uq_dhamen_receipts_notification" in reflected["receipt_uniques"]
    assert reflected["receipt_columns"].isdisjoint(
        {
            "raw_body",
            "raw_json",
            "callback_body",
            "payload",
            "body",
            "iban",
            "identity_number",
            "national_id",
            "card_number",
            "customer_identifier",
            "personal_data",
        }
    )
    assert {
        "idx_dhamen_receipts_batch",
        "idx_dhamen_receipts_payment_reference",
        "idx_dhamen_receipts_pending",
    } <= reflected["receipt_indexes"]
    assert {"chk_dhamen_receipts_raw_hash", "chk_dhamen_receipts_outcome"} <= reflected[
        "receipt_checks"
    ]
    assert "fk_message_attachments_message_id_messages" in reflected["attachment_fks"]
    assert "uq_message_attachments_message_key" in reflected["attachment_uniques"]
    assert "idx_message_attachments_message_order" in reflected["attachment_indexes"]
    assert "fk_occasions_user_id_users" in reflected["occasion_fks"]
    assert "fk_occasions_featured_gift_id_featured_gifts" in reflected["occasion_fks"]
    assert "idx_occasions_user_date" in reflected["occasion_indexes"]
    assert "chk_occasions_reminder_days" in reflected["occasion_checks"]
    assert {"idx_featured_gifts_active_order", "idx_featured_gifts_category"} <= reflected[
        "featured_indexes"
    ]
    assert {
        "chk_featured_gifts_price_non_negative",
        "chk_featured_gifts_display_order",
    } <= reflected["featured_checks"]
    assert {
        "chk_message_attachments_content_type",
        "chk_message_attachments_byte_size",
    } <= reflected["attachment_checks"]


@pytest.mark.asyncio
async def test_database_rejects_duplicate_provider_and_customer_records(
    db_session: AsyncSession,
) -> None:
    """Uniqueness and attachment ownership are enforced by PostgreSQL itself."""
    courier = models.User(
        phone=f"+9665{uuid.uuid4().int % 10**8:08d}",
        role=UserRole.COURIER,
        gateway_customer_identifier="100000000001",
    )
    db_session.add(courier)
    await db_session.flush()
    wallet = models.Wallet(user_id=courier.id, type=WalletType.COURIER)
    db_session.add(wallet)
    await db_session.flush()
    withdrawal = models.Withdrawal(
        courier_id=courier.id,
        wallet_id=wallet.id,
        amount=Decimal("50.00"),
        iban_encrypted="ciphertext",
        iban_last4="1234",
    )
    db_session.add(withdrawal)
    await db_session.flush()

    receipt = models.DhamenNotificationReceipt(
        notification_id="notification-1",
        batch_id="batch-1",
        notification_type="Payment_Settled",
        raw_hash="a" * 64,
    )
    transfer = models.PayoutTransfer(
        withdrawal_id=withdrawal.id,
        provider="DHAMEN",
        payment_reference="payout-1",
        supplier_id=uuid.uuid4(),
        amount=Decimal("50.00"),
        status="CREATED",
    )
    db_session.add_all([receipt, transfer])
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                models.DhamenNotificationReceipt(
                    notification_id="notification-1",
                    batch_id="batch-2",
                    notification_type="Payment_Failed",
                    raw_hash="b" * 64,
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                models.User(
                    phone=f"+9665{uuid.uuid4().int % 10**8:08d}",
                    role=UserRole.CUSTOMER,
                    gateway_customer_identifier="100000000001",
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                models.PayoutTransfer(
                    withdrawal_id=withdrawal.id,
                    provider="DHAMEN",
                    payment_reference="payout-1",
                    supplier_id=uuid.uuid4(),
                    amount=Decimal("50.00"),
                    status="CREATED",
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                models.MessageAttachment(
                    message_id=uuid.uuid4(),
                    storage_key="chat/missing.jpg",
                    content_type="image/jpeg",
                    byte_size=100,
                )
            )
            await db_session.flush()


@pytest.mark.asyncio
async def test_repository_queries_keep_mobile_resources_actor_scoped(
    db_session: AsyncSession,
) -> None:
    """Another user cannot read an occasion or order media through repositories."""
    from app.repositories.order_repository import OrderRepository
    from app.repositories.planning_repository import PlanningRepository

    owner = models.User(phone=f"+9665{uuid.uuid4().int % 10**8:08d}", role=UserRole.CUSTOMER)
    stranger = models.User(phone=f"+9665{uuid.uuid4().int % 10**8:08d}", role=UserRole.CUSTOMER)
    db_session.add_all([owner, stranger])
    await db_session.flush()
    planning = PlanningRepository(db_session)
    occasion = await planning.create_occasion(
        user_id=owner.id,
        title="Birthday",
        occasion_date=date.today() + timedelta(days=30),
        reminder_days_before=7,
        featured_gift_id=None,
    )
    assert await planning.get_occasion_for_actor(occasion.id, owner.id) == occasion
    assert await planning.get_occasion_for_actor(occasion.id, stranger.id) is None

    order = models.Order(
        customer_id=owner.id,
        description="A gift",
        delivery_city="Jeddah",
        delivery_location=WKTElement("POINT(39.2 21.5)", srid=4326),
        delivery_date=date.today() + timedelta(days=2),
    )
    db_session.add(order)
    await db_session.flush()
    media = models.OrderMedia(
        order_id=order.id,
        uploaded_by_user_id=owner.id,
        media_type=MediaType.CUSTOMER_REQUEST,
        storage_key="orders/request.jpg",
        content_type="image/jpeg",
        byte_size=100,
    )
    db_session.add(media)
    await db_session.flush()
    orders = OrderRepository(db_session)
    assert await orders.list_order_media_for_actor(order.id, owner.id) == [media]
    assert await orders.list_order_media_for_actor(order.id, stranger.id) == []


@pytest.mark.asyncio
async def test_attachment_insert_is_authorized_in_the_repository_query(
    db_session: AsyncSession,
) -> None:
    """A non-participant cannot create an attachment even when the message ID is known."""
    from app.repositories.chat_repository import ChatRepository

    customer = models.User(phone=f"+9665{uuid.uuid4().int % 10**8:08d}", role=UserRole.CUSTOMER)
    courier = models.User(phone=f"+9665{uuid.uuid4().int % 10**8:08d}", role=UserRole.COURIER)
    outsider = models.User(phone=f"+9665{uuid.uuid4().int % 10**8:08d}", role=UserRole.CUSTOMER)
    db_session.add_all([customer, courier, outsider])
    await db_session.flush()
    order = models.Order(
        customer_id=customer.id,
        courier_id=courier.id,
        description="A gift",
        delivery_city="Jeddah",
        delivery_location=WKTElement("POINT(39.2 21.5)", srid=4326),
        delivery_date=date.today() + timedelta(days=2),
        status=models.enums.OrderStatus.ASSIGNED,
    )
    db_session.add(order)
    await db_session.flush()
    conversation = models.Conversation(
        order_id=order.id,
        customer_id=customer.id,
        courier_id=courier.id,
    )
    db_session.add(conversation)
    await db_session.flush()
    message = models.Message(
        conversation_id=conversation.id,
        sender_id=customer.id,
        content_encrypted="ciphertext",
    )
    db_session.add(message)
    await db_session.flush()

    chat = ChatRepository(db_session)
    denied = await chat.add_attachment(
        message_id=message.id,
        actor_id=outsider.id,
        storage_key="chat/denied.jpg",
        content_type="image/jpeg",
        byte_size=100,
        display_order=0,
    )
    allowed = await chat.add_attachment(
        message_id=message.id,
        actor_id=customer.id,
        storage_key="chat/allowed.jpg",
        content_type="image/jpeg",
        byte_size=100,
        display_order=0,
    )

    assert denied is None
    assert allowed is not None
    assert await db_session.scalar(select(func.count()).select_from(models.MessageAttachment)) == 1


@pytest.mark.asyncio
async def test_notification_receipt_repository_is_idempotent(db_session: AsyncSession) -> None:
    """A notification replay returns None and leaves one durable receipt."""
    from app.repositories.payment_repository import PaymentRepository

    payments = PaymentRepository(db_session)
    values = {
        "notification_id": f"notification-{uuid.uuid4()}",
        "batch_id": "batch-1",
        "notification_type": "Payment_Settled",
        "payment_reference": "payment-1",
        "transaction_id": "transaction-1",
        "raw_hash": "a" * 64,
    }
    first = await payments.insert_notification_receipt_if_new(**values)
    replay = await payments.insert_notification_receipt_if_new(**values)

    assert first is not None
    assert replay is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(models.DhamenNotificationReceipt)
            .where(models.DhamenNotificationReceipt.notification_id == values["notification_id"])
        )
        == 1
    )


@pytest.mark.asyncio
async def test_real_migration_preserves_checkouts_and_backfills_active_users() -> None:
    """Prior-head rows are seeded before upgrade so the actual migration does the backfill."""
    database_url = _dedicated_migration_database_url()
    _run_alembic(database_url, "downgrade", "base")
    _run_alembic(database_url, "upgrade", _PRIOR_REVISION)
    active_id = uuid.uuid4()
    banned_id = uuid.uuid4()
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, phone, role, status)
                    VALUES (:active_id, :active_phone, 'CUSTOMER', 'ACTIVE'),
                           (:banned_id, :banned_phone, 'CUSTOMER', 'BANNED')
                    """
                ),
                {
                    "active_id": active_id,
                    "active_phone": f"+9665{uuid.uuid4().int % 10**8:08d}",
                    "banned_id": banned_id,
                    "banned_phone": f"+9665{uuid.uuid4().int % 10**8:08d}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO payment_intents (
                        user_id, purpose, amount, status, checkout_provider,
                        gateway_reference, gateway_payment_url, expires_at
                    ) VALUES (
                        :user_id, 'WALLET_TOPUP', 100.00, 'NEW', 'SIMULATED',
                        'legacy-link-1', 'https://legacy.example/pay/1', :expires_at
                    )
                    """
                ),
                {"user_id": active_id, "expires_at": datetime.now(UTC) + timedelta(hours=1)},
            )
        await engine.dispose()
        _run_alembic(database_url, "upgrade", "head")
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            backfilled = (
                await connection.execute(
                    text(
                        """
                        SELECT gateway_reference, gateway_payment_url
                        FROM payment_intents
                        WHERE gateway_reference = 'legacy-link-1'
                        """
                    )
                )
            ).one()
            active_identifier = await connection.scalar(
                text("SELECT gateway_customer_identifier FROM users WHERE id = :id"),
                {"id": active_id},
            )
            banned_identifier = await connection.scalar(
                text("SELECT gateway_customer_identifier FROM users WHERE id = :id"),
                {"id": banned_id},
            )
            missing_active = await connection.scalar(
                text(
                    "SELECT count(*) FROM users "
                    "WHERE status = 'ACTIVE' AND gateway_customer_identifier IS NULL"
                )
            )
        assert tuple(backfilled) == ("legacy-link-1", "https://legacy.example/pay/1")
        assert isinstance(active_identifier, str)
        assert len(active_identifier) == 12 and active_identifier.isdigit()
        assert banned_identifier is None
        assert missing_active == 0
    finally:
        await engine.dispose()
        _run_alembic(database_url, "downgrade", "base")


@pytest.mark.asyncio
async def test_real_downgrade_refuses_to_erase_submitted_payout_state() -> None:
    """A real downgrade fails atomically while transfer and submitted state remain."""
    database_url = _dedicated_migration_database_url()
    _run_alembic(database_url, "downgrade", "base")
    _run_alembic(database_url, "upgrade", "head")
    courier_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    withdrawal_id = uuid.uuid4()
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, phone, role, status) "
                    "VALUES (:id, :phone, 'COURIER', 'ACTIVE')"
                ),
                {"id": courier_id, "phone": f"+9665{uuid.uuid4().int % 10**8:08d}"},
            )
            await connection.execute(
                text("INSERT INTO wallets (id, user_id, type) VALUES (:id, :user_id, 'COURIER')"),
                {"id": wallet_id, "user_id": courier_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO withdrawals (
                        id, courier_id, wallet_id, amount, iban_encrypted, iban_last4, status
                    ) VALUES (
                        :id, :courier_id, :wallet_id, 50.00, 'ciphertext', '1234', 'SUBMITTED'
                    )
                    """
                ),
                {"id": withdrawal_id, "courier_id": courier_id, "wallet_id": wallet_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO payout_transfers (
                        withdrawal_id, provider, payment_reference, supplier_id, amount, status
                    ) VALUES (
                        :withdrawal_id, 'DHAMEN', 'payout-1', :supplier_id, 50.00, 'SUBMITTED'
                    )
                    """
                ),
                {"withdrawal_id": withdrawal_id, "supplier_id": uuid.uuid4()},
            )
        await engine.dispose()
        result = _run_alembic(database_url, "downgrade", _PRIOR_REVISION, expect_success=False)
        assert result.returncode != 0
        assert "Refusing downgrade: payout transfer state exists" in result.stderr
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM payout_transfers")) == 1
            assert (
                await connection.scalar(
                    text("SELECT status::text FROM withdrawals WHERE id = :id"),
                    {"id": withdrawal_id},
                )
                == "SUBMITTED"
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM payout_transfers"))
            await connection.execute(
                text("UPDATE withdrawals SET status = 'APPROVED' WHERE id = :id"),
                {"id": withdrawal_id},
            )
        await engine.dispose()
        _run_alembic(database_url, "downgrade", "base")


def _dedicated_migration_database_url() -> str:
    """Return an explicitly disposable migration-test URL or skip safely."""
    raw = os.environ.get(_MIGRATION_DATABASE_ENV)
    if raw is None:
        pytest.skip(
            f"{_MIGRATION_DATABASE_ENV} is required and must name a disposable *_migration_test DB"
        )
    database = make_url(raw).database or ""
    if not database.endswith("_migration_test"):
        pytest.fail(f"{_MIGRATION_DATABASE_ENV} must name a disposable *_migration_test database")
    return raw


def _run_alembic(
    database_url: str, command: str, revision: str, *, expect_success: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run Alembic in a subprocess with an explicit disposable database URL."""
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "test",
            "DATABASE_URL": database_url,
            "REDIS_URL": "redis://localhost:6379/0",
            "JWT_SECRET": "test-jwt-secret-value-not-real-000000000000",
            "FIELD_ENCRYPTION_KEYS": '{"1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
            "FIELD_ENCRYPTION_KEY_VERSION": "1",
            "IDENTITY_FINGERPRINT_PEPPER": "test-pepper-value-not-real-0000000000000",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        capture_output=True,
        check=False,
        cwd=os.getcwd(),
        env=environment,
        text=True,
    )
    if expect_success and result.returncode != 0:
        pytest.fail(f"Alembic {command} {revision} failed:\n{result.stderr}")
    return result
