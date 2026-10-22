import base64
import hashlib
import json
import re

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


def test_payment_return_uses_fixed_deep_link_and_english_fallback() -> None:
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
    assert 'href="giftly://payment/return"' in body
    assert 'window.location.replace("giftly://payment/return")' in body
    assert "attacker.example" not in body
    assert "secret" not in body
    assert "check your payment status" in body
    assert "payment was successful" not in body.lower()
    script = re.search(r"<script>(.*?)</script>", body)
    assert script is not None
    script_hash = base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode()
    assert f"script-src 'sha256-{script_hash}'" in response.headers["content-security-policy"]


def test_payment_return_uses_arabic_fallback_without_reflecting_query() -> None:
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
    assert 'href="giftly://payment/return"' in body
    assert "javascript:" not in body
    assert "English" not in body


def test_association_files_are_exact_and_path_scoped() -> None:
    app = FastAPI()
    app.state.settings = make_test_settings(
        APPLE_APP_IDENTIFIER="A1B2C3D4E5.com.giftly.mobile",
        ANDROID_APPLICATION_ID="com.giftly.mobile",
        ANDROID_SHA256_CERT_FINGERPRINTS=(
            "00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:"
            "00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"
        ),
    )
    app.include_router(payment_return.router)
    apple_request = _request("/.well-known/apple-app-site-association")
    android_request = _request("/.well-known/assetlinks.json")
    apple_request.scope["app"] = app
    android_request.scope["app"] = app
    apple = payment_return.apple_app_site_association(apple_request)
    android = payment_return.android_asset_links(android_request)

    assert apple.status_code == 200
    assert apple.headers["content-type"].startswith("application/json")
    assert json.loads(apple.body) == {
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appID": "A1B2C3D4E5.com.giftly.mobile",
                    "components": [{"/": "/payment/return"}],
                }
            ],
        }
    }
    assert android.status_code == 200
    assert android.headers["content-type"].startswith("application/json")
    assert json.loads(android.body) == [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.giftly.mobile",
                "sha256_cert_fingerprints": [
                    "00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:"
                    "00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"
                ],
            },
        }
    ]


def test_association_files_fail_closed_when_signing_data_is_missing() -> None:
    app = FastAPI()
    app.state.settings = make_test_settings()
    app.include_router(payment_return.router)
    apple_request = _request("/.well-known/apple-app-site-association")
    android_request = _request("/.well-known/assetlinks.json")
    apple_request.scope["app"] = app
    android_request.scope["app"] = app
    apple = payment_return.apple_app_site_association(apple_request)
    android = payment_return.android_asset_links(android_request)

    assert apple.status_code == 503
    assert android.status_code == 503
    assert "giftly.mobile" not in apple.body.decode()
    assert "sha256_cert_fingerprints" not in android.body.decode()


def test_association_files_reject_malformed_signing_data() -> None:
    app = FastAPI()
    app.state.settings = make_test_settings(
        APPLE_APP_IDENTIFIER="A1B2C3D4E5.com.giftly.mobile\n<script>",
        ANDROID_APPLICATION_ID="com.giftly.mobile",
        ANDROID_SHA256_CERT_FINGERPRINTS="not-a-certificate-fingerprint",
    )
    app.include_router(payment_return.router)
    apple_request = _request("/.well-known/apple-app-site-association")
    android_request = _request("/.well-known/assetlinks.json")
    apple_request.scope["app"] = app
    android_request.scope["app"] = app

    assert payment_return.apple_app_site_association(apple_request).status_code == 503
    assert payment_return.android_asset_links(android_request).status_code == 503


def test_return_routes_have_no_database_or_payment_dependencies() -> None:
    route = next(
        route
        for route in payment_return.router.routes
        if getattr(route, "name", None) == "payment_return"
    )
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
