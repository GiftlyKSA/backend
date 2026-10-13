"""The single place integration clients are constructed.

Layer 2 of the production interlock: ``build_clients`` is the ONLY place a client
is built, and it raises at boot if ENVIRONMENT=production would select any fake.
The Fakes additionally guard their own ``__init__`` (layer 3), so a production fake
is impossible at two independent layers.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.email.base import EmailClient
from app.integrations.email.fake import FakeEmailClient
from app.integrations.email.sndr_client import SndrEmailClient
from app.integrations.payments.base import PaymentClient
from app.integrations.payments.dhamen import DhamenPaymentClient
from app.integrations.payments.disabled import DisabledPaymentClient
from app.integrations.payments.fake import FakePaymentClient
from app.integrations.push.base import PushClient
from app.integrations.push.fake import FakePushClient
from app.integrations.push.real import RealPushClient
from app.integrations.sms.base import SmsClient
from app.integrations.sms.fake import FakeSmsClient
from app.integrations.sms.real import RealSmsClient
from app.integrations.storage.base import StorageClient
from app.integrations.storage.fake import FakeStorageClient
from app.integrations.storage.real import S3StorageClient


@dataclass(frozen=True)
class Clients:
    """The bundle of integration clients wired for the active environment."""

    gateway: PaymentClient
    email: EmailClient
    sms: SmsClient
    push: PushClient
    storage: StorageClient


def build_clients(settings: Settings) -> Clients:
    """Construct the integration clients for the active environment.

    In production, payments are disabled; other integrations use real clients.
    In development/test, defaults to fakes; explicit storage/push overrides are independent.
    Selecting a Real client in production requires its config, which boot validation
    has already guaranteed present.

    Raises:
        RuntimeError: A fake would be selected in production (defence in depth).
    """
    if settings.is_production:
        return _build_production_clients(settings)
    return _build_fake_clients(settings)


def _build_production_clients(settings: Settings) -> Clients:
    gateway = build_payment_client(settings)
    email = build_email_client(settings)
    sms = RealSmsClient(
        provider_key=(
            settings.SMS_PROVIDER_KEY.get_secret_value() if settings.SMS_PROVIDER_KEY else ""
        ),
        base_url=settings.SUPABASE_URL or "",
        timeout_seconds=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS,
    )
    push = build_push_client(settings)
    storage = build_storage_client(settings)
    return Clients(gateway=gateway, email=email, sms=sms, push=push, storage=storage)


def _required_secret(value: SecretStr | None, name: str) -> str:
    """Return a production secret or fail closed if boot validation regresses."""
    if value is None or not value.get_secret_value().strip():
        raise RuntimeError(f"{name} is required in production.")
    return value.get_secret_value()


def build_payment_client(settings: Settings) -> PaymentClient:
    """Select the provider independently of other integrations; defaults remain safe."""
    if settings.payment_provider == "disabled":
        return DisabledPaymentClient()
    if settings.payment_provider == "dhamen":
        if settings.dhamen_should_fall_back_to_fake:
            return FakePaymentClient(settings.ENVIRONMENT)
        return DhamenPaymentClient(
            base_url=settings.dhamen_base_url,
            app_id=_required_secret(settings.DHAMEN_APP_ID, "DHAMEN_APP_ID"),
            app_key=_required_secret(settings.DHAMEN_APP_KEY, "DHAMEN_APP_KEY"),
            client_id=_required_secret(settings.DHAMEN_CLIENT_ID, "DHAMEN_CLIENT_ID"),
            checkout_hosts=settings.dhamen_checkout_hosts,
            return_url=settings.DHAMEN_RETURN_URL or "",
            timeout_seconds=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS,
        )
    return FakePaymentClient(settings.ENVIRONMENT)


def _build_fake_clients(settings: Settings) -> Clients:
    environment = settings.ENVIRONMENT
    return Clients(
        gateway=build_payment_client(settings),
        email=FakeEmailClient(environment),
        sms=FakeSmsClient(environment),
        push=build_push_client(settings),
        storage=build_storage_client(settings),
    )


def build_email_client(settings: Settings) -> EmailClient:
    """Construct only the email integration required by the caller."""
    if not settings.is_production:
        return FakeEmailClient(settings.ENVIRONMENT)
    return SndrEmailClient(
        base_url=settings.SNDR_BASE_URL or "",
        api_key=_required_secret(settings.SNDR_API_KEY, "SNDR_API_KEY"),
        from_email=settings.SNDR_FROM_EMAIL or "",
        from_name=settings.SNDR_FROM_NAME or "",
        timeout_seconds=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS,
    )


def build_push_client(settings: Settings) -> PushClient:
    """Construct only the independently selected push integration."""
    if settings.push_provider == "fake":
        return FakePushClient(settings.ENVIRONMENT)
    if settings.push_provider == "expo":
        from app.core.db import build_engine, build_session_factory
        from app.integrations.push.expo import ExpoPushClient
        from app.services.expo_receipt_service import ExpoReceiptService

        receipt_settings = settings.model_copy(update={"DB_POOL_SIZE": 1, "DB_MAX_OVERFLOW": 0})
        engine = build_engine(receipt_settings)
        return ExpoPushClient(
            receipt_store=ExpoReceiptService(build_session_factory(engine), owned_engine=engine),
            access_token=(
                settings.EXPO_ACCESS_TOKEN.get_secret_value()
                if settings.EXPO_ACCESS_TOKEN
                else None
            ),
            timeout_seconds=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS,
        )
    return RealPushClient(
        timeout_seconds=settings.INTEGRATION_HTTP_TIMEOUT_SECONDS,
        supabase_url=settings.SUPABASE_URL or "",
        service_key=_required_secret(settings.SUPABASE_SERVICE_KEY, "SUPABASE_SERVICE_KEY"),
    )


def build_storage_client(settings: Settings) -> StorageClient:
    """Construct only the storage integration required by the caller."""
    if settings.storage_provider == "fake":
        return FakeStorageClient(settings.ENVIRONMENT)
    if not settings.S3_ENDPOINT or not settings.S3_BUCKET or not settings.S3_REGION:
        raise RuntimeError("S3 endpoint, bucket, and region are required for real storage.")
    return S3StorageClient(
        endpoint=settings.S3_ENDPOINT,
        bucket=settings.S3_BUCKET,
        region=settings.S3_REGION,
        access_key_id=_required_secret(settings.S3_ACCESS_KEY_ID, "S3_ACCESS_KEY_ID"),
        secret_access_key=_required_secret(settings.S3_SECRET_ACCESS_KEY, "S3_SECRET_ACCESS_KEY"),
    )
