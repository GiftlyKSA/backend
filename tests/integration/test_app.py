"""Integration tests for the app skeleton, health, and dev-route gating."""

from __future__ import annotations

from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from tests.conftest import make_test_settings


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_liveness(test_settings: Settings) -> None:
    with _client(test_settings) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "X-Request-ID" in response.headers


def test_security_headers_present(test_settings: Settings) -> None:
    with _client(test_settings) as client:
        response = client.get("/api/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


def test_docs_disabled_in_test(test_settings: Settings) -> None:
    with _client(test_settings) as client:
        assert client.get("/openapi.json").status_code == 404


def test_dev_routes_absent_outside_development(test_settings: Settings) -> None:
    with _client(test_settings) as client:
        assert client.get("/api/dev/ping").status_code == 404


def test_dev_routes_present_in_development() -> None:
    settings = make_test_settings(ENVIRONMENT="development")
    with _client(settings) as client:
        assert client.get("/api/dev/ping").status_code == 200


def test_development_cors_accepts_any_origin_and_preflight() -> None:
    settings = make_test_settings(ENVIRONMENT="development")
    with _client(settings) as client:
        response = client.get("/api/health", headers={"Origin": "https://any.example"})
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        preflight = client.options(
            "/api/auth/send-otp",
            headers={
                "Origin": "https://any.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["Access-Control-Allow-Origin"] == "*"


def test_non_development_cors_does_not_allow_arbitrary_origin(test_settings: Settings) -> None:
    with _client(test_settings) as client:
        response = client.get("/api/health", headers={"Origin": "https://any.example"})
        assert "Access-Control-Allow-Origin" not in response.headers
