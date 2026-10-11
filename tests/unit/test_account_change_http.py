"""The request receipt can inspect status but cannot authenticate ordinary APIs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.core.deps import get_db
from app.main import create_app
from app.routers import account_changes
from fastapi.testclient import TestClient

from tests.conftest import make_test_settings


def test_restricted_account_receipt_is_not_a_normal_bearer_token(monkeypatch) -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        id=uuid4(),
        kind="DELETE_ACCOUNT",
        status="RECOVERABLE",
        created_at=now,
        updated_at=now,
        restore_deadline=now + timedelta(days=14),
    )
    status = AsyncMock(return_value=row)
    app = create_app(make_test_settings())
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(
        account_changes, "_service", lambda request, db: SimpleNamespace(get_account_request=status)
    )
    receipt = "r" * 43
    with TestClient(app) as client:
        response = client.get(
            "/api/users/me/account-request", headers={"X-Account-Request-Receipt": receipt}
        )
        assert response.status_code == 200
        assert response.json()["id"] == str(row.id)
        assert "receipt" not in response.json()
        assert response.json()["administrative_review_required"] is True
        status.assert_awaited_once_with(receipt=receipt)
        assert (
            client.get("/api/users/me", headers={"Authorization": f"Bearer {receipt}"}).status_code
            == 401
        )
        assert (
            client.get(
                "/api/users/me/account-request", headers={"Authorization": f"Bearer {receipt}"}
            ).status_code
            == 401
        )


def test_account_challenges_proofs_and_receipts_are_private_no_store(monkeypatch) -> None:
    from app.core.deps import Actor, require_auth
    from app.models.enums import UserRole

    now = datetime.now(UTC)
    row = SimpleNamespace(
        id=uuid4(),
        kind="DELETE_ACCOUNT",
        status="RECOVERABLE",
        created_at=now,
        updated_at=now,
        restore_deadline=now + timedelta(days=14),
    )
    challenge = SimpleNamespace(id=uuid4())
    service = SimpleNamespace(
        request_reauthentication=AsyncMock(return_value=(challenge, None)),
        confirm_reauthentication=AsyncMock(return_value="p" * 43),
        submit_account_request=AsyncMock(return_value=(row, "r" * 43)),
        get_account_request=AsyncMock(return_value=row),
    )
    app = create_app(make_test_settings())
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole.CUSTOMER, "fake")
    monkeypatch.setattr(account_changes, "_service", lambda request, db: service)
    with TestClient(app) as client:
        responses = [
            client.post("/api/users/me/reauthentication/request", json={"purpose": "PHONE_CHANGE"}),
            client.post(
                "/api/users/me/reauthentication/confirm",
                json={"challenge_id": str(challenge.id), "otp": "123456"},
            ),
            client.post(
                "/api/users/me/account-request",
                json={
                    "kind": "DELETE_ACCOUNT",
                    "confirmation": "DELETE",
                    "reauthentication_token": "p" * 43,
                },
            ),
            client.get(
                "/api/users/me/account-request", headers={"X-Account-Request-Receipt": "r" * 43}
            ),
        ]
    assert [response.status_code for response in responses] == [202, 200, 202, 200]
    assert all(response.headers["Cache-Control"] == "private, no-store" for response in responses)
