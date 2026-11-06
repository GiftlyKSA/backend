"""Track independent Expo device outcomes without replaying historical outboxes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_push_deliveries"
down_revision = "0039_order_read_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create empty delivery state; existing Expo receipts remain valid."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.create_table(
        "push_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("event_id", "device_id", name="uq_push_delivery_event_device"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 8", name="chk_push_delivery_attempts"),
        sa.CheckConstraint(
            "state IN ('PENDING','LEASED','ACCEPTED','PLATFORM_ACCEPTED','FAILED','EXPIRED')",
            name="chk_push_delivery_state",
        ),
    )
    op.create_index(
        "idx_push_delivery_due",
        "push_deliveries",
        ["available_at", "id"],
        postgresql_where=sa.text("state IN ('PENDING','LEASED')"),
    )
    op.create_index("ix_push_deliveries_user_id", "push_deliveries", ["user_id"])
    op.add_column("expo_push_receipts", sa.Column("delivery_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_expo_receipt_delivery",
        "expo_push_receipts",
        "push_deliveries",
        ["delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_expo_receipt_delivery", "expo_push_receipts", ["delivery_id"])


def downgrade() -> None:
    """Require an explicit data-preservation plan before discarding delivery history."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("LOCK TABLE push_deliveries, expo_push_receipts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM push_deliveries)")):
        raise RuntimeError(
            "Cannot downgrade with push delivery records; preserve delivery state first."
        )
    op.drop_index("idx_expo_receipt_delivery", table_name="expo_push_receipts")
    op.drop_constraint("fk_expo_receipt_delivery", "expo_push_receipts", type_="foreignkey")
    op.drop_column("expo_push_receipts", "delivery_id")
    op.drop_table("push_deliveries")
