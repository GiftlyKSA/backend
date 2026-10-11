"""Private courier avatar attachment."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CourierAvatar(TimestampMixin, Base):
    """One active avatar per courier."""

    __tablename__ = "courier_avatars"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    storage_key: Mapped[str] = mapped_column(
        String(512),
        ForeignKey("media_uploads.storage_key", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
