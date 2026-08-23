"""Fast schema checks for the mobile participant and courier contracts."""

from app.schemas import users
from app.schemas.orders import OrderDetail, OrderSummary
from app.schemas.users import UserMeResponse


def test_mobile_contract_schemas_expose_role_scoped_fields() -> None:
    """Removing any mobile-visible field must break the generated API contract."""
    assert "current_actor_has_rated" in OrderSummary.model_json_schema()["properties"]
    assert "current_actor_has_rated" in OrderDetail.model_json_schema()["properties"]
    assert "courier_profile" in UserMeResponse.model_json_schema()["properties"]
    assert hasattr(users, "ParticipantProfile")
    participant_profile = users.ParticipantProfile
    assert set(participant_profile.model_json_schema()["properties"]) == {
        "id",
        "display_name",
        "role",
        "rating",
        "rating_count",
        "initials",
        "avatar_url",
        "courier_city",
        "courier_bio",
    }
