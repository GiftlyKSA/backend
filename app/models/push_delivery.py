"""Per-event device delivery state without copied tokens or message content."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PUSH_DELIVERY_DUE_PREDICATE = "state IN ('PENDING','LEASED')"


class PushDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A durable device target; provider acceptance and inbox reads are separate."""

    __tablename__ = "push_deliveries"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, str]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(24), server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("event_id", "device_id", name="uq_push_delivery_event_device"),
        CheckConstraint("attempts BETWEEN 0 AND 8", name="chk_push_delivery_attempts"),
        CheckConstraint(
            "state IN ('PENDING','LEASED','ACCEPTED','PLATFORM_ACCEPTED','FAILED','EXPIRED')",
            name="chk_push_delivery_state",
        ),
        Index(
            "idx_push_delivery_due",
            "available_at",
            "id",
            postgresql_where=text(PUSH_DELIVERY_DUE_PREDICATE),
        ),
    )
