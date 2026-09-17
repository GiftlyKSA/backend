"""Add the provider discriminator for local simulated checkouts.

Revision ID: b7c8d9e0f1a2
Revises: 8c9d1e2f3a4b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "8c9d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Label payment intents without enabling a production gateway."""
    op.add_column(
        "payment_intents",
        sa.Column("checkout_provider", sa.String(20), server_default="SIMULATED", nullable=False),
    )


def downgrade() -> None:
    """Remove the provider discriminator."""
    op.drop_column("payment_intents", "checkout_provider")
