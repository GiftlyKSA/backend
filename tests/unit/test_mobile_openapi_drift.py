"""Keep the mobile handoff's wire contract aligned with implemented API routes."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

from tests.conftest import make_test_settings

_MOBILE_SPEC = Path(__file__).resolve().parents[2] / "docs" / "mobile-openapi.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


def test_mobile_openapi_matches_non_admin_wire_contract() -> None:
    """Catch route, body, response, or shared-schema drift before UI handoff."""
    generated = create_app(make_test_settings(ENVIRONMENT="development")).openapi()
    mobile = json.loads(_MOBILE_SPEC.read_text(encoding="utf-8"))
    operations = {
        (path, method): operation
        for path, path_operations in generated["paths"].items()
        if not path.startswith(("/api/admin/", "/api/dev/", "/api/webhooks/"))
        for method, operation in path_operations.items()
        if method in _METHODS
    }
    documented = {
        (path, method): operation
        for path, path_operations in mobile["paths"].items()
        for method, operation in path_operations.items()
        if method in _METHODS
    }
    assert documented.keys() == operations.keys()
    for route, operation in operations.items():
        for field in ("operationId", "parameters", "requestBody", "responses"):
            assert documented[route].get(field) == operation.get(field), (route, field)

    generated_schemas = generated["components"]["schemas"]
    mobile_schemas = mobile["components"]["schemas"]
    assert mobile_schemas.keys() <= generated_schemas.keys()
    for name, schema in mobile_schemas.items():
        assert schema == generated_schemas[name], name
