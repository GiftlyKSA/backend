"""Invalidate private order summaries on committed order and rating mutations."""

from alembic import op

revision = "0039_order_read_cache"
down_revision = "0038_occasion_read_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Share the calendar commit gate; keep normal invalidation participant-scoped."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""
CREATE FUNCTION giftly_order_cache_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 owners uuid[] := ARRAY[]::uuid[];
 participants uuid[] := ARRAY[]::uuid[];
 related_orders uuid[] := ARRAY[]::uuid[];
 owner uuid;
BEGIN
 IF TG_OP='TRUNCATE' THEN
  PERFORM giftly_bump_read_cache('orders_all','00000000-0000-0000-0000-000000000000');
  RETURN NULL;
 END IF;
 IF TG_OP='UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='orders' THEN
  IF TG_OP<>'INSERT' THEN
   owners:=owners || ARRAY[(to_jsonb(OLD)->>'customer_id')::uuid,
                           (to_jsonb(OLD)->>'courier_id')::uuid];
  END IF;
  IF TG_OP<>'DELETE' THEN
   owners:=owners || ARRAY[(to_jsonb(NEW)->>'customer_id')::uuid,
                           (to_jsonb(NEW)->>'courier_id')::uuid];
  END IF;
 ELSE
  IF TG_OP<>'INSERT' THEN
   related_orders:=array_append(related_orders,(to_jsonb(OLD)->>'order_id')::uuid);
   owners:=array_append(owners,(to_jsonb(OLD)->>'rater_id')::uuid);
  END IF;
  IF TG_OP<>'DELETE' THEN
   related_orders:=array_append(related_orders,(to_jsonb(NEW)->>'order_id')::uuid);
   owners:=array_append(owners,(to_jsonb(NEW)->>'rater_id')::uuid);
  END IF;
  SELECT array_agg(participant) INTO participants FROM public.orders o
   CROSS JOIN LATERAL unnest(ARRAY[o.customer_id,o.courier_id]) participant
   WHERE o.id=ANY(related_orders);
  owners:=owners || COALESCE(participants,ARRAY[]::uuid[]);
 END IF;
 PERFORM pg_advisory_xact_lock(7243195821438);
 FOR owner IN SELECT DISTINCT value FROM unnest(owners) value
              WHERE value IS NOT NULL ORDER BY value LOOP
  PERFORM giftly_bump_read_cache('orders',owner);
 END LOOP;
 RETURN NULL;
END $$
""")
    op.execute("SELECT giftly_bump_read_cache('orders_all','00000000-0000-0000-0000-000000000000')")
    for table in ("orders", "ratings"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table}_order_cache "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION giftly_order_cache_changed()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_order_cache_truncate AFTER TRUNCATE "
            f"ON {table} EXECUTE FUNCTION giftly_order_cache_changed()"
        )


def downgrade() -> None:
    """Restore uncached list code before removing participant invalidation."""
    for table in ("orders", "ratings"):
        op.execute(f"DROP TRIGGER trg_{table}_order_cache ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_order_cache_truncate ON {table}")
    op.execute("DROP FUNCTION giftly_order_cache_changed()")
    op.execute("DELETE FROM read_cache_versions WHERE scope IN ('orders','orders_all')")
