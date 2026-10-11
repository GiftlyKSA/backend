"""Add courier private avatars and bounded customer order prefix search."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_courier_avatar_search"
down_revision = "0031_account_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pointers, allow the new upload purpose, and index bounded prefixes."""
    op.create_table(
        "courier_avatars",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "storage_key",
            sa.String(512),
            sa.ForeignKey("media_uploads.storage_key", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.drop_constraint("chk_media_purpose", "media_uploads", type_="check")
    op.create_check_constraint(
        "chk_media_purpose",
        "media_uploads",
        "purpose IN ('ORDER_REQUEST','DELIVERY_PROOF','CHAT_ATTACHMENT','COURIER_AVATAR')",
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_orders_customer_description_prefix ON orders (customer_id, left(lower(description),100) text_pattern_ops)"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_courier_avatars_audit AFTER INSERT OR UPDATE OR DELETE ON courier_avatars FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('user_id')"
        )
    )


def downgrade() -> None:
    """Refuse rollback until the new upload and avatar state is preserved."""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM media_uploads WHERE purpose='COURIER_AVATAR') OR EXISTS(SELECT 1 FROM courier_avatars)"
        )
    ):
        raise RuntimeError("Preserve courier avatar uploads before rollback.")
    op.drop_index("idx_orders_customer_description_prefix", table_name="orders")
    op.drop_table("courier_avatars")
    op.drop_constraint("chk_media_purpose", "media_uploads", type_="check")
    op.create_check_constraint(
        "chk_media_purpose",
        "media_uploads",
        "purpose IN ('ORDER_REQUEST','DELIVERY_PROOF','CHAT_ATTACHMENT')",
    )
