"""Scoped OTP challenges cannot consume ordinary login codes."""

from unittest.mock import AsyncMock

import pytest
from app.services.otp_service import OtpService

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_scoped_otp_isolated_and_shares_phone_send_limits() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 1
    service = OtpService(redis, AsyncMock(), make_test_settings())
    await service.request_otp("+966501234567", scope="account-change:challenge", ttl_seconds=60)
    issued = redis.eval.await_args.args
    assert issued[2] == "otp:code:account-change:challenge:+966501234567"
    assert issued[4] == "otp:rate:+966501234567"
    assert issued[5] == "otp:block:+966501234567"
    assert issued[7] == 60
    await service.verify_otp("+966501234567", "123456", scope="account-change:challenge")
    verified = redis.eval.await_args.args
    assert verified[2] == issued[2]
    assert verified[4] != issued[4]


@pytest.mark.asyncio
async def test_scoped_otp_resend_has_atomic_sixty_second_cooldown() -> None:
    from app.core.exceptions import RateLimitedError

    redis = AsyncMock()
    redis.set.return_value = False
    service = OtpService(redis, AsyncMock(), make_test_settings())
    with pytest.raises(RateLimitedError) as error:
        await service.request_otp("+966501234567", scope="account-change:challenge")
    assert error.value.retry_after_seconds == 60
    redis.set.assert_awaited_once_with("otp:cooldown:+966501234567", "1", ex=60, nx=True)
    redis.eval.assert_not_awaited()
