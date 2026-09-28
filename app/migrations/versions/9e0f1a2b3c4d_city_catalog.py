"""Create the city catalog and protect existing order/profile city references.

Revision ID: 9e0f1a2b3c4d
Revises: a7b8c9d0e1f2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9e0f1a2b3c4d"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_DEFAULT_CITIES = (
    ("Riyadh", "RUH"),
    ("Jeddah", "JED"),
    ("Makkah", "MKK"),
    ("Madinah", "MED"),
    ("Dammam", "DMM"),
    ("Al Khobar", "KBR"),
    ("Tabuk", "TUU"),
    ("Hail", "HAS"),
    ("Buraydah", "BUR"),
    ("Sakaka", "SKK"),
    ("Arar", "RAE"),
    ("Taif", "TIF"),
    ("Al Baha", "BAH"),
    ("Abha", "AHB"),
    ("Khamis Mushayt", "KMX"),
    ("Najran", "EAM"),
    ("Jazan", "GIZ"),
    ("Al Hofuf", "HOF"),
    ("Yanbu", "YNB"),
    ("Jubail", "JUB"),
)


def upgrade() -> None:
    cities = op.create_table(
        "cities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("shortcut", sa.String(16), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.bulk_insert(
        cities,
        [
            {"name": name, "shortcut": shortcut, "is_active": True}
            for name, shortcut in _DEFAULT_CITIES
        ],
    )
    op.execute("""
        INSERT INTO cities (name, shortcut, is_active)
        SELECT name, 'LEG' || lpad(row_number() OVER (ORDER BY name)::text, 9, '0'), true
        FROM (
            SELECT delivery_city AS name FROM orders
            UNION
            SELECT city_of_residence AS name FROM courier_profiles
        ) existing
        WHERE NOT EXISTS (SELECT 1 FROM cities WHERE cities.name = existing.name)
        ON CONFLICT (name) DO NOTHING
    """)
    op.create_foreign_key(
        "fk_orders_delivery_city", "orders", "cities", ["delivery_city"], ["name"]
    )
    op.create_foreign_key(
        "fk_courier_profiles_city", "courier_profiles", "cities", ["city_of_residence"], ["name"]
    )
    op.create_index("idx_refresh_tokens_expiry", "refresh_tokens", ["expires_at", "id"])


def downgrade() -> None:
    op.drop_index("idx_refresh_tokens_expiry", table_name="refresh_tokens")
    op.drop_constraint("fk_courier_profiles_city", "courier_profiles", type_="foreignkey")
    op.drop_constraint("fk_orders_delivery_city", "orders", type_="foreignkey")
    op.drop_table("cities")
