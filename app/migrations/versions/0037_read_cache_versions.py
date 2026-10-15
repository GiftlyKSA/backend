"""Transactional versions for cacheable public catalogs, independent of Redis availability."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_read_cache_versions"
down_revision = "0036_admin_financial_chronology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rotate catalog versions in the same transaction as every catalog mutation."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.create_table(
        "read_cache_versions",
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('catalog','occasions','occasions_all','orders','orders_all')",
            name="chk_read_cache_scope",
        ),
    )
    op.execute("""
CREATE FUNCTION giftly_bump_read_cache(cache_scope text, owner uuid) RETURNS void
LANGUAGE sql AS $$
 INSERT INTO public.read_cache_versions(scope,owner_id,version)
 VALUES(cache_scope,owner,gen_random_uuid())
 ON CONFLICT(scope,owner_id) DO UPDATE SET version=EXCLUDED.version
$$
""")
    op.execute("""
CREATE FUNCTION giftly_catalog_cache_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NULL; END IF;
 PERFORM giftly_bump_read_cache('catalog','00000000-0000-0000-0000-000000000000');
 RETURN NULL;
END $$;
""")
    op.execute("SELECT giftly_bump_read_cache('catalog', '00000000-0000-0000-0000-000000000000')")
    for table in ("cities", "featured_gifts"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_read_cache AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION giftly_catalog_cache_changed()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_read_cache_truncate AFTER TRUNCATE "
            f"ON {table} EXECUTE FUNCTION giftly_catalog_cache_changed()"
        )


def downgrade() -> None:
    """Remove only cache infrastructure; old application code remains uncached."""
    for table in ("cities", "featured_gifts"):
        op.execute(f"DROP TRIGGER trg_{table}_read_cache ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_read_cache_truncate ON {table}")
    op.execute("DROP FUNCTION giftly_catalog_cache_changed()")
    op.execute("DROP FUNCTION giftly_bump_read_cache(text,uuid)")
    op.drop_table("read_cache_versions")
