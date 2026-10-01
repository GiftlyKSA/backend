"""HTTP throttle policies and literal user-text rendering."""

from collections import Counter
from importlib import import_module
from pkgutil import iter_modules
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app import main, schemas
from app.admin.router import _TEMPLATES
from app.admin.table_router import _TEMPLATES as TABLE_TEMPLATES
from app.core.jwt import create_access_token
from app.schemas.chat import MessageResponse, SendMessageRequest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError

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


@pytest.mark.parametrize("authenticated,limit,window", [(True, 60, 60), (False, 30, 3600)])
async def test_http_policy_enforces_identity_specific_limit(authenticated, limit, window):
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
        for _ in range(limit):
            assert (await client.get("/api/example", headers=headers)).status_code == 200
        blocked = await client.get("/api/example", headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == str(window)
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"
        assert (await client.get("/api/health")).status_code == 404
        assert (await client.options("/api/example")).status_code == 405
    assert list(redis.windows.values()) == [window]


async def test_invalid_bearer_token_uses_anonymous_policy():
    settings = make_test_settings(RATE_LIMIT_ENABLED=True)
    app = FastAPI()
    redis = CounterRedis()
    app.state.redis = redis
    main._install_middleware(app, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/example", headers={"Authorization": "Bearer invalid"})
    assert redis.windows == {"ratelimit:ip:127.0.0.1": 3600}


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
