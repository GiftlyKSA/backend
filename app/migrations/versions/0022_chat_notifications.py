"""Persist chat push intent and delivery leases independently of append-only messages.

Existing messages are not backfilled, avoiding unsolicited historical pushes.
Deploy this migration before API/worker code. Roll back code before downgrade;
downgrade drops pending intents and delivery history, leaving messages intact.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0022_chat_notifications"
down_revision = "0021_courier_read_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a message-keyed outbox and a partial index for ready delivery scans."""
    op.create_table(
        "chat_notifications",
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", UUID(as_uuid=True), nullable=False),
        sa.Column("cursor_token_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_id", UUID(as_uuid=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "idx_chat_notifications_pending",
        "chat_notifications",
        ["available_at", "created_at"],
        postgresql_where=sa.text("completed_at IS NULL AND failed_at IS NULL"),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_chat_notifications_audit "
            "AFTER INSERT OR UPDATE OR DELETE ON chat_notifications "
            "FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('message_id')"
        )
    )


def downgrade() -> None:
    """Drop only push intent/history; preserve the committed message rows."""
    op.drop_table("chat_notifications")
