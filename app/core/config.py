"""Application settings, loaded once at boot from environment variables only.

There is no secrets manager (SPEC SECTION 6): every secret is an env var wrapped in
``SecretStr`` so a stray repr prints ``**********``. The ``model_validator`` refuses
to boot on any production safety violation, naming the offending variable. This is
the first of the four §5.2 interlock layers.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """The three legal runtime modes. There is no default; missing => refuse boot."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed, validated view of the process environment.

    Held in memory for the process lifetime and never written anywhere. Secret
    fields are ``SecretStr``; business-rule knobs are plain typed values.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )

    # Environment
    ENVIRONMENT: Environment
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Data
    DATABASE_URL: SecretStr
    REDIS_URL: SecretStr

    # Auth / JWT
    JWT_SECRET: SecretStr | None = None
    JWT_PRIVATE_KEY: SecretStr | None = None
    JWT_PUBLIC_KEY: SecretStr | None = None
    JWT_ALGORITHM: Literal["HS256", "RS256"] = "HS256"
    JWT_ACCESS_TTL_MINUTES: int = 30
    JWT_REFRESH_TTL_DAYS: int = 30
    JWT_ISSUER: str = "giftly"
    JWT_AUDIENCE: str = "giftly"

    # Field encryption
    FIELD_ENCRYPTION_KEYS: SecretStr
    FIELD_ENCRYPTION_KEY_VERSION: int
    IDENTITY_FINGERPRINT_PEPPER: SecretStr

    # Storage / CDN (required in production; validated by deploy config, not here)
    AWS_REGION: str | None = None
    AWS_ACCESS_KEY_ID: SecretStr | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    S3_BUCKET_NAME: str | None = None
    CLOUDFRONT_DOMAIN: str | None = None
    CLOUDFRONT_KEY_PAIR_ID: str | None = None
    CLOUDFRONT_PRIVATE_KEY: SecretStr | None = None

    # sndr.sh (required only in production)
    SNDR_API_KEY: SecretStr | None = None
    SNDR_BASE_URL: str | None = None
    SNDR_FROM_EMAIL: str | None = None
    SNDR_FROM_NAME: str | None = None
    SNDR_INVOICE_PAID_TEMPLATE_KEY: str | None = None

    # SMS / Push
    SMS_PROVIDER_KEY: SecretStr | None = None
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_KEY: SecretStr | None = None

    # Admin dashboard
    ADMIN_USERNAME: str | None = None
    ADMIN_PASSWORD: SecretStr | None = None
    ADMIN_SESSION_SECRET: SecretStr | None = None
    ADMIN_TOTP_SECRET: SecretStr | None = None
    ADMIN_SESSION_TTL_MINUTES: int = 60
    ADMIN_DASHBOARD_ENABLED: bool = False

    # CORS
    CORS_ALLOWED_ORIGINS: str = ""

    # Hardening (SPEC SECTION 17.2 A04): global request throttle and body-size guard.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_MAX_REQUESTS: int = Field(default=60, ge=1)
    RATE_LIMIT_WINDOW_SECONDS: int = Field(default=60, ge=1)
    RATE_LIMIT_ANONYMOUS_MAX_REQUESTS: int = Field(default=30, ge=1)
    RATE_LIMIT_ANONYMOUS_WINDOW_SECONDS: int = Field(default=3600, ge=1)
    RATE_LIMIT_ADMIN_MAX_REQUESTS: int = Field(default=100, ge=1)
    RATE_LIMIT_ADMIN_WINDOW_SECONDS: int = Field(default=60, ge=1)
    MAX_REQUEST_BODY_BYTES: int = 1_048_576

    # WebSocket chat guards (audit SEC-4/LOG-3): per-user message throttle + frame cap.
    WS_RATE_LIMIT_MAX_MESSAGES: int = 30
    WS_RATE_LIMIT_WINDOW_SECONDS: int = 60
    WS_MAX_FRAME_BYTES: int = 4096

    # Dedicated OTP HMAC key (audit SEC-3); optional — see OtpService for the fallback.
    OTP_HMAC_KEY: SecretStr | None = None

    # Retention for consumed/expired refresh tokens (audit PERF-3).
    REFRESH_TOKEN_RETENTION_DAYS: int = 30

    # Business rules
    DEFAULT_VAT_RATE: Decimal = Decimal("0.15")
    SERVICE_FEE_RATE: Decimal = Decimal("0.05")
    SERVICE_FEE_MIN_AMOUNT: Decimal = Decimal("5.00")
    SERVICE_FEE_MAX_AMOUNT: Decimal = Decimal("500.00")
    PLATFORM_COMMISSION_RATE: Decimal = Decimal("0.10")
    MAX_INVOICE_ITEMS: int = 20
    MAX_INVOICE_AMOUNT: Decimal = Decimal("50000.00")
    MAX_ITEM_UNIT_PRICE: Decimal = Decimal("50000.00")
    MIN_TOPUP_AMOUNT: Decimal = Decimal("100.00")
    MAX_TOPUP_AMOUNT: Decimal = Decimal("20000.00")
    MIN_WITHDRAWAL_AMOUNT: Decimal = Decimal("50.00")
    MAX_WITHDRAWAL_AMOUNT: Decimal = Decimal("20000.00")
    AUTO_APPROVE_HOURS: int = 72
    PAYMENT_EXPIRY_HOURS: int = 48
    MAX_UPLOAD_BYTES: int = 10_485_760
    CHAT_IMAGE_MAX_UPLOAD_BYTES: int = Field(default=10_485_760, ge=1, le=10_485_760)
    CHAT_VIDEO_MAX_UPLOAD_BYTES: int = Field(default=125_829_120, ge=1, le=125_829_120)
    CHAT_AUDIO_MAX_UPLOAD_BYTES: int = Field(default=10_485_760, ge=1, le=10_485_760)
    CHAT_VIDEO_MAX_DURATION_SECONDS: int = Field(default=120, ge=1, le=120)
    CHAT_AUDIO_MAX_DURATION_SECONDS: int = Field(default=120, ge=1, le=120)
    OTP_TTL_SECONDS: int = 60
    OTP_MAX_PER_WINDOW: int = 3
    OTP_WINDOW_SECONDS: int = 300
    OTP_BLOCK_SECONDS: int = 1800

    # --- Derived helpers -------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.ENVIRONMENT is Environment.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI/docs are served only in development (SPEC SECTION 5.1)."""
        return self.ENVIRONMENT is Environment.DEVELOPMENT

    @property
    def cors_origins(self) -> list[str]:
        """Parsed CORS allow-list; empty in development (all origins allowed)."""
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    def encryption_keys(self) -> dict[int, bytes]:
        """Decode ``FIELD_ENCRYPTION_KEYS`` into a version -> raw-key map."""
        raw = json.loads(self.FIELD_ENCRYPTION_KEYS.get_secret_value())
        return {int(v): base64.b64decode(k) for v, k in raw.items()}

    # --- Boot validation -------------------------------------------------------

    @model_validator(mode="after")
    def _validate_boot(self) -> Settings:
        """Refuse to boot on any configuration or production-safety violation."""
        self._validate_encryption_keys()
        self._validate_jwt()
        self._validate_otp_key()
        self._validate_rates()
        self._validate_admin()
        if self.is_production:
            self._validate_production_interlock()
        return self

    def _validate_encryption_keys(self) -> None:
        try:
            keys = self.encryption_keys()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("FIELD_ENCRYPTION_KEYS is not valid JSON.") from exc
        for version, key in keys.items():
            if len(key) != 32:
                raise ValueError(
                    f"FIELD_ENCRYPTION_KEYS version {version} is not 32 bytes decoded."
                )
        if self.FIELD_ENCRYPTION_KEY_VERSION not in keys:
            raise ValueError("FIELD_ENCRYPTION_KEY_VERSION is not present in the key map.")
        pepper = self.IDENTITY_FINGERPRINT_PEPPER.get_secret_value().encode("utf-8")
        if any(pepper == key for key in keys.values()):
            raise ValueError("IDENTITY_FINGERPRINT_PEPPER must differ from every encryption key.")
        if len(pepper) < 32:
            raise ValueError("IDENTITY_FINGERPRINT_PEPPER must be at least 32 bytes.")

    def _validate_jwt(self) -> None:
        if self.JWT_ALGORITHM == "HS256":
            if self.JWT_SECRET is None:
                raise ValueError("JWT_SECRET is required for HS256.")
            if len(self.JWT_SECRET.get_secret_value().encode("utf-8")) < 32:
                raise ValueError("JWT_SECRET must be at least 32 bytes for HS256.")
        else:  # RS256
            if self.JWT_PRIVATE_KEY is None or self.JWT_PUBLIC_KEY is None:
                raise ValueError("JWT_PRIVATE_KEY and JWT_PUBLIC_KEY are required for RS256.")

    def _validate_otp_key(self) -> None:
        if self.OTP_HMAC_KEY is None:
            return
        key = self.OTP_HMAC_KEY.get_secret_value()
        if len(key) < 32 or not key.isascii():
            raise ValueError("OTP_HMAC_KEY must be at least 32 ASCII characters.")

    def _validate_rates(self) -> None:
        for name in ("SERVICE_FEE_RATE", "DEFAULT_VAT_RATE", "PLATFORM_COMMISSION_RATE"):
            value: Decimal = getattr(self, name)
            if not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be within [0, 1].")
        if self.MIN_WITHDRAWAL_AMOUNT < Decimal("50.00"):
            raise ValueError("MIN_WITHDRAWAL_AMOUNT must be at least 50.00.")
        if self.MAX_WITHDRAWAL_AMOUNT < self.MIN_WITHDRAWAL_AMOUNT:
            raise ValueError("MAX_WITHDRAWAL_AMOUNT must be at least MIN_WITHDRAWAL_AMOUNT.")

    def _validate_admin(self) -> None:
        if self.ADMIN_DASHBOARD_ENABLED:
            if not self.ADMIN_USERNAME or self.ADMIN_PASSWORD is None:
                raise ValueError(
                    "ADMIN_USERNAME and ADMIN_PASSWORD are required when the dashboard is on."
                )
            password = self.ADMIN_PASSWORD.get_secret_value()
            if not password:
                raise ValueError("ADMIN_PASSWORD must not be empty.")
            if self.is_production and (
                self.ADMIN_USERNAME.casefold() == "admin" or password.casefold() == "admin"
            ):
                raise ValueError("Production admin credentials must not use development defaults.")
            if self.ADMIN_SESSION_SECRET is None:
                raise ValueError("ADMIN_SESSION_SECRET is required when the dashboard is on.")
            if len(self.ADMIN_SESSION_SECRET.get_secret_value().encode("utf-8")) < 32:
                raise ValueError("ADMIN_SESSION_SECRET must be at least 32 bytes.")
            if self.is_production:
                raw = (
                    self.ADMIN_TOTP_SECRET.get_secret_value()
                    if self.ADMIN_TOTP_SECRET is not None
                    else ""
                )
                try:
                    decoded = base64.b32decode(raw, casefold=False)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("ADMIN_TOTP_SECRET must be valid Base32.") from exc
                if len(decoded) < 20:
                    raise ValueError("ADMIN_TOTP_SECRET must decode to at least 20 bytes.")

    def _validate_production_interlock(self) -> None:
        if self.DEBUG:
            raise ValueError("DEBUG must be False in production.")
        if self.docs_enabled:
            raise ValueError("OpenAPI docs must be disabled in production.")
        if not self.cors_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must be set in production.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must not contain a wildcard in production.")
        self._require_production_fields(
            "AWS_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "S3_BUCKET_NAME",
            "CLOUDFRONT_DOMAIN",
            "CLOUDFRONT_KEY_PAIR_ID",
            "CLOUDFRONT_PRIVATE_KEY",
            "SMS_PROVIDER_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "SNDR_API_KEY",
            "SNDR_BASE_URL",
            "SNDR_FROM_EMAIL",
            "SNDR_FROM_NAME",
        )
        for name in ("SUPABASE_URL", "SNDR_BASE_URL"):
            parsed = urlsplit(getattr(self, name))
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"{name} must be an HTTPS base URL without credentials.")

    def _require_production_fields(self, *names: str) -> None:
        """Reject an empty production integration setting."""
        for name in names:
            if getattr(self, name) in (None, ""):
                raise ValueError(f"{name} is required in production.")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton, constructed once at first use."""
    try:
        return Settings()
    except ValidationError as exc:
        reasons = [
            str(error["msg"]).removeprefix("Value error, ")
            for error in exc.errors(include_input=False, include_url=False)
        ]
        message = "Invalid application settings: " + "; ".join(reasons)
        logger.error("Application settings failed validation: %s", message)
        raise RuntimeError(message) from None
