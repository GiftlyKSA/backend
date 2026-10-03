"""Enable private chat media and verified recording duration metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0015_chat_media"
down_revision = "0014_overdue_order_index"
branch_labels = None
depends_on = None

_TYPES = "content_type IN ('image/jpeg','image/png','video/mp4','video/webm','audio/mp4','audio/mpeg','audio/ogg','audio/webm','audio/wav','audio/aac')"
_SIZE = "byte_size > 0 AND ((content_type IN ('video/mp4','video/webm') AND byte_size <= 125829120) OR (content_type NOT IN ('video/mp4','video/webm') AND byte_size <= 10485760))"


def _check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    op.execute("ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'VOICE'")
    op.add_column(
        "message_attachments", sa.Column("duration_seconds", sa.Numeric(9, 3), nullable=True)
    )
    _check(
        "media_uploads",
        "chk_media_purpose",
        "purpose IN ('ORDER_REQUEST','DELIVERY_PROOF','CHAT_ATTACHMENT')",
    )
    _check("media_uploads", "chk_media_content_type", _TYPES)
    _check("message_attachments", "chk_message_attachments_content_type", _TYPES)
    _check("message_attachments", "chk_message_attachments_byte_size", _SIZE)
    op.create_check_constraint(
        "chk_attachment_duration",
        "message_attachments",
        "duration_seconds IS NULL OR (duration_seconds > 0 AND duration_seconds <= 120)",
    )


def downgrade() -> None:
    # Fail rather than destroy media incompatible with the previous schema.
    _check("media_uploads", "chk_media_purpose", "purpose IN ('ORDER_REQUEST','DELIVERY_PROOF')")
    for table, name in (
        ("media_uploads", "chk_media_content_type"),
        ("message_attachments", "chk_message_attachments_content_type"),
    ):
        _check(table, name, "content_type IN ('image/jpeg','image/png')")
    _check(
        "message_attachments",
        "chk_message_attachments_byte_size",
        "byte_size > 0 AND byte_size <= 10485760",
    )
    op.drop_constraint("chk_attachment_duration", "message_attachments", type_="check")
    op.drop_column("message_attachments", "duration_seconds")
