"""Application settings, loaded once at boot from environment variables only.

There is no secrets manager: every secret is an env var wrapped in
``SecretStr`` so a stray repr prints ``**********``. The ``model_validator`` refuses
to boot on any production safety violation, naming the offending variable. This is
the first of the production interlock layers.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import re
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal, TypeVar
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
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    DB_POOL_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)

    INTEGRATION_HTTP_TIMEOUT_SECONDS: float = Field(default=10, gt=0, le=15, allow_inf_nan=False)

    PAYMENT_PROVIDER: Literal["auto", "disabled", "simulated", "dhamen"] = "auto"
    PAYMENT_CHECKOUTS_ENABLED: bool = True
    DHAMEN_ENVIRONMENT: Literal["testing", "production"] = "testing"
    DHAMEN_TEST_BASE_URL: str | None = None
    DHAMEN_PRODUCTION_BASE_URL: str | None = None
    DHAMEN_APP_ID: SecretStr | None = None
    DHAMEN_APP_KEY: SecretStr | None = None
    DHAMEN_CLIENT_ID: SecretStr | None = None
    DHAMEN_CHECKOUT_HOSTS: str = ""
    DHAMEN_RETURN_URL: str | None = None
    DHAMEN_WEBHOOK_URL: str | None = None

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

    # Private S3-compatible storage
    STORAGE_PROVIDER: Literal["auto", "s3", "fake"] = "auto"
    S3_ENDPOINT: str | None = None
    S3_BUCKET: str | None = None
    S3_REGION: str = "auto"
    S3_ACCESS_KEY_ID: SecretStr | None = None
    S3_SECRET_ACCESS_KEY: SecretStr | None = None

    # sndr.sh (required only in production)
    SNDR_API_KEY: SecretStr | None = None
    SNDR_BASE_URL: str | None = None
    SNDR_FROM_EMAIL: str | None = None
    SNDR_FROM_NAME: str | None = None
    SNDR_INVOICE_PAID_TEMPLATE_KEY: str | None = None

    # SMS / Push
    PUSH_PROVIDER: Literal["auto", "fake", "expo", "supabase"] = "auto"
    EXPO_ACCESS_TOKEN: SecretStr | None = None
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

    # Hardening: global request throttle and body-size guard.
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
    WS_MAX_OUTGOING_FRAME_BYTES: int = Field(default=32768, ge=1, le=131072)

    # Dedicated OTP HMAC key (audit SEC-3); optional — see OtpService for the fallback.
    OTP_HMAC_KEY: SecretStr | None = None

    # Retention for consumed/expired refresh tokens (audit PERF-3).
    REFRESH_TOKEN_RETENTION_DAYS: int = 30

    # Business rules
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
        """OpenAPI/docs are served only in development."""
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
        self._validate_payments()
        if self.is_production:
            self._validate_production_interlock()
        else:
            self._validate_storage()
            self._validate_push()
        return self

    @property
    def storage_provider(self) -> str:
        """Resolve storage independently from other integrations."""
        if self.STORAGE_PROVIDER == "auto":
            return "s3" if self.is_production else "fake"
        return self.STORAGE_PROVIDER

    @property
    def push_provider(self) -> str:
        """Default production push to Expo and development to fake delivery."""
        if self.PUSH_PROVIDER == "auto":
            return "expo" if self.is_production else "fake"
        return self.PUSH_PROVIDER

    def _validate_storage(self) -> None:
        if self.storage_provider == "fake":
            if self.is_production:
                raise ValueError("Production must not select fake storage.")
            return
        for name in (
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_REGION",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ):
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value or not value.strip():
                raise ValueError(f"{name} is required for S3 storage.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.S3_BUCKET or ""):
            raise ValueError("S3_BUCKET must be a valid bucket name.")

        self._validate_storage_endpoint()

    def _validate_storage_endpoint(self) -> None:
        endpoint = self.S3_ENDPOINT or ""
        try:
            parsed = urlsplit(endpoint)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("S3_ENDPOINT must be a public HTTPS origin.") from exc
        if (
            endpoint != endpoint.strip()
            or any(character.isspace() for character in endpoint)
            or "?" in endpoint
            or "#" in endpoint
            or parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise ValueError("S3_ENDPOINT must be a public HTTPS origin.")
        self._validate_storage_host(host)

    @staticmethod
    def _validate_storage_host(host: str) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                "." not in host
                or host.endswith(
                    (".localhost", ".localdomain", ".local", ".internal", ".lan", ".home", ".arpa")
                )
                or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")
                )
                or all(label.isdigit() for label in host.split("."))
            ):
                raise ValueError("S3_ENDPOINT must have a public hostname.") from None
        else:
            if not address.is_global:
                raise ValueError("S3_ENDPOINT must have a public address.")

    def _validate_push(self) -> None:
        if self.push_provider == "fake" and self.is_production:
            raise ValueError("Production must not select fake push.")
        if self.push_provider == "supabase":
            self._require_production_fields("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
            parsed = urlsplit(self.SUPABASE_URL or "")
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("SUPABASE_URL must be an HTTPS base URL without credentials.")

    @property
    def payment_provider(self) -> str:
        """Resolve the backward-compatible default without selecting a production fake."""
        if self.PAYMENT_PROVIDER == "auto":
            return "disabled" if self.is_production else "simulated"
        return self.PAYMENT_PROVIDER

    @property
    def dhamen_base_url(self) -> str:
        """Default testing to the demo origin; require an explicit production origin."""
        if self.DHAMEN_ENVIRONMENT == "production":
            return self.DHAMEN_PRODUCTION_BASE_URL or ""
        return self.DHAMEN_TEST_BASE_URL or "https://dhamendemo.elm.sa"

    @property
    def dhamen_checkout_hosts(self) -> tuple[str, ...]:
        """Return exact allowed checkout hostnames, never suffix or wildcard matches."""
        return tuple(
            host.strip().lower() for host in self.DHAMEN_CHECKOUT_HOSTS.split(",") if host.strip()
        )

    @property
    def dhamen_should_fall_back_to_fake(self) -> bool:
        """Use simulated payments in development when Dhamen settings are incomplete."""
        credentials = (self.DHAMEN_APP_ID, self.DHAMEN_APP_KEY, self.DHAMEN_CLIENT_ID)
        configured = (
            all(value is not None and value.get_secret_value().strip() for value in credentials)
            and bool(self.DHAMEN_RETURN_URL and self.DHAMEN_RETURN_URL.strip())
            and bool(self.DHAMEN_WEBHOOK_URL and self.DHAMEN_WEBHOOK_URL.strip())
            and bool(self.dhamen_checkout_hosts)
        )
        return (
            self.ENVIRONMENT is Environment.DEVELOPMENT
            and self.payment_provider == "dhamen"
            and not configured
        )

    def _validate_payments(self) -> None:
        if self.payment_provider == "simulated" and self.is_production:
            raise ValueError("Production must not select simulated payments.")
        if self.payment_provider == "dhamen":
            self._validate_dhamen()

    def _validate_dhamen(self) -> None:
        if self.is_production:
            raise ValueError("Production Dhamen payments remain disabled pending verification.")
        if self.dhamen_should_fall_back_to_fake:
            return
        if self.is_production != (self.DHAMEN_ENVIRONMENT == "production"):
            raise ValueError(
                "Dhamen testing/production mode must match the application environment."
            )
        for name in ("DHAMEN_APP_ID", "DHAMEN_APP_KEY", "DHAMEN_CLIENT_ID"):
            value: SecretStr | None = getattr(self, name)
            if value is None or not value.get_secret_value().strip():
                raise ValueError(f"{name} is required for Dhamen.")
        for name, url_value in (
            ("Dhamen base URL", self.dhamen_base_url),
            ("DHAMEN_RETURN_URL", self.DHAMEN_RETURN_URL),
            ("DHAMEN_WEBHOOK_URL", self.DHAMEN_WEBHOOK_URL),
        ):
            parsed = urlsplit(url_value or "")
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
            ):
                raise ValueError(f"{name} must be an HTTPS URL without credentials or query.")
        if not self.dhamen_checkout_hosts or any(
            urlsplit("https://" + host).netloc != host
            or "/" in host
            or ":" in host
            or "*" in host
            or "@" in host
            for host in self.dhamen_checkout_hosts
        ):
            raise ValueError("DHAMEN_CHECKOUT_HOSTS must contain exact hostnames.")

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
            if self.JWT_SECRET is None:
                raise ValueError("OTP_HMAC_KEY is required when JWT_SECRET is absent.")
            return
        key = self.OTP_HMAC_KEY.get_secret_value()
        if len(key) < 32 or not key.isascii():
            raise ValueError("OTP_HMAC_KEY must be at least 32 ASCII characters.")

    def _validate_rates(self) -> None:
        for name in ("SERVICE_FEE_RATE", "PLATFORM_COMMISSION_RATE"):
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
        self._validate_storage()
        self._validate_push()
        self._require_production_fields(
            "SMS_PROVIDER_KEY",
            "SUPABASE_URL",
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
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"{name} is required in production.")


class WorkerSettings(Settings):
    """Validate worker dependencies while retaining typed business settings."""

    @model_validator(mode="after")
    def _validate_boot(self) -> WorkerSettings:
        self._validate_encryption_keys()
        self._validate_rates()
        self._validate_payments()
        self._validate_storage()
        self._validate_push()
        if self.is_production:
            if self.DEBUG:
                raise ValueError("DEBUG must be False in production.")
            self._require_production_fields(
                "SNDR_API_KEY",
                "SNDR_BASE_URL",
                "SNDR_FROM_EMAIL",
                "SNDR_FROM_NAME",
            )
            for name in ("SNDR_BASE_URL",):
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
        return self


class MigrationSettings(BaseSettings):
    """Minimal settings for the migration gate, independent of integrations."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )
    ENVIRONMENT: Environment
    DATABASE_URL: SecretStr
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    @property
    def is_production(self) -> bool:
        """Return the actual environment without changing integration selection."""
        return self.ENVIRONMENT is Environment.PRODUCTION

    @model_validator(mode="after")
    def _validate_boot(self) -> MigrationSettings:
        if self.is_production and self.DEBUG:
            raise ValueError("DEBUG must be False in production.")
        if not self.DATABASE_URL.get_secret_value().strip():
            raise ValueError("DATABASE_URL must not be empty.")
        return self


class SchedulerSettings(BaseSettings):
    """Queue-only settings; scheduler imports task declarations without executing them."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )
    ENVIRONMENT: Environment
    REDIS_URL: SecretStr
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    @property
    def is_production(self) -> bool:
        """Return the configured environment."""
        return self.ENVIRONMENT is Environment.PRODUCTION

    @model_validator(mode="after")
    def _validate_boot(self) -> SchedulerSettings:
        if self.is_production and self.DEBUG:
            raise ValueError("DEBUG must be False in production.")
        if not self.REDIS_URL.get_secret_value().strip():
            raise ValueError("REDIS_URL must not be empty.")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """Load validated worker configuration."""
    return _load_settings(WorkerSettings)


@lru_cache
def get_scheduler_settings() -> SchedulerSettings:
    """Load only scheduler configuration."""
    return _load_settings(SchedulerSettings)


@lru_cache
def get_migration_settings() -> MigrationSettings:
    """Load only migration configuration."""
    return _load_settings(MigrationSettings)


_SettingsT = TypeVar("_SettingsT", bound=BaseSettings)


def _load_settings(settings_class: type[_SettingsT]) -> _SettingsT:
    try:
        return settings_class()
    except ValidationError as exc:
        reasons = [
            ".".join(str(part) for part in error["loc"])
            + ": "
            + str(error["msg"]).removeprefix("Value error, ")
            for error in exc.errors(include_input=False, include_url=False)
        ]
        message = "Invalid application settings: " + "; ".join(reasons)
        logger.error("Application settings failed validation: %s", message)
        raise RuntimeError(message) from None


@lru_cache
def get_settings() -> Settings:
    """Load the API configuration; process-selection environment cannot weaken it."""
    return _load_settings(Settings)
