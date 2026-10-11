"""PostgreSQL courier avatar ownership, cleanup and migration protections."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import cast
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import Environment
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.integrations.storage.fake import FakeStorageClient
from app.models import City, CourierProfile, MediaUpload, User
from app.models.enums import UserRole, UserStatus
from app.repositories.courier_avatar_repository import CourierAvatarRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.user_repository import UserRepository
from app.services.courier_avatar_service import CourierAvatarService
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_cleanup_service import clean_abandoned_uploads
from app.services.media_service import MediaService
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import make_test_settings


async def _courier(session: AsyncSession, *, verified: bool = True) -> User:
    city = await session.scalar(select(City).limit(1))
    assert city is not None
    user = User(
        phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.COURIER, status=UserStatus.ACTIVE
    )
    session.add(user)
    await session.flush()
    session.add(
        CourierProfile(
            user_id=user.id,
            city_of_residence_id=city.id,
            national_id_encrypted="test",
            is_verified=verified,
        )
    )
    await session.flush()
    return user


def _avatars(session: AsyncSession) -> CourierAvatarService:
    return CourierAvatarService(
        CourierAvatarRepository(session),
        CourierEligibilityService(
            users=UserRepository(session), couriers=CourierRepository(session)
        ),
    )


def _media(session: AsyncSession, storage: FakeStorageClient) -> MediaService:
    return MediaService(storage, make_test_settings(), MediaRepository(session))


async def _upload(
    media: MediaService,
    owner: User,
    *,
    purpose: str = "COURIER_AVATAR",
    content_type: str = "image/jpeg",
    confirmed: bool = True,
) -> str:
    body_file = BytesIO()
    Image.new("RGB", (16, 16), "purple").save(
        body_file, format="PNG" if content_type == "image/png" else "JPEG"
    )
    body = body_file.getvalue()
    _, key, _ = await media.request_upload_url(
        actor_id=owner.id, purpose=purpose, content_type=content_type, byte_size=len(body)
    )
    storage = cast(FakeStorageClient, media._storage)
    storage.recorded_bytes[key] = body
    if confirmed:
        await media.confirm(key, actor_id=owner.id)
    return key


async def test_courier_avatar_replace_remove_and_cleanup(db_session: AsyncSession) -> None:
    owner = await _courier(db_session)
    other = await _courier(db_session)
    storage = FakeStorageClient(Environment.TEST)
    media = _media(db_session, storage)
    avatars, repository = _avatars(db_session), CourierAvatarRepository(db_session)
    first = await _upload(media, owner)
    second = await _upload(media, owner, content_type="image/png")
    await avatars.set_avatar(owner.id, first, media)
    assert await repository.get_key(owner.id) == first
    assert await repository.get_key(other.id) is None
    assert (await MediaRepository(db_session).get(first)).attached_at is not None
    await avatars.set_avatar(owner.id, second, media)
    assert await repository.get_key(owner.id) == second
    assert (await MediaRepository(db_session).get(first)).attached_at is None
    assert (await MediaRepository(db_session).get(second)).attached_at is not None
    await db_session.execute(
        update(MediaUpload)
        .where(MediaUpload.storage_key.in_([first, second]))
        .values(created_at=datetime.now(UTC) - timedelta(days=2))
    )
    connection = await db_session.connection()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    await clean_abandoned_uploads(factory=factory, storage=storage, settings=make_test_settings())
    assert await MediaRepository(db_session).get(first) is None
    assert await storage.head_object(first) is None
    assert await storage.head_object(second) is not None
    assert await repository.get_key(owner.id) == second
    await avatars.remove_avatar(owner.id)
    assert await repository.get_key(owner.id) is None
    assert (await MediaRepository(db_session).get(second)).attached_at is None
    await clean_abandoned_uploads(factory=factory, storage=storage, settings=make_test_settings())
    assert await MediaRepository(db_session).get(second) is None
    assert await storage.head_object(second) is None


async def test_avatar_rejects_foreign_unconfirmed_wrong_purpose_and_reuse(
    db_session: AsyncSession,
) -> None:
    owner = await _courier(db_session)
    other = await _courier(db_session)
    media = _media(db_session, FakeStorageClient(Environment.TEST))
    avatars = _avatars(db_session)
    foreign = await _upload(media, other)
    unconfirmed = await _upload(media, owner, confirmed=False)
    order_image = await _upload(media, owner, purpose="ORDER_REQUEST")
    for key in (foreign, unconfirmed, order_image):
        with pytest.raises(BadRequestError):
            await avatars.set_avatar(owner.id, key, media)
        assert await CourierAvatarRepository(db_session).get_key(owner.id) is None
        assert (await MediaRepository(db_session).get(key)).attached_at is None
    valid = await _upload(media, owner)
    await avatars.set_avatar(owner.id, valid, media)
    with pytest.raises(ConflictError):
        await avatars.set_avatar(owner.id, valid, media)
    assert await CourierAvatarRepository(db_session).get_key(owner.id) == valid


@pytest.mark.parametrize("content_type", ["image/heic", "image/heif", "video/mp4"])
async def test_avatar_upload_rejects_non_jpeg_png(
    db_session: AsyncSession, content_type: str
) -> None:
    owner = await _courier(db_session)
    media = _media(db_session, FakeStorageClient(Environment.TEST))
    with pytest.raises(BadRequestError):
        await _upload(media, owner, content_type=content_type)


async def test_customer_and_unverified_courier_cannot_attach_or_remove(
    db_session: AsyncSession,
) -> None:
    unverified = await _courier(db_session, verified=False)
    customer = User(
        phone=f"+96650{uuid4().int % 10000000:07d}",
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )
    db_session.add(customer)
    await db_session.flush()
    avatars = _avatars(db_session)
    media = _media(db_session, FakeStorageClient(Environment.TEST))
    for user in (unverified, customer):
        key = await _upload(media, user)
        with pytest.raises(ForbiddenError):
            await avatars.set_avatar(user.id, key, media)
        with pytest.raises(ForbiddenError):
            await avatars.remove_avatar(user.id)
        assert (await MediaRepository(db_session).get(key)).attached_at is None


async def test_avatar_migration_downgrade_refuses_upload_or_pointer(
    db_session: AsyncSession,
) -> None:
    import importlib

    migration = importlib.import_module("app.migrations.versions.0032_courier_avatar_search")
    owner = await _courier(db_session)
    media = _media(db_session, FakeStorageClient(Environment.TEST))
    key = await _upload(media, owner)
    connection = await db_session.connection()

    def downgrade(sync_connection):
        with Operations.context(MigrationContext.configure(sync_connection)):
            migration.downgrade()

    with pytest.raises(RuntimeError, match="Preserve courier avatar"):
        await connection.run_sync(downgrade)
    await _avatars(db_session).set_avatar(owner.id, key, media)
    with pytest.raises(RuntimeError, match="Preserve courier avatar"):
        await connection.run_sync(downgrade)
    assert await CourierAvatarRepository(db_session).get_key(owner.id) == key
