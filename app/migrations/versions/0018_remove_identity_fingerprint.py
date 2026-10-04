"""Remove derived courier identity indexes, preserving encrypted numbers."""

import sqlalchemy as sa
from alembic import op

revision = "0018_remove_identity_fingerprint"
down_revision = "0017_user_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove the duplicate-document index and unused derived value."""
    op.drop_index("uq_courier_identity_fingerprint", table_name="courier_profiles")
    op.drop_column("courier_profiles", "identity_fingerprint")


def downgrade() -> None:
    """Restore the nullable legacy column without inventing historical fingerprints."""
    op.add_column(
        "courier_profiles", sa.Column("identity_fingerprint", sa.String(64), nullable=True)
    )
    op.create_index(
        "uq_courier_identity_fingerprint",
        "courier_profiles",
        ["identity_fingerprint"],
        unique=True,
        postgresql_where=sa.text("identity_fingerprint IS NOT NULL"),
    )
