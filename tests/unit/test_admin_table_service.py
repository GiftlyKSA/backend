from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.crypto import build_aad, build_cipher
from app.core.exceptions import ConflictError, ForbiddenError, ValidationDomainError
from app.repositories.admin_table_repository import AdminTableRepository
from app.repositories.audit_repository import AuditRepository
from app.services.admin_table_service import AdminTableService

from tests.conftest import make_test_settings


@asynccontextmanager
async def transaction():
    yield


def setup_service(role="ADMIN", status="ACTIVE", deleted_at=None):
    repo = Mock(spec=AdminTableRepository)
    repo.transaction = transaction
    repo.get_record = AsyncMock(
        return_value={"role": role, "status": status, "deleted_at": deleted_at}
    )
    audit = Mock(spec=AuditRepository)
    settings = make_test_settings()
    return AdminTableService(repo, audit, settings, AsyncMock()), repo, audit, settings


@pytest.mark.parametrize(
    "role,status,deleted",
    [
        ("CUSTOMER", "ACTIVE", None),
        ("COURIER", "ACTIVE", None),
        ("ADMIN", "BANNED", None),
        ("ADMIN", "ACTIVE", datetime.now(UTC)),
    ],
)
async def test_only_active_non_deleted_admin_can_write(role, status, deleted):
    service, repo, audit, _ = setup_service(role, status, deleted)
    with pytest.raises(ForbiddenError):
        await service.save("users", {}, admin_id=uuid4(), session_id=uuid4())
    repo.save.assert_not_awaited()
    audit.record.assert_not_awaited()


async def test_write_is_audited_without_values_and_scope_is_cleared():
    service, repo, audit, _ = setup_service()
    identifier = await service.save(
        "users",
        {"phone": "test-private-phone", "role": "CUSTOMER"},
        admin_id=uuid4(),
        session_id=uuid4(),
    )
    assert identifier is not None
    assert audit.record.call_args.kwargs["metadata"] == {"fields": ["phone", "role"]}
    assert "test-private-phone" not in str(audit.record.call_args)
    repo.clear_maintenance.assert_awaited_once()


async def test_stale_edit_is_rejected_before_write():
    service, repo, audit, _ = setup_service()
    repo.get_record.side_effect = [
        {"role": "ADMIN", "status": "ACTIVE", "deleted_at": None},
        {"updated_at": "new-revision"},
    ]
    with pytest.raises(ConflictError):
        await service.save(
            "users",
            {"phone": "123"},
            admin_id=uuid4(),
            session_id=uuid4(),
            record_id=uuid4(),
            revision="old-revision",
        )
    repo.save.assert_not_awaited()
    audit.record.assert_not_awaited()


async def test_message_content_is_encrypted_using_conversation_identity():
    service, repo, _, settings = setup_service()
    conversation, sender = uuid4(), uuid4()
    await service.save(
        "messages",
        {
            "conversation_id": str(conversation),
            "sender_id": str(sender),
            "content_encrypted": "secret text",
        },
        admin_id=uuid4(),
        session_id=uuid4(),
    )
    stored = repo.save.call_args.args[2]["content_encrypted"]
    cipher = build_cipher(settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION)
    assert (
        cipher.decrypt(stored, build_aad("messages", "content", str(conversation))) == "secret text"
    )


async def test_audit_failure_prevents_success_and_propagates():
    service, _, audit, _ = setup_service()
    audit.record.side_effect = RuntimeError("audit unavailable")
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.save(
            "users", {"phone": "123", "role": "CUSTOMER"}, admin_id=uuid4(), session_id=uuid4()
        )


@pytest.mark.parametrize(
    "submitted,invalidates",
    [
        ({"phone": "new-phone"}, True),
        ({"role": "COURIER"}, True),
        ({"status": "BANNED"}, True),
        ({"deleted_at": "2026-09-23T00:00:00+00:00"}, True),
        ({"phone": "old-phone", "role": "CUSTOMER", "status": "ACTIVE"}, False),
        ({"full_name": "Updated name"}, False),
    ],
)
async def test_user_security_changes_revoke_credentials_but_profile_edits_do_not(
    submitted, invalidates
):
    service, repo, _, _ = setup_service()
    old = {
        "phone": "old-phone",
        "role": "CUSTOMER",
        "status": "ACTIVE",
        "deleted_at": None,
        "auth_version": 9,
    }
    repo.get_record.side_effect = [
        {"role": "ADMIN", "status": "ACTIVE", "deleted_at": None},
        old,
    ]
    record_id = uuid4()
    await service.save(
        "users",
        submitted,
        admin_id=uuid4(),
        session_id=uuid4(),
        record_id=record_id,
        revision=service._revision(old),
    )
    assert service._auth.invalidate_user_credentials.await_count == int(invalidates)
    assert "auth_version" not in repo.save.call_args.args[2]
    if invalidates:
        assert service._auth.invalidate_user_credentials.call_args.args[0] == record_id


async def test_admin_cannot_reset_the_server_owned_credential_version():
    service, repo, _, _ = setup_service()
    with pytest.raises(ValidationDomainError, match="read-only"):
        await service.save(
            "users",
            {"auth_version": "0"},
            admin_id=uuid4(),
            session_id=uuid4(),
            record_id=uuid4(),
        )
    repo.save.assert_not_awaited()
