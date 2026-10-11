"""Durable, scoped account-change credentials and recoverable review requests."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AccountChangeChallenge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An OTP challenge or single-use proof, bound to account version and purpose."""

    __tablename__ = "account_change_challenges"
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_version: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proof_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    proof_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('PHONE_CHANGE', 'ACCOUNT_REQUEST', 'NEW_PHONE', 'RESTORE_ACCOUNT')",
            name="ck_account_change_challenge_purpose",
        ),
        Index("idx_account_change_challenges_expiry", "expires_at", "id"),
    )


class AccountRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A request that retains all data and funds for eventual administrative review."""

    __tablename__ = "account_requests"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prior_status: Mapped[str] = mapped_column(String(32), nullable=False)
    disabled_auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    restore_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        CheckConstraint(
            "kind IN ('DELETE_ACCOUNT', 'TERMINATE_CONTRACT')", name="ck_account_request_kind"
        ),
        CheckConstraint(
            "status IN ('RECOVERABLE', 'PENDING_REVIEW', 'RESTORED')",
            name="ck_account_request_status",
        ),
        Index(
            "uq_account_requests_active",
            "user_id",
            unique=True,
            postgresql_where=text("status != 'RESTORED'"),
        ),
        Index("idx_account_requests_review", "status", "restore_deadline", "id"),
    )


class AccountRequestEmail(TimestampMixin, Base):
    """One durable notification per request, leased and retried with bounded attempts."""

    __tablename__ = "account_request_emails"
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_requests.id"), primary_key=True
    )
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "idx_account_request_emails_pending",
            "available_at",
            "request_id",
            postgresql_where=text("sent_at IS NULL AND failed_at IS NULL"),
        ),
    )
