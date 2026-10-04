"""Snapshot courier push recipients at the first durable delivery claim.

Incomplete legacy sweeps restart their cursor when first snapshotted; an already
delivered legacy page can be repeated once during rollout or rollback.
"""

from alembic import op
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

revision = "0016_notification_recipients"
down_revision = "0015_chat_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add recipient snapshots without changing completed notification history."""
    op.add_column(
        "order_notifications",
        Column("recipients_snapshotted_at", DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "order_notification_recipients",
        Column("id", UUID(as_uuid=True), server_default=text("gen_random_uuid()"), nullable=False),
        Column("order_id", UUID(as_uuid=True), nullable=False),
        Column("token_id", UUID(as_uuid=True), nullable=False),
        Column("user_id", UUID(as_uuid=True), nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=text("now()"), nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default=text("now()"), nullable=False),
        ForeignKeyConstraint(["order_id"], ["order_notifications.order_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["token_id"], ["device_tokens.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        PrimaryKeyConstraint("id"),
        UniqueConstraint(
            "order_id", "token_id", name="uq_order_notification_recipients_order_token"
        ),
    )
    op.create_index(
        "idx_order_notification_recipients_token", "order_notification_recipients", ["token_id"]
    )
    op.create_index(
        "idx_order_notification_recipients_user", "order_notification_recipients", ["user_id"]
    )
    op.execute(
        text(
            "CREATE TRIGGER trg_order_notification_recipients_audit "
            "AFTER INSERT OR UPDATE OR DELETE ON order_notification_recipients "
            "FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('id')"
        )
    )
    op.execute(
        text("UPDATE order_notifications SET cursor_token_id = NULL WHERE completed_at IS NULL")
    )


def downgrade() -> None:
    """Drop snapshots and restart incomplete legacy sweeps for recipient coverage."""
    op.execute(
        text("UPDATE order_notifications SET cursor_token_id = NULL WHERE completed_at IS NULL")
    )
    op.drop_table("order_notification_recipients")
    op.drop_column("order_notifications", "recipients_snapshotted_at")
