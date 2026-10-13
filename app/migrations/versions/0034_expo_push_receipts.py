"""Persist Expo ticket receipts and device-registration snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_expo_push_receipts"
down_revision = "0033_annual_occasions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expo_push_receipts",
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
        sa.Column("ticket_id", sa.String(256), nullable=False, unique=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("device_last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_expo_push_receipts_pending",
        "expo_push_receipts",
        ["available_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE expo_push_receipts IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM expo_push_receipts WHERE completed_at IS NULL) "
        "THEN RAISE EXCEPTION 'Cannot downgrade with pending Expo receipts'; END IF; END $$"
    )
    op.drop_index("idx_expo_push_receipts_pending", table_name="expo_push_receipts")
    op.drop_table("expo_push_receipts")
