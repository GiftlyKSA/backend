"""Durable persistence contracts for dormant Dhamen and mobile integrations."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import app.models as models
import pytest
from app.models.enums import (
    MediaType,
    MessageType,
    UserRole,
    UserStatus,
    WalletType,
    WithdrawalStatus,
)
from geoalchemy2.elements import WKTElement
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


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
        "streampay_payment_link_id",
        "streampay_payment_url",
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
            "attachment_fks": {
                item["name"] for item in schema.get_foreign_keys("message_attachments")
            },
            "occasion_fks": {item["name"] for item in schema.get_foreign_keys("occasions")},
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
    assert "fk_message_attachments_message_id_messages" in reflected["attachment_fks"]
    assert "fk_occasions_user_id_users" in reflected["occasion_fks"]
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
    wallet = models.Wallet(user_id=None, type=WalletType.COURIER)
    db_session.add_all([courier, wallet])
    await db_session.flush()
    wallet.user_id = courier.id
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
async def test_streampay_generic_fields_are_backfilled(db_session: AsyncSession) -> None:
    """The migration keeps legacy fields and copies their values to generic columns."""
    intent = await db_session.scalar(
        select(models.PaymentIntent).where(
            models.PaymentIntent.checkout_provider == "STREAMPAY",
            models.PaymentIntent.streampay_payment_link_id.is_not(None),
        )
    )
    if intent is None:
        pytest.skip("clean head has no pre-existing StreamPay intent to inspect")
    assert intent.gateway_reference == intent.streampay_payment_link_id
    assert intent.gateway_payment_url == intent.streampay_payment_url
