"""Durable mobile inbox, status history and one-off reminder delivery."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InboxNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A neutral localizable event owned by one marketplace user."""

    __tablename__ = "inbox_notifications"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    event_key: Mapped[str] = mapped_column(String(160))
    notification_type: Mapped[str] = mapped_column(String(40))
    destination_kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(notification_type='ORDER_STATUS_CHANGED' AND destination_kind='ORDER' "
            "AND status IS NOT NULL AND status IN "
            "('NEW','ASSIGNED','WAITING_PAYMENT','IN_PROGRESS','DELIVERED','COMPLETED',"
            "'CANCELLED','DISPUTED','REFUNDED')) "
            "OR (notification_type='INVOICE_ISSUED' AND destination_kind='ORDER' "
            "AND status IS NULL) "
            "OR (notification_type='OCCASION_REMINDER' AND destination_kind='OCCASION' "
            "AND status IS NULL) "
            "OR (notification_type='WALLET_TOPUP_COMPLETED' AND destination_kind='WALLET' "
            "AND status IS NULL) "
            "OR (notification_type='WITHDRAWAL_STATUS_CHANGED' AND destination_kind='WALLET' "
            "AND status IS NOT NULL AND status IN "
            "('REQUESTED','APPROVED','REJECTED','PAID','SUBMITTED'))",
            name="chk_inbox_event",
        ),
        UniqueConstraint("user_id", "event_key", name="uq_inbox_notification_event"),
        Index("idx_inbox_owned_time", "user_id", text("created_at DESC"), text("id DESC")),
        Index(
            "idx_inbox_unread",
            "user_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )


class OrderTimelineOrigin(Base):
    """Marks orders created while complete event capture is installed."""

    __tablename__ = "order_timeline_origins"
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )


class OrderTimelineEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An actual persisted order status, with privacy-bounded attribution."""

    __tablename__ = "order_timeline_events"
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32))
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('NEW','ASSIGNED','WAITING_PAYMENT','IN_PROGRESS','DELIVERED',"
            "'COMPLETED','CANCELLED','DISPUTED','REFUNDED')",
            name="order_timeline_events_status_check",
        ),
        CheckConstraint(
            "actor_role IN ('CUSTOMER','COURIER','ADMIN')",
            name="order_timeline_events_actor_role_check",
        ),
        Index("idx_order_timeline_time", "order_id", "created_at", "id"),
    )


class OccasionReminder(Base):
    """Versioned schedule and fenced push progress for one saved occasion."""

    __tablename__ = "occasion_reminders"
    occasion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("occasions.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurrence_date: Mapped[date] = mapped_column(Date)
    enabled: Mapped[bool] = mapped_column(Boolean)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    push_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    push_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cursor_token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    __table_args__ = (
        CheckConstraint("version > 0", name="occasion_reminders_version_check"),
        CheckConstraint(
            "push_attempts BETWEEN 0 AND 5",
            name="occasion_reminders_push_attempts_check",
        ),
        Index(
            "idx_occasion_reminders_due",
            "scheduled_at",
            "occasion_id",
            postgresql_where=text("enabled AND delivered_at IS NULL"),
        ),
        Index(
            "idx_occasion_reminders_push",
            "next_attempt_at",
            "occasion_id",
            postgresql_where=text(
                "enabled AND delivered_at IS NOT NULL AND push_completed_at IS NULL"
            ),
        ),
    )
