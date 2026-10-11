"""Persist versioned courier report targets separately from financial records."""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CourierReportSettings(TimestampMixin, Base):
    """One owner-scoped target, with optimistic concurrency."""

    __tablename__ = "courier_report_settings"
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courier_profiles.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    period: Mapped[str | None] = mapped_column(String(5), nullable=True)
    __table_args__ = (
        CheckConstraint("version > 0", name="chk_report_settings_version"),
        CheckConstraint(
            "(enabled AND amount > 0 AND amount <= 1000000 AND amount IS NOT NULL "
            "AND period IS NOT NULL AND period IN ('WEEK','MONTH')) "
            "OR (NOT enabled AND amount IS NULL AND period IS NULL)",
            name="chk_report_settings_target",
        ),
    )
