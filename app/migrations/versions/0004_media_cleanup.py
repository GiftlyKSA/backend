"""Fence abandoned media grants during object cleanup."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_media_cleanup"
down_revision = "0003_order_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a deletion fence and an index for bounded expiry scans."""
    op.add_column("media_uploads", sa.Column("deleting_at", sa.DateTime(timezone=True)))
    op.create_index(
        "idx_media_uploads_expiry",
        "media_uploads",
        ["created_at", "id"],
        postgresql_where=sa.text("attached_at IS NULL"),
    )
    op.create_index(
        "idx_media_uploads_outstanding_owner",
        "media_uploads",
        ["owner_user_id"],
        postgresql_where=sa.text("attached_at IS NULL"),
    )


def downgrade() -> None:
    """Remove the cleanup fence and scan index."""
    op.drop_index("idx_media_uploads_outstanding_owner", table_name="media_uploads")
    op.drop_index("idx_media_uploads_expiry", table_name="media_uploads")
    op.drop_column("media_uploads", "deleting_at")
