"""Add scoped credentials, recoverable requests, and their durable email outbox.

Deploy migration before API/worker code. No existing accounts are backfilled.
Rollback code before downgrade; downgrade refuses request/email history or
unexpired OTP/proof state. Expired ephemeral credentials may be removed.
Financial records are untouched.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0031_account_requests"
down_revision = "0030_mobile_notifications"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[datetime]]:
    return [
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
    ]


def upgrade() -> None:
    """Persist account-scoped proofs, one active request, and fenced email delivery."""
    op.create_table(
        "account_change_challenges",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("auth_version", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("proof_hash", sa.String(64), unique=True),
        sa.Column("proof_expires_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "purpose IN ('PHONE_CHANGE', 'ACCOUNT_REQUEST', 'NEW_PHONE', 'RESTORE_ACCOUNT')",
            name="ck_account_change_challenge_purpose",
        ),
    )
    op.create_index(
        "idx_account_change_challenges_expiry", "account_change_challenges", ["expires_at", "id"]
    )
    op.create_table(
        "account_requests",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("prior_status", sa.String(32), nullable=False),
        sa.Column("disabled_auth_version", sa.Integer(), nullable=False),
        sa.Column("restore_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("reason", sa.String(500)),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('DELETE_ACCOUNT', 'TERMINATE_CONTRACT')", name="ck_account_request_kind"
        ),
        sa.CheckConstraint(
            "status IN ('RECOVERABLE', 'PENDING_REVIEW', 'RESTORED')",
            name="ck_account_request_status",
        ),
    )
    op.create_index(
        "uq_account_requests_active",
        "account_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status != 'RESTORED'"),
    )
    op.create_index(
        "idx_account_requests_review", "account_requests", ["status", "restore_deadline", "id"]
    )
    op.create_table(
        "account_request_emails",
        sa.Column(
            "request_id", UUID(as_uuid=True), sa.ForeignKey("account_requests.id"), primary_key=True
        ),
        sa.Column("to_email", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_id", UUID(as_uuid=True)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index(
        "idx_account_request_emails_pending",
        "account_request_emails",
        ["available_at", "request_id"],
        postgresql_where=sa.text("sent_at IS NULL AND failed_at IS NULL"),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_account_requests_audit AFTER INSERT OR UPDATE OR DELETE ON account_requests FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('id')"
        )
    )


def downgrade() -> None:
    """Refuse retained history or usable credentials before removing empty tables."""
    op.execute(
        sa.text(
            "LOCK TABLE account_requests, account_request_emails, account_change_challenges "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    retained_state = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM account_requests) "
            "OR EXISTS (SELECT 1 FROM account_request_emails) "
            "OR EXISTS (SELECT 1 FROM account_change_challenges "
            "WHERE expires_at > now() OR proof_expires_at > now())"
        )
    )
    if retained_state:
        raise RuntimeError(
            "Retain account-request history and active recovery credentials before downgrade."
        )
    op.drop_table("account_request_emails")
    op.drop_table("account_requests")
    op.drop_table("account_change_challenges")
