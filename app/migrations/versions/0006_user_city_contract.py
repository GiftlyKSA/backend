"""Add public user identifiers and Arabic city names; remove unused user fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_user_city_contract"
down_revision = "0005_receipt_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill existing records and switch to the public profile contract."""
    op.add_column("cities", sa.Column("name_ar", sa.String(length=100), nullable=True))
    names = {
        "RUH": "الرياض",
        "JED": "جدة",
        "MKK": "مكة المكرمة",
        "MED": "المدينة المنورة",
        "DMM": "الدمام",
        "KBR": "الخبر",
        "TUU": "تبوك",
        "HAS": "حائل",
        "BUR": "بريدة",
        "SKK": "سكاكا",
        "RAE": "عرعر",
        "TIF": "الطائف",
        "BAH": "الباحة",
        "AHB": "أبها",
        "KMX": "خميس مشيط",
        "EAM": "نجران",
        "GIZ": "جازان",
        "HOF": "الهفوف",
        "YNB": "ينبع",
        "JUB": "الجبيل",
    }
    for shortcut, name_ar in names.items():
        op.execute(
            sa.text("UPDATE cities SET name_ar = :name_ar WHERE shortcut = :shortcut").bindparams(
                name_ar=name_ar, shortcut=shortcut
            )
        )
    op.execute(sa.text("UPDATE cities SET name_ar = name WHERE name_ar IS NULL"))
    op.alter_column("cities", "name_ar", nullable=False)

    op.add_column("users", sa.Column("public_identifier", sa.Integer(), nullable=True))
    op.execute(
        sa.text("""
            CREATE FUNCTION giftly_new_public_identifier() RETURNS integer AS $$
            DECLARE candidate integer;
            DECLARE attempt integer;
            BEGIN
                PERFORM pg_advisory_xact_lock(74719913);
                FOR attempt IN 1..100 LOOP
                    candidate := 1000000 + floor(random() * 9000000)::integer;
                    IF NOT EXISTS (SELECT 1 FROM users WHERE public_identifier = candidate) THEN
                        RETURN candidate;
                    END IF;
                END LOOP;
                RAISE EXCEPTION 'Public identifier space unavailable';
            END;
            $$ LANGUAGE plpgsql VOLATILE
        """)
    )
    op.execute(
        sa.text("""
            WITH numbered AS (
                SELECT id, row_number() OVER (ORDER BY md5(id::text)) - 1 AS position
                FROM users
            )
            UPDATE users AS u
            SET public_identifier =
                1000000 + ((numbered.position * 7 + 3842179) % 9000000)::integer
            FROM numbered WHERE numbered.id = u.id
        """)
    )
    op.create_check_constraint(
        "chk_users_public_identifier", "users", "public_identifier BETWEEN 1000000 AND 9999999"
    )
    op.create_unique_constraint("uq_users_public_identifier", "users", ["public_identifier"])
    op.alter_column(
        "users",
        "public_identifier",
        nullable=False,
        server_default=sa.text("giftly_new_public_identifier()"),
    )
    op.drop_index("uq_users_gateway_customer_identifier", table_name="users")
    op.drop_constraint("chk_users_gateway_customer_identifier", "users", type_="check")
    op.drop_column("users", "gateway_customer_identifier")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS gateway_customer_identifier_seq"))
    op.drop_constraint("chk_rating_range", "users", type_="check")
    op.drop_column("users", "rating")
    op.drop_column("users", "rating_count")
    op.drop_column("users", "avatar_storage_key")


def downgrade() -> None:
    """Restore legacy fields; their historical values cannot be recovered."""
    op.add_column(
        "users", sa.Column("rating", sa.Numeric(2, 1), nullable=False, server_default="5.0")
    )
    op.add_column(
        "users", sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("users", sa.Column("avatar_storage_key", sa.String(512), nullable=True))
    op.create_check_constraint("chk_rating_range", "users", "rating BETWEEN 0.0 AND 5.0")
    op.execute(
        sa.text("CREATE SEQUENCE IF NOT EXISTS gateway_customer_identifier_seq START WITH 1")
    )
    op.add_column(
        "users",
        sa.Column(
            "gateway_customer_identifier",
            sa.String(12),
            nullable=True,
            server_default=sa.text(
                "lpad(nextval('gateway_customer_identifier_seq')::text, 12, '0')"
            ),
        ),
    )
    op.create_check_constraint(
        "chk_users_gateway_customer_identifier",
        "users",
        "gateway_customer_identifier IS NULL OR gateway_customer_identifier ~ '^[0-9]{12}$'",
    )
    op.create_index(
        "uq_users_gateway_customer_identifier",
        "users",
        ["gateway_customer_identifier"],
        unique=True,
        postgresql_where=sa.text("gateway_customer_identifier IS NOT NULL"),
    )
    op.drop_constraint("uq_users_public_identifier", "users", type_="unique")
    op.drop_constraint("chk_users_public_identifier", "users", type_="check")
    op.drop_column("users", "public_identifier")
    op.execute(sa.text("DROP FUNCTION giftly_new_public_identifier()"))
    op.drop_column("cities", "name_ar")
