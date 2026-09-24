"""Bind issued media uploads to owner, purpose, and one attachment.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_uploads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("storage_key", name="uq_media_uploads_storage_key"),
        sa.CheckConstraint(
            "purpose IN ('ORDER_REQUEST', 'DELIVERY_PROOF')", name="chk_media_purpose"
        ),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png')", name="chk_media_content_type"
        ),
        sa.CheckConstraint("byte_size > 0", name="chk_media_byte_size_positive"),
    )


def downgrade() -> None:
    op.drop_table("media_uploads")
