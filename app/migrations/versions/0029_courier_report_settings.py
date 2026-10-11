"""Create versioned owner-scoped courier report targets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_courier_report_settings"
down_revision = "0028_original_media_formats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create a small target table; no financial history is rewritten."""
    op.create_table(
        "courier_report_settings",
        sa.Column(
            "courier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courier_profiles.user_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("period", sa.String(5), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="chk_report_settings_version"),
        sa.CheckConstraint(
            "(enabled AND amount > 0 AND amount <= 1000000 AND amount IS NOT NULL "
            "AND period IS NOT NULL AND period IN ('WEEK','MONTH')) "
            "OR (NOT enabled AND amount IS NULL AND period IS NULL)",
            name="chk_report_settings_target",
        ),
    )

    op.execute(
        sa.text(
            "CREATE TRIGGER trg_courier_report_settings_audit "
            "AFTER INSERT OR UPDATE OR DELETE ON courier_report_settings "
            "FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('courier_id')"
        )
    )


def downgrade() -> None:
    """Refuse rollback that would silently lose persisted courier settings."""
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM courier_report_settings)")):
        raise RuntimeError("Preserve persisted courier report settings before rollback.")
    op.drop_table("courier_report_settings")
