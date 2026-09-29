"""Queue new-order city pushes after the order transaction commits."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_order_notifications"
down_revision = "0002_schema_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the durable, bounded notification cursor table."""
    op.create_table(
        "order_notifications",
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "city_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cities.id"), nullable=False
        ),
        sa.Column("cursor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_order_notifications_pending",
        "order_notifications",
        ["available_at", "created_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    """Remove the notification queue without touching orders."""
    op.drop_index("idx_order_notifications_pending", table_name="order_notifications")
    op.drop_table("order_notifications")
