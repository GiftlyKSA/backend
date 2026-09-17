"""Offline regression for enum constraints during migration rollback."""

import io

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.migrations.versions import c9d0e1f2a3b4_add_dhamen_and_mobile_contracts as migration


def test_media_constraint_is_rebuilt_around_enum_replacement() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.downgrade()
    statements = output.getvalue()
    drop = statements.index("DROP CONSTRAINT chk_proof_has_location")
    conversion = statements.index("ALTER COLUMN media_type TYPE media_type_previous")
    rename = statements.index("ALTER TYPE media_type_previous RENAME TO media_type")
    restore = statements.index("ADD CONSTRAINT chk_proof_has_location CHECK")
    assert drop < conversion < rename < restore
    assert "media_type <> 'DELIVERY_PROOF' OR capture_location IS NOT NULL" in statements
