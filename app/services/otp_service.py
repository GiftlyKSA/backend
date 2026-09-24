"""OTP request and verification (SPEC SECTION 17.2 A07, 20.A).

Codes are generated with a CSPRNG and stored in Redis only as an HMAC — a Redis dump
must not be a free login. Requests are rate limited per phone with a block window;
verification is single-use and capped. The admin dashboard reuses this service.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import RateLimitedError
from app.core.security import generate_otp, hmac_hex
from app.integrations.sms.base import SmsClient

_ISSUE_LUA = """
if redis.call('EXISTS', KEYS[4]) == 1 then return 0 end
local count = redis.call('INCR', KEYS[3])
if count == 1 or redis.call('TTL', KEYS[3]) < 0 then
    redis.call('EXPIRE', KEYS[3], ARGV[3])
end
if count > tonumber(ARGV[4]) then
    redis.call('SET', KEYS[4], '1', 'EX', ARGV[5])
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('DEL', KEYS[2])
return 1
"""

_VERIFY_LUA = """
local stored = redis.call('GET', KEYS[1])
if not stored then return 0 end
local attempts = redis.call('INCR', KEYS[2])
if attempts == 1 or redis.call('TTL', KEYS[2]) < 0 then
    redis.call('EXPIRE', KEYS[2], ARGV[2])
end
if attempts > 5 then
    redis.call('DEL', KEYS[1])
    return -1
end
local difference = bit.bxor(string.len(stored), string.len(ARGV[1]))
for i = 1, string.len(stored) do
    difference = bit.bor(difference, bit.bxor(string.byte(stored, i),
        string.byte(ARGV[1], i) or 0))
end
if difference ~= 0 then return 0 end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
return 1
"""


class OtpService:
    """Issues and verifies phone OTPs, throttled through Redis."""

    def __init__(self, redis: Redis, sms: SmsClient, settings: Settings) -> None:
        """Hold the Redis client, the SMS sender, and the OTP tuning from settings."""
        self._redis = redis
        self._sms = sms
        self._settings = settings
        # The OTP HMAC key (audit SEC-3): a dedicated OTP_HMAC_KEY wins; otherwise fall
        # back to the JWT secret, then the identity pepper — every mode (including
        # RS256, where JWT_SECRET is absent) ends on a boot-validated >=32-byte secret,
        # never a constant. The code is ephemeral (180s TTL); this protects a Redis dump.
        if settings.OTP_HMAC_KEY is not None:
            self._hmac_key = settings.OTP_HMAC_KEY.get_secret_value()
        elif settings.JWT_SECRET is not None:
            self._hmac_key = settings.JWT_SECRET.get_secret_value()
        else:
            self._hmac_key = settings.IDENTITY_FINGERPRINT_PEPPER.get_secret_value()

    def _code_key(self, phone: str) -> str:
        return f"otp:code:{phone}"

    def _attempts_key(self, phone: str) -> str:
        return f"otp:attempts:{phone}"

    def _rate_key(self, phone: str) -> str:
        return f"otp:rate:{phone}"

    def _block_key(self, phone: str) -> str:
        return f"otp:block:{phone}"

    async def request_otp(self, phone: str) -> str | None:
        """Generate, store, and send an OTP for ``phone``.

        Returns:
            The code in development (so a developer can complete login without SMS),
            otherwise None.

        Raises:
            RateLimitedError: The phone is blocked or over its request window.
        """
        code = generate_otp()
        issued = await self._redis.eval(
            _ISSUE_LUA,
            4,
            self._code_key(phone),
            self._attempts_key(phone),
            self._rate_key(phone),
            self._block_key(phone),
            hmac_hex(code, self._hmac_key),
            self._settings.OTP_TTL_SECONDS,
            self._settings.OTP_WINDOW_SECONDS,
            self._settings.OTP_MAX_PER_WINDOW,
            self._settings.OTP_BLOCK_SECONDS,
        )
        if not issued:
            raise RateLimitedError(self._settings.OTP_BLOCK_SECONDS)
        await self._sms.send_otp(phone, code)

        return code if self._settings.ENVIRONMENT.value == "development" else None

    async def verify_otp(self, phone: str, code: str) -> bool:
        """Verify a submitted OTP, consuming it on success.

        Returns:
            True on a correct, unexpired, unexhausted code; False otherwise.

        Raises:
            RateLimitedError: Too many verification attempts for this code.
        """
        result = await self._redis.eval(
            _VERIFY_LUA,
            3,
            self._code_key(phone),
            self._attempts_key(phone),
            self._rate_key(phone),
            hmac_hex(code, self._hmac_key),
            self._settings.OTP_TTL_SECONDS,
        )
        if result == -1:
            raise RateLimitedError(self._settings.OTP_TTL_SECONDS)
        return bool(result == 1)
