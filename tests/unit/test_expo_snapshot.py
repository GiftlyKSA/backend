"""Expo snapshot queries preserve recipient eligibility at the final send boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.repositories.expo_receipt_repository import ExpoReceiptRepository
from sqlalchemy.dialects import postgresql


async def test_order_snapshot_rechecks_original_owner_city_and_order_in_one_query() -> None:
    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    order_id = uuid4()
    await ExpoReceiptRepository(session).snapshot_devices(
        ["ExpoPushToken[test]"], order_id=order_id
    )
    assert session.execute.await_count == 1
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "order_notification_recipients.user_id = device_tokens.user_id" in sql
    assert "order_notification_recipients.token_id = device_tokens.id" in sql
    assert "courier_profiles.city_of_residence_id = orders.delivery_city_id" in sql
    assert "users.deleted_at IS NULL" in sql
    assert "courier_profiles.is_verified IS true" in sql
    assert order_id in compiled.params.values()
    assert "NEW" in compiled.params.values()
    assert "ACTIVE" in compiled.params.values()
