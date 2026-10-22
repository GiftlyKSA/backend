from app.main import create_app
from app.routers import payment_return
from fastapi import FastAPI
from starlette.requests import Request

from tests.conftest import make_test_settings


def _request(path: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path.split("?", maxsplit=1)[0],
        "raw_path": path.split("?", maxsplit=1)[0].encode(),
        "query_string": path.split("?", maxsplit=1)[1].encode() if "?" in path else b"",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "server": ("test", 443),
        "client": ("127.0.0.1", 10000),
    }
    return Request(scope)


def test_payment_return_shows_english_manual_expo_go_instructions() -> None:
    app = FastAPI()
    app.include_router(payment_return.router)
    request = _request(
        "/payment/return?redirect=https://attacker.example&status=success&token=secret",
        {"Accept-Language": "en-US"},
    )
    request.scope["app"] = app
    response = payment_return.payment_return(request)
    body = response.body.decode()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Return to Expo Go manually" in body
    assert "giftly://" not in body
    assert "window.location" not in body
    assert "Open Giftly" not in body
    assert "attacker.example" not in body
    assert "secret" not in body
    assert "payment was successful" not in body.lower()
    assert "script-src" not in response.headers["content-security-policy"]


def test_payment_return_shows_arabic_manual_expo_go_instructions() -> None:
    app = FastAPI()
    app.include_router(payment_return.router)
    request = _request(
        "/payment/return?language=en&deep_link=javascript%3Aalert(1)",
        {"Accept-Language": "ar-SA, en;q=0.5"},
    )
    request.scope["app"] = app
    response = payment_return.payment_return(request)

    assert response.status_code == 200
    body = response.body.decode()
    assert '<html lang="ar" dir="rtl">' in body
    assert "Expo Go" in body
    assert "giftly://" not in body
    assert "javascript:" not in body
    assert "English" not in body


def test_app_association_files_are_deferred() -> None:
    app = FastAPI()
    app.include_router(payment_return.router)
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/.well-known/apple-app-site-association" not in paths
    assert "/.well-known/assetlinks.json" not in paths


def test_return_route_has_no_database_or_payment_dependencies() -> None:
    route = next(route for route in payment_return.router.routes if route.name == "payment_return")
    assert route.methods == {"GET"}
    assert not route.dependant.dependencies


def test_return_and_refresh_openapi_contracts_are_explicit() -> None:
    schema = create_app(make_test_settings(ENVIRONMENT="development")).openapi()
    payment_return_operation = schema["paths"]["/payment/return"]["get"]
    refresh_operation = schema["paths"]["/api/payment-sessions/{intent_id}/refresh"]["post"]

    assert "requestBody" not in payment_return_operation
    assert payment_return_operation["responses"]["200"]["content"].keys() == {"text/html"}
    assert "requestBody" not in refresh_operation
    assert refresh_operation["parameters"] == [
        {
            "name": "intent_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "format": "uuid", "title": "Intent Id"},
        }
    ]
    assert refresh_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PaymentSessionResponse"
    }
