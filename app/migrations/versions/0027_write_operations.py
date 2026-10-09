"""Retain owned encrypted write recovery and stable operation identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_write_operations"
down_revision = "0026_chat_retry_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add bounded-expiry records without changing existing resources."""
    op.create_table(
        "write_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("operation_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("result_encrypted", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "operation", "operation_key", name="uq_write_operation_key"),
    )
    op.create_index("ix_write_operations_expires_at", "write_operations", ["expires_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_write_operations_audit AFTER INSERT OR UPDATE OR DELETE "
            "ON write_operations FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('id')"
        )
    )


def downgrade() -> None:
    """Refuse to remove unresolved or still-retained replay protection."""
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM write_operations WHERE result_encrypted IS NULL OR expires_at > now())"
        )
    ):
        raise RuntimeError(
            "Resolve outstanding operations and wait for retention before downgrade."
        )
    op.drop_table("write_operations")
