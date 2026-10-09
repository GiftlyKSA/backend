"""Permit validated original HEIF images and QuickTime recordings."""

import sqlalchemy as sa
from alembic import op

revision = "0028_original_media_formats"
down_revision = "0027_write_operations"
branch_labels = None
depends_on = None
_OLD = "'image/jpeg','image/png','video/mp4','video/webm','audio/mp4','audio/mpeg','audio/ogg','audio/webm','audio/wav','audio/aac'"
_NEW = _OLD + ",'image/heic','image/heif','video/quicktime'"


def _replace(types: str, videos: str) -> None:
    for table, name in (
        ("media_uploads", "chk_media_content_type"),
        ("message_attachments", "chk_message_attachments_content_type"),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"content_type IN ({types})")
    op.drop_constraint("chk_message_attachments_byte_size", "message_attachments", type_="check")
    op.create_check_constraint(
        "chk_message_attachments_byte_size",
        "message_attachments",
        f"byte_size > 0 AND ((content_type IN ({videos}) AND byte_size <= 125829120) "
        f"OR (content_type NOT IN ({videos}) AND byte_size <= 10485760))",
    )


def upgrade() -> None:
    """Extend constraints while retaining historical formats and existing size caps."""
    _replace(_NEW, "'video/mp4','video/webm','video/quicktime'")


def downgrade() -> None:
    """Reject rollback that would orphan newly supported historical attachments."""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM media_uploads WHERE content_type IN "
            "('image/heic','image/heif','video/quicktime') UNION ALL SELECT 1 FROM "
            "message_attachments WHERE content_type IN ('image/heic','image/heif','video/quicktime'))"
        )
    ):
        raise RuntimeError("Keep original media support while these uploads or attachments exist.")
    _replace(_OLD, "'video/mp4','video/webm'")
