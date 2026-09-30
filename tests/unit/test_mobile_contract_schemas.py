"""Fast schema checks for the mobile participant and courier contracts."""

from app.models import Base
from app.schemas import users
from app.schemas.orders import OrderDetail, OrderSummary
from app.schemas.users import UserMeResponse


def test_mobile_contract_schemas_expose_role_scoped_fields() -> None:
    """Removing any mobile-visible field must break the generated API contract."""
    assert "current_actor_has_rated" in OrderSummary.model_json_schema()["properties"]
    assert "current_actor_has_rated" in OrderDetail.model_json_schema()["properties"]
    assert "courier_profile" in UserMeResponse.model_json_schema()["properties"]
    assert "public_identifier" in UserMeResponse.model_json_schema()["properties"]
    assert "rating" not in UserMeResponse.model_json_schema()["properties"]
    assert "rating_count" not in UserMeResponse.model_json_schema()["properties"]
    assert hasattr(users, "ParticipantProfile")
    participant_profile = users.ParticipantProfile
    assert set(participant_profile.model_json_schema()["properties"]) == {
        "id",
        "public_identifier",
        "display_name",
        "role",
        "rating",
        "rating_count",
        "initials",
        "avatar_url",
        "courier_city",
        "courier_bio",
    }


def test_public_identifier_is_database_generated_and_unique() -> None:
    users_table = Base.metadata.tables["users"]
    identifier = users_table.c.public_identifier
    assert identifier.server_default is not None
    assert not identifier.nullable
    assert any(
        constraint.name == "uq_users_public_identifier" for constraint in users_table.constraints
    )
    assert "gateway_customer_identifier" not in users_table.c
    assert "rating" not in users_table.c
    assert "rating_count" not in users_table.c
    assert "avatar_storage_key" not in users_table.c
