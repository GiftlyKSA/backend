"""Store order and courier cities as references to city primary keys.

Revision ID: b1c2d3e4f5a6
Revises: 9e0f1a2b3c4d
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "9e0f1a2b3c4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_city_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "courier_profiles", sa.Column("city_of_residence_id", postgresql.UUID(as_uuid=True))
    )
    op.execute("""
        UPDATE orders AS o SET delivery_city_id = c.id
        FROM cities AS c WHERE o.delivery_city = c.name
    """)
    op.execute("""
        UPDATE courier_profiles AS p SET city_of_residence_id = c.id
        FROM cities AS c WHERE p.city_of_residence = c.name
    """)
    op.alter_column("orders", "delivery_city_id", nullable=False)
    op.alter_column("courier_profiles", "city_of_residence_id", nullable=False)
    op.drop_constraint("fk_orders_delivery_city", "orders", type_="foreignkey")
    op.drop_constraint("fk_courier_profiles_city", "courier_profiles", type_="foreignkey")
    op.drop_index("idx_orders_city_status", table_name="orders")
    op.drop_index("idx_courier_profiles_city_verified", table_name="courier_profiles")
    op.drop_column("orders", "delivery_city")
    op.drop_column("courier_profiles", "city_of_residence")
    op.create_foreign_key(
        "fk_orders_delivery_city_id", "orders", "cities", ["delivery_city_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_courier_profiles_city_id",
        "courier_profiles",
        "cities",
        ["city_of_residence_id"],
        ["id"],
    )
    op.create_index("idx_orders_city_status", "orders", ["delivery_city_id", "status"])
    op.create_index(
        "idx_courier_profiles_city_verified",
        "courier_profiles",
        ["city_of_residence_id", "is_verified"],
    )


def downgrade() -> None:
    op.add_column("orders", sa.Column("delivery_city", sa.String(100)))
    op.add_column("courier_profiles", sa.Column("city_of_residence", sa.String(100)))
    op.execute("""
        UPDATE orders AS o SET delivery_city = c.name
        FROM cities AS c WHERE o.delivery_city_id = c.id
    """)
    op.execute("""
        UPDATE courier_profiles AS p SET city_of_residence = c.name
        FROM cities AS c WHERE p.city_of_residence_id = c.id
    """)
    op.alter_column("orders", "delivery_city", nullable=False)
    op.alter_column("courier_profiles", "city_of_residence", nullable=False)
    op.drop_index("idx_orders_city_status", table_name="orders")
    op.drop_index("idx_courier_profiles_city_verified", table_name="courier_profiles")
    op.drop_constraint("fk_orders_delivery_city_id", "orders", type_="foreignkey")
    op.drop_constraint("fk_courier_profiles_city_id", "courier_profiles", type_="foreignkey")
    op.drop_column("orders", "delivery_city_id")
    op.drop_column("courier_profiles", "city_of_residence_id")
    op.create_foreign_key(
        "fk_orders_delivery_city", "orders", "cities", ["delivery_city"], ["name"]
    )
    op.create_foreign_key(
        "fk_courier_profiles_city", "courier_profiles", "cities", ["city_of_residence"], ["name"]
    )
    op.create_index("idx_orders_city_status", "orders", ["delivery_city", "status"])
    op.create_index(
        "idx_courier_profiles_city_verified",
        "courier_profiles",
        ["city_of_residence", "is_verified"],
    )
