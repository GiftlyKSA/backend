"""Persist scoped chat send identities and bounded live-delivery intents.

Rollback removes retry metadata only; message rows and ciphertext remain intact.
Legacy rows have NULL client IDs and no retrospective live fanout.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_chat_retry_recovery"
down_revision = "0025_measured_read_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable legacy-compatible identity and reference-only live outbox."""
    op.add_column("messages", sa.Column("client_message_id", postgresql.UUID(as_uuid=True)))
    op.create_unique_constraint(
        "uq_messages_client_send", "messages", ["conversation_id", "sender_id", "client_message_id"]
    )
    op.create_table(
        "chat_live_deliveries",
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
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
    )
    op.create_index(
        "idx_chat_live_deliveries_pending",
        "chat_live_deliveries",
        ["available_at", "created_at"],
        postgresql_where=sa.text("completed_at IS NULL AND failed_at IS NULL"),
    )

    op.execute(
        sa.text(
            "CREATE TRIGGER trg_chat_live_deliveries_audit "
            "AFTER INSERT OR UPDATE OR DELETE ON chat_live_deliveries "
            "FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('message_id')"
        )
    )


def downgrade() -> None:
    """Retain committed messages while removing retry and live recovery metadata."""
    op.drop_table("chat_live_deliveries")
    op.drop_constraint("uq_messages_client_send", "messages", type_="unique")
    op.drop_column("messages", "client_message_id")
