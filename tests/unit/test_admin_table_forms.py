from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.models import Base
from app.services.admin_table_fields import form_fields, parse_values


def test_every_foreign_key_gets_a_relationship_widget():
    for table in Base.metadata.tables.values():
        fields = {field.name: field for field in form_fields(table, None)}
        for column in table.c:
            if column.foreign_keys:
                assert fields[column.name].kind == "relationship"


def test_media_upload_grants_have_uuid_admin_records_and_editable_fields():
    table = Base.metadata.tables["media_uploads"]
    assert [column.name for column in table.primary_key.columns] == ["id"]
    fields = {field.name: field for field in form_fields(table, None)}
    assert fields["owner_user_id"].kind == "relationship"
    assert fields["storage_key"].required
    values = parse_values(
        table,
        {
            "storage_key": "orders/pending/example.jpg",
            "owner_user_id": str(uuid4()),
            "purpose": "ORDER_REQUEST",
            "content_type": "image/jpeg",
            "byte_size": "1000",
        },
        creating=True,
    )
    assert values["byte_size"] == 1000


def test_money_is_exact_and_extra_precision_is_rejected():
    table = Base.metadata.tables["wallets"]
    assert parse_values(table, {"balance": "12.34"}, creating=False)["balance"] == Decimal("12.34")
    for raw in ("NaN", "Infinity", "1.001", "1e1000000"):
        with pytest.raises(ValidationDomainError):
            parse_values(table, {"balance": raw}, creating=False)


def test_unknown_fields_and_generated_columns_are_rejected():
    table = Base.metadata.tables["users"]
    for name in ("not_a_field", "created_at", "id"):
        with pytest.raises(ValidationDomainError):
            parse_values(table, {name: "anything"}, creating=False)


def test_generated_id_and_audit_timestamps_are_visible_but_read_only_on_edit():
    table = Base.metadata.tables["users"]
    now = datetime(2026, 9, 29, 12, 34, 56, tzinfo=UTC)
    record_id = uuid4()
    fields = {
        field.name: field
        for field in form_fields(
            table,
            {
                "id": record_id,
                "created_at": now,
                "updated_at": now,
                "deleted_at": now,
            },
        )
    }
    assert fields["id"].readonly
    assert fields["id"].value == str(record_id)
    for name in ("created_at", "updated_at", "deleted_at"):
        assert fields[name].readonly
        assert fields[name].kind == "datetime"
        assert "12:34:56" in fields[name].value
        with pytest.raises(ValidationDomainError):
            parse_values(table, {name: now.isoformat()}, creating=False)

    create_fields = {field.name for field in form_fields(table, None)}
    assert not {"id", "created_at", "updated_at", "deleted_at"} & create_fields


def test_promo_datetime_picker_submits_supported_timezone_aware_format():
    table = Base.metadata.tables["promos"]
    values = parse_values(
        table,
        {"starts_at": "2026-10-01T09:30:00.000Z", "ends_at": "2026-10-02T09:30:00.000Z"},
        creating=False,
    )
    assert values["starts_at"] == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    assert values["ends_at"] == datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    with pytest.raises(ValidationDomainError):
        parse_values(table, {"starts_at": "2026-10-01T12:30"}, creating=False)


def test_secrets_are_write_only_and_blank_edit_keeps_them():
    table = Base.metadata.tables["refresh_tokens"]
    fields = form_fields(table, {"token_hash": "private", "user_id": None})
    assert next(field for field in fields if field.name == "token_hash").value == ""
    assert parse_values(table, {"token_hash": ""}, creating=False) == {}


def test_blank_creation_uses_defaults_and_nullable_fields_allow_explicit_null():
    table = Base.metadata.tables["users"]
    values = parse_values(table, {"phone": "123", "role": "CUSTOMER", "status": ""}, creating=True)
    assert "status" not in values
    assert parse_values(table, {"email": "", "null__email": "1"}, creating=False) == {"email": None}
    with pytest.raises(ValidationDomainError):
        parse_values(table, {"null__phone": "1"}, creating=False)


def test_invalid_boolean_enum_json_and_date_rejected():
    for table, name, raw in (
        ("featured_gifts", "is_active", "maybe"),
        ("users", "role", "ROOT"),
        ("audit_logs", "metadata", "[invalid"),
        ("audit_logs", "metadata", '{"value": 1e9999}'),
        ("users", "date_of_birth", "2026-02-31"),
    ):
        with pytest.raises(ValidationDomainError):
            parse_values(Base.metadata.tables[table], {name: raw}, creating=False)


def test_json_scalar_strings_roundtrip():
    table = Base.metadata.tables["audit_logs"]
    field = next(
        field for field in form_fields(table, {"metadata": "value"}) if field.name == "metadata"
    )
    assert parse_values(table, {"metadata": field.value}, creating=False) == {"metadata": "value"}
