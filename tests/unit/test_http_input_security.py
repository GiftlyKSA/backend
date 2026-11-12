"""HTTP throttle policies and literal user-text rendering."""

from collections import Counter
from importlib import import_module
from pkgutil import iter_modules
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app import main, schemas
from app.admin.router import _TEMPLATES
from app.admin.table_router import _TEMPLATES as TABLE_TEMPLATES
from app.core import deps
from app.core.exceptions import RateLimitedError
from app.core.jwt import create_access_token
from app.core.middleware import register_exception_handlers
from app.schemas.chat import MessageResponse, SendMessageRequest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from tests.conftest import make_test_settings


class CounterRedis:
    """Track each throttle identity and expose the selected window."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.windows: dict[str, int] = {}

    async def eval(self, script: str, key_count: int, key: str, window: int, limit: int) -> int:
        self.counts[key] += 1
        self.windows[key] = window
        return window if self.counts[key] > limit else 0


@pytest.mark.parametrize("authenticated", [True, False])
async def test_http_policy_enforces_identity_specific_limit(authenticated):
    settings = make_test_settings(RATE_LIMIT_ENABLED=True)
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, settings)

    @app.get("/api/example")
    async def example() -> dict[str, bool]:
        return {"ok": True}

    headers = {}
    if authenticated:
        token, _, _ = create_access_token(settings, user_id=uuid4(), role="CUSTOMER")
        headers["Authorization"] = f"Bearer {token}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(100):
            assert (await client.get("/api/example", headers=headers)).status_code == 200
        blocked = await client.get("/api/example", headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"
        assert blocked.json()["error"]["scope"] == "http"
        assert (await client.get("/api/health")).status_code == 404
        assert (await client.options("/api/example", headers=headers)).status_code == 429
    assert list(redis.windows.values()) == [60]


async def test_invalid_bearer_token_uses_anonymous_policy():
    settings = make_test_settings(RATE_LIMIT_ENABLED=True)
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/example", headers={"Authorization": "Bearer invalid"})
    assert redis.windows == {"ratelimit:http:60:ip:127.0.0.1": 60}


@pytest.mark.parametrize(
    ("peer", "identity"),
    [("192.0.2.10", "198.51.100.7"), ("192.0.2.20", "192.0.2.20")],
)
async def test_anonymous_quota_uses_only_a_trusted_proxy_client_address(peer, identity):
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=True))
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=["192.0.2.10"])
    transport = ASGITransport(app=proxy, client=(peer, 12345))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/example", headers={"X-Forwarded-For": "198.51.100.7"})

    assert redis.counts == {f"ratelimit:http:60:ip:{identity}": 1}


async def test_throttle_reuses_only_same_request_signature_and_still_checks_access(monkeypatch):
    settings = make_test_settings(RATE_LIMIT_ENABLED=True)
    app = FastAPI()
    app.state.settings = settings
    app.state.redis = CounterRedis()
    main._install_middleware(app, settings)
    decode = Mock(wraps=deps.decode_access_token)
    validate = AsyncMock()
    monkeypatch.setattr(deps, "decode_access_token", decode)
    monkeypatch.setattr(main, "decode_access_token", decode, raising=False)
    monkeypatch.setattr(deps, "validate_access_claims", validate)
    monkeypatch.setattr(deps, "set_audit_actor", AsyncMock())
    user_id = uuid4()

    @app.get("/api/example")
    async def example(request: main.Request) -> dict[str, str]:
        actor = await deps.require_auth(request, db=AsyncMock())
        return {"id": str(actor.id)}

    token, _, _ = create_access_token(settings, user_id=user_id, role="CUSTOMER")
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            assert (await client.get("/api/example", headers=headers)).json() == {
                "id": str(user_id)
            }
    assert decode.call_count == 2
    assert validate.await_count == 2


@pytest.mark.parametrize("path", ["/v1/admin/admin", "/v1/admin/admin/tables"])
async def test_admin_dashboard_bypasses_general_http_limit(path):
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=True))

    @app.get(path)
    async def example() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(101):
            assert (await client.get(path)).status_code == 200
        assert (await client.get("/v1/admin/admin-extra")).status_code == 404
    assert redis.counts == {"ratelimit:http:60:ip:127.0.0.1": 1}


async def test_optional_endpoint_budgets_use_separate_ip_keys():
    settings = make_test_settings(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_SEPARATE_ENDPOINTS_ENABLED=True,
        RATE_LIMIT_ANONYMOUS_MAX_REQUESTS=1,
        RATE_LIMIT_SIGN_IN_MAX_REQUESTS=1,
        RATE_LIMIT_REFRESH_MAX_REQUESTS=1,
        RATE_LIMIT_PAYMENT_CALLBACK_MAX_REQUESTS=1,
    )
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, settings)

    async def example() -> dict[str, bool]:
        return {"ok": True}

    paths = {
        "/api/example": "http",
        "/api/auth/send-otp": "sign_in",
        "/api/auth/refresh": "token_refresh",
        "/api/webhooks/dhamen": "payment_callback",
    }
    for path in paths:
        app.add_api_route(path, example, methods=["POST"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path, scope in paths.items():
            assert (await client.post(path)).status_code == 200
            blocked = await client.post(path)
            assert blocked.status_code == 429
            assert blocked.json()["error"]["scope"] == scope
    assert len(redis.counts) == len(paths)


async def test_operation_limit_scope_does_not_block_unrelated_http_requests():
    app = FastAPI()
    app.state.redis = CounterRedis()
    main._install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=True))
    register_exception_handlers(app)

    @app.post("/api/orders/cancel")
    async def cancel() -> None:
        raise RateLimitedError(3600, scope="order_cancellation")

    @app.get("/api/profile")
    async def profile() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.post("/api/orders/cancel")
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "3600"
        assert blocked.json()["error"]["scope"] == "order_cancellation"
        assert (await client.get("/api/profile")).status_code == 200


@pytest.mark.parametrize("path", ["/v1/admin/admin/login", "/api/admin", "/api/admin/users"])
async def test_admin_routes_bypass_general_limiter_during_redis_outage(path):
    app = FastAPI()

    class UnavailableRedis:
        async def eval(self, *args: object) -> int:
            raise RedisConnectionError("Redis unavailable")

    app.state.redis = UnavailableRedis()
    main._install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=True))

    @app.post(path)
    async def example() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(path)).status_code == 200
        for public_path in ("/api/example", "/api/admin-extra"):
            unavailable = await client.post(public_path)
            assert unavailable.status_code == 503
            assert unavailable.headers["Retry-After"] == "1"
            assert unavailable.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"


async def test_api_text_is_returned_as_json_with_nosniff():
    app = FastAPI()
    main._install_middleware(app, make_test_settings())

    @app.post("/api/example")
    async def example(body: SendMessageRequest) -> dict[str, str]:
        return {"content": body.text}

    text = '<script>alert(1)</script><svg onload="alert(2)"> &lt;img&gt;'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/example", json={"text": text})
    assert response.status_code == 200
    assert response.json()["content"] == text
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_script_like_text_is_preserved_in_api_and_escaped_in_admin():
    text = '<script>alert("hello")</script><img src=x onerror=alert(1)> & العربية'
    request = SendMessageRequest(text=text)
    response = MessageResponse(
        id=str(uuid4()),
        conversation_id=str(uuid4()),
        sender_id=str(uuid4()),
        message_type="TEXT",
        content=request.text,
        is_read=False,
        created_at="2026-10-01T00:00:00+00:00",
    )
    assert MessageResponse.model_validate_json(response.model_dump_json()).content == text
    template = _TEMPLATES.env.get_template("table_browser.html")
    rendered = template.render(
        tr=lambda value: value,
        request=SimpleNamespace(url=SimpleNamespace(path="/v1/admin/admin/tables/users")),
        data=SimpleNamespace(
            table=SimpleNamespace(name="users", title="Users"),
            columns=["full_name"],
            rows=[SimpleNamespace(cells=[text], edit_url=None)],
            edit_column="id",
            filters=[],
            date_columns=[],
        ),
        filters=SimpleNamespace(page_size=25, direction="desc", sort_by="", filter_field=""),
    )
    assert "<script>" not in rendered
    assert "<img src=x" not in rendered
    assert "&lt;script&gt;" in rendered


def test_all_request_models_reject_undeclared_properties() -> None:
    models = []
    for module in iter_modules(schemas.__path__):
        for value in vars(import_module(f"app.schemas.{module.name}")).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__name__.endswith("Request")
            ):
                models.append(value)
    assert models
    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__


@pytest.mark.parametrize("templates", [_TEMPLATES, TABLE_TEMPLATES])
def test_admin_templates_escape_text_and_attribute_values(templates) -> None:
    text = '"><script>alert(1)</script>'
    rendered = templates.env.from_string('<input value="{{ value }}">{{ value }}').render(
        value=text
    )
    assert "<script>" not in rendered
    assert "&#34;&gt;&lt;script&gt;" in rendered


@pytest.mark.parametrize(
    "field",
    [
        "RATE_LIMIT_MAX_REQUESTS",
        "RATE_LIMIT_WINDOW_SECONDS",
        "RATE_LIMIT_ANONYMOUS_MAX_REQUESTS",
        "RATE_LIMIT_ANONYMOUS_WINDOW_SECONDS",
    ],
)
def test_http_throttle_settings_cannot_disable_limits_with_zero(field) -> None:
    with pytest.raises(ValidationError):
        make_test_settings(**{field: 0})
