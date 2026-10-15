"""Invalidate customer calendar pages atomically on occasion and order mutations."""

from alembic import op

revision = "0038_occasion_read_cache"
down_revision = "0037_read_cache_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Defer owner invalidation to commit; serialize only its short final lock phase."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""
CREATE FUNCTION giftly_occasion_cache_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 owners uuid[] := ARRAY[]::uuid[];
 owner uuid;
 field_name text := CASE WHEN TG_TABLE_NAME='orders' THEN 'customer_id' ELSE 'user_id' END;
BEGIN
 IF TG_OP='TRUNCATE' THEN
  PERFORM giftly_bump_read_cache('occasions_all','00000000-0000-0000-0000-000000000000');
  RETURN NULL;
 END IF;
 IF TG_OP='UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(7243195821438);
 IF TG_OP<>'INSERT' THEN owners:=array_append(owners,(to_jsonb(OLD)->>field_name)::uuid); END IF;
 IF TG_OP<>'DELETE' THEN owners:=array_append(owners,(to_jsonb(NEW)->>field_name)::uuid); END IF;
 FOR owner IN SELECT DISTINCT value FROM unnest(owners) value
              WHERE value IS NOT NULL ORDER BY value LOOP
  PERFORM giftly_bump_read_cache('occasions',owner);
 END LOOP;
 RETURN NULL;
END $$
""")
    op.execute(
        "SELECT giftly_bump_read_cache('occasions_all','00000000-0000-0000-0000-000000000000')"
    )
    for table in ("occasions", "orders"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_occasion_cache "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION giftly_occasion_cache_changed()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_occasion_cache_truncate AFTER TRUNCATE "
            f"ON {table} EXECUTE FUNCTION giftly_occasion_cache_changed()"
        )


def downgrade() -> None:
    """Restore uncached calendar code before removing these triggers."""
    for table in ("occasions", "orders"):
        op.execute(f"DROP TRIGGER trg_{table}_occasion_cache ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_occasion_cache_truncate ON {table}")
    op.execute("DROP FUNCTION giftly_occasion_cache_changed()")
    op.execute("DELETE FROM read_cache_versions WHERE scope IN ('occasions','occasions_all')")
