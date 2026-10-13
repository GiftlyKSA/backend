"""Durable Expo tickets with bounded receipt lookup and lease fencing."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExpoPushReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Retain the original registration snapshot until receipt handling finishes."""

    __tablename__ = "expo_push_receipts"

    ticket_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    device_last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index(
            "idx_expo_push_receipts_pending",
            "available_at",
            postgresql_where=completed_at.is_(None),
        ),
    )
