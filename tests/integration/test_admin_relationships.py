from uuid import UUID, uuid4

from app.models import CourierProfile, User
from app.models.enums import UserRole
from app.repositories.admin_table_repository import AdminTableRepository


async def test_profile_choices_hide_claimed_users_but_preserve_edit_owner(db_session):
    users = [
        User(phone=f"+966{str(uuid4().int)[:9]}", role=UserRole.COURIER, full_name=name)
        for name in ("Claimed courier", "Available courier")
    ]
    customer = User(phone=f"+966{str(uuid4().int)[:9]}", role=UserRole.CUSTOMER)
    db_session.add_all([*users, customer])
    await db_session.flush()
    db_session.add(
        CourierProfile(
            user_id=users[0].id, city_of_residence="Riyadh", national_id_encrypted="test"
        )
    )
    await db_session.flush()
    repo = AdminTableRepository(db_session)
    choices, _ = await repo.choices("courier_profiles", "user_id", search="courier")
    assert str(users[0].id) not in {choice["value"] for choice in choices}
    assert str(users[1].id) in {choice["value"] for choice in choices}
    assert str(customer.id) not in {choice["value"] for choice in choices}
    choices, _ = await repo.choices(
        "courier_profiles", "user_id", search="courier", record_id=users[0].id
    )
    assert str(users[0].id) in {choice["value"] for choice in choices}


async def test_choices_page_without_duplicates_and_escape_wildcards(db_session):
    prefix = uuid4().hex[:8]
    db_session.add_all(
        [
            User(
                phone=f"+966{str(uuid4().int)[:9]}",
                full_name=f"{prefix} person {number}",
                role=UserRole.CUSTOMER,
            )
            for number in range(30)
        ]
    )
    await db_session.flush()
    repo = AdminTableRepository(db_session)
    first, cursor = await repo.choices("orders", "customer_id", search=prefix)
    assert len(first) == 25 and cursor
    second, cursor = await repo.choices("orders", "customer_id", search=prefix, after=UUID(cursor))
    assert len(second) == 5 and cursor is None
    assert not {item["value"] for item in first} & {item["value"] for item in second}
    wildcard, _ = await repo.choices("orders", "customer_id", search=f"{prefix}%")
    assert wildcard == []
