"""Mobile participant, courier-profile, rejection, and rating API contracts."""

from __future__ import annotations

import errno
import os
import secrets
import socket
import uuid
from datetime import date, timedelta

import pytest
from app.core.config import Settings
from app.core.db import build_engine, build_session_factory
from app.main import create_app
from app.models import CourierProfile, Order, User
from app.models.enums import OrderStatus, UserStatus
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from tests.conftest import make_test_settings

_CONNECTION_ERRNOS = frozenset(
    value
    for name in (
        "ECONNREFUSED",
        "ECONNRESET",
        "ECONNABORTED",
        "ETIMEDOUT",
        "ENETUNREACH",
        "EHOSTUNREACH",
    )
    if (value := getattr(errno, name, None)) is not None
)
_CONNECTION_WINERRORS = frozenset({10051, 10060, 10061, 10065, 11001, 1225})


def _settings() -> Settings:
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    if os.environ.get("REDIS_URL"):
        overrides["REDIS_URL"] = os.environ["REDIS_URL"]
    return make_test_settings(**overrides)


def _phone() -> str:
    return f"+96650{secrets.randbelow(10_000_000):07d}"


def _future() -> str:
    return (date.today() + timedelta(days=30)).isoformat()


async def _register(
    client: AsyncClient,
    app: object,
    *,
    role: str,
    full_name: str,
    city: str | None = None,
) -> tuple[str, dict[str, object]]:
    phone = _phone()
    await client.post("/api/auth/send-otp", json={"phone": phone})
    otp = app.state.clients.sms.last_otp[phone]  # type: ignore[attr-defined]
    verify = await client.post("/api/auth/verify-otp", json={"phone": phone, "otp": otp})
    body: dict[str, object] = {
        "registration_token": verify.json()["registration_token"],
        "role": role,
        "full_name": full_name,
    }
    if role == "COURIER":
        body.update(city=city or "Jeddah", national_id=_phone()[1:])
    response = await client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return phone, response.json()


async def _login(client: AsyncClient, app: object, phone: str) -> dict[str, object]:
    await client.post("/api/auth/send-otp", json={"phone": phone})
    otp = app.state.clients.sms.last_otp[phone]  # type: ignore[attr-defined]
    response = await client.post("/api/auth/verify-otp", json={"phone": phone, "otp": otp})
    assert response.status_code == 200, response.text
    return response.json()


def _headers(tokens: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _activate_courier(factory: object, phone: str, *, bio: str | None = None) -> User:
    async with factory() as session:  # type: ignore[operator]
        user = await session.scalar(select(User).where(User.phone == phone))
        assert user is not None
        profile = await session.get(CourierProfile, user.id)
        assert profile is not None
        profile.is_verified = True
        profile.bio = bio
        user.status = UserStatus.ACTIVE
        await session.commit()
        return user


async def _create_order(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/orders",
        headers=headers,
        json={
            "delivery_city": "Jeddah",
            "latitude": 21.5433,
            "longitude": 39.1728,
            "delivery_date": _future(),
            "request_media_keys": [],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _raise_or_skip_database_unavailable(exc: Exception) -> None:
    """Skip the DB-backed contract when its connection is unavailable."""
    candidate: BaseException = exc
    if isinstance(exc, OperationalError):
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        if isinstance(sqlstate, str):
            if sqlstate.startswith("08"):
                pytest.skip(f"database unavailable: {exc}")
            raise exc
        candidate = original

    if isinstance(candidate, OSError) and (
        isinstance(candidate, (ConnectionRefusedError, TimeoutError, socket.gaierror))
        or candidate.errno in _CONNECTION_ERRNOS
        or getattr(candidate, "winerror", None) in _CONNECTION_WINERRORS
        or candidate.errno in _CONNECTION_WINERRORS
    ):
        pytest.skip(f"database unavailable: {exc}")
    raise exc


async def _stack() -> tuple[Settings, object, object]:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except (OSError, OperationalError) as exc:
        await engine.dispose()
        _raise_or_skip_database_unavailable(exc)
    return settings, engine, factory


async def test_courier_order_list_is_assignment_scoped() -> None:
    """A courier history query must not return another courier's assigned order."""
    settings, engine, factory = await _stack()
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            _customer_phone, customer = await _register(
                client, app, role="CUSTOMER", full_name="Order Owner"
            )
            courier_phone, _pending = await _register(
                client, app, role="COURIER", full_name="Assigned Courier"
            )
            other_phone, _other_pending = await _register(
                client, app, role="COURIER", full_name="Other Courier"
            )
            await _activate_courier(factory, courier_phone)
            await _activate_courier(factory, other_phone)
            courier = await _login(client, app, courier_phone)
            other = await _login(client, app, other_phone)

            owned_id = await _create_order(client, _headers(customer))
            other_id = await _create_order(client, _headers(customer))
            assert (
                await client.post(f"/api/orders/{owned_id}/accept", headers=_headers(courier))
            ).status_code == 200
            assert (
                await client.post(f"/api/orders/{other_id}/accept", headers=_headers(other))
            ).status_code == 200

            response = await client.get("/api/orders", headers=_headers(courier))
            assert response.status_code == 200, response.text
            assert [row["id"] for row in response.json()["items"]] == [owned_id]
            assert response.json()["items"][0]["current_actor_has_rated"] is False
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        await app.state.engine.dispose()


async def test_participant_profile_requires_shared_order_and_is_minimal() -> None:
    """Only a co-participant may see the compact, non-sensitive profile projection."""
    settings, engine, factory = await _stack()
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            _customer_phone, customer = await _register(
                client, app, role="CUSTOMER", full_name="Nora Customer"
            )
            _stranger_phone, stranger = await _register(
                client, app, role="CUSTOMER", full_name="Unrelated User"
            )
            courier_phone, _pending = await _register(
                client, app, role="COURIER", full_name="Cora Driver", city="Jeddah"
            )
            courier_user = await _activate_courier(
                factory, courier_phone, bio="Careful with fragile gifts."
            )
            courier = await _login(client, app, courier_phone)
            order_id = await _create_order(client, _headers(customer))
            accepted = await client.post(
                f"/api/orders/{order_id}/accept", headers=_headers(courier)
            )
            assert accepted.status_code == 200, accepted.text

            allowed = await client.get(
                f"/api/users/{courier_user.id}/participant", headers=_headers(customer)
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json() == {
                "id": str(courier_user.id),
                "display_name": "Cora Driver",
                "role": "COURIER",
                "rating": "5.0",
                "rating_count": 0,
                "initials": "CD",
                "avatar_url": None,
                "courier_city": "Jeddah",
                "courier_bio": "Careful with fragile gifts.",
            }

            denied = await client.get(
                f"/api/users/{courier_user.id}/participant", headers=_headers(stranger)
            )
            assert denied.status_code == 404
            sensitive = {
                "phone",
                "email",
                "dob",
                "date_of_birth",
                "national_id",
                "passport_id",
                "identity_fingerprint",
                "payout_iban",
                "avatar_storage_key",
                "gateway_customer_identifier",
                "gateway_supplier_id",
                "delivery_location",
            }
            assert sensitive.isdisjoint(allowed.json())
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        await app.state.engine.dispose()


async def test_self_participant_lookup_still_requires_shared_relationship() -> None:
    """The participant path is not an alternate generic lookup for the actor's own row."""
    settings, engine, _factory = await _stack()
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            _phone_value, customer = await _register(
                client, app, role="CUSTOMER", full_name="Self Lookup"
            )
            me = await client.get("/api/users/me", headers=_headers(customer))
            response = await client.get(
                f"/api/users/{me.json()['id']}/participant", headers=_headers(customer)
            )
            assert response.status_code == 404
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        await app.state.engine.dispose()


async def test_courier_me_exposes_safe_profile_and_rejected_account_cannot_use_actions() -> None:
    """A rejected courier can sign in and inspect the reason, but cannot use the radar."""
    settings, engine, factory = await _stack()
    app = create_app(settings)
    courier_phone = ""
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            _customer_phone, customer = await _register(
                client, app, role="CUSTOMER", full_name="Courier Order Customer"
            )
            courier_phone, _pending = await _register(
                client, app, role="COURIER", full_name="Rejected Courier", city="Jeddah"
            )
            user = await _activate_courier(factory, courier_phone)
            active = await _login(client, app, courier_phone)
            order_id = await _create_order(client, _headers(customer))
            accepted = await client.post(f"/api/orders/{order_id}/accept", headers=_headers(active))
            assert accepted.status_code == 200, accepted.text
            patched = await client.patch(
                "/api/users/me",
                headers=_headers(active),
                json={"courier_city": "Riyadh", "courier_bio": "Gift specialist."},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["courier_profile"] == {
                "city_of_residence": "Riyadh",
                "bio": "Gift specialist.",
                "verification_status": "ACTIVE",
                "rejection_reason": None,
                "avatar_url": None,
            }

            async with factory() as session:  # type: ignore[operator]
                stored_user = await session.get(User, user.id)
                profile = await session.get(CourierProfile, user.id)
                assert stored_user is not None and profile is not None
                stored_user.status = UserStatus.REJECTED
                profile.is_verified = False
                profile.verification_rejection_reason = "Identity image is unreadable."
                await session.commit()

            rejected = await _login(client, app, courier_phone)
            profile_response = await client.get("/api/users/me", headers=_headers(rejected))
            assert profile_response.status_code == 200, profile_response.text
            assert profile_response.json()["status"] == "REJECTED"
            assert profile_response.json()["courier_profile"]["rejection_reason"] == (
                "Identity image is unreadable."
            )
            radar = await client.get("/api/orders/available", headers=_headers(rejected))
            assert radar.status_code == 403
            detail = await client.get(f"/api/orders/{order_id}", headers=_headers(rejected))
            assert detail.status_code == 403
            cancel = await client.post(
                f"/api/orders/{order_id}/cancel",
                headers=_headers(rejected),
                json={"reason": "not permitted"},
            )
            assert cancel.status_code == 403
            invoice = await client.post(
                f"/api/orders/{order_id}/invoices",
                headers=_headers(rejected),
                json={
                    "items": [
                        {
                            "title": "Gift",
                            "unit_price_amount": "100.00",
                            "quantity": 1,
                            "tax_rate": "0.15",
                        }
                    ],
                    "courier_fee_amount": "10.00",
                },
            )
            assert invoice.status_code == 403
            withdrawal = await client.post(
                "/api/wallets/withdrawals",
                headers={**_headers(rejected), "Idempotency-Key": str(uuid.uuid4())},
                json={"amount": "100.00", "iban": "SA0380000000608010167519"},
            )
            assert withdrawal.status_code == 403
            resubmitted = await client.post(
                "/api/users/me/courier-verification/resubmit", headers=_headers(rejected)
            )
            assert resubmitted.status_code == 200, resubmitted.text
            assert resubmitted.json()["status"] == "PENDING_VERIFICATION"
            assert resubmitted.json()["courier_profile"]["rejection_reason"] is None
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        await app.state.engine.dispose()


async def test_order_rating_state_is_actor_specific_and_authoritative() -> None:
    """Rating visibility changes only for the participant who inserted the rating."""
    settings, engine, factory = await _stack()
    app = create_app(settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            _customer_phone, customer = await _register(
                client, app, role="CUSTOMER", full_name="Rating Customer"
            )
            courier_phone, _pending = await _register(
                client, app, role="COURIER", full_name="Rating Courier"
            )
            await _activate_courier(factory, courier_phone)
            courier = await _login(client, app, courier_phone)
            order_id = await _create_order(client, _headers(customer))
            await client.post(f"/api/orders/{order_id}/accept", headers=_headers(courier))
            async with factory() as session:  # type: ignore[operator]
                order = await session.get(Order, order_id)
                assert order is not None
                order.status = OrderStatus.COMPLETED
                await session.commit()

            before = await client.get(f"/api/orders/{order_id}", headers=_headers(customer))
            assert before.status_code == 200, before.text
            assert before.json()["current_actor_has_rated"] is False
            rated = await client.post(
                f"/api/orders/{order_id}/ratings",
                headers=_headers(customer),
                json={"score": 5, "comment": "Excellent."},
            )
            assert rated.status_code == 201, rated.text
            customer_after = await client.get(f"/api/orders/{order_id}", headers=_headers(customer))
            courier_after = await client.get(f"/api/orders/{order_id}", headers=_headers(courier))
            assert customer_after.json()["current_actor_has_rated"] is True
            assert courier_after.json()["current_actor_has_rated"] is False
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
        await app.state.engine.dispose()
