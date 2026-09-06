"""Integration tests for the single administrator service (W04-T001, §21).

Runs against the migrated PostgreSQL (0001→0011) — the singleton constraint
and the persisted-hash behavior are database facts.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth.admin import (
    get_admin,
    initialize_admin,
    set_admin_password,
    verify_admin_login,
)
from backend.auth.passwords import hash_password
from backend.db.models import User

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    """Each test starts with no administrator account."""

    with Session(db_engine) as session:
        session.query(User).delete()
        session.commit()
    yield


def test_initialize_admin_creates_single_account(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        admin = initialize_admin(session, "admin", "s3cret-password-密码")

        assert admin.id is not None
        assert admin.username == "admin"
        assert get_admin(session) is admin
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 1


def test_password_stored_as_scrypt_hash_never_plaintext(db_engine: Engine) -> None:
    password = "s3cret-password-密码"

    with Session(db_engine) as session:
        initialize_admin(session, "admin", password)
        # Read the persisted column back straight from the database.
        persisted = session.execute(select(User.password_hash)).scalar_one()

    assert password not in persisted
    assert persisted.startswith("scrypt$")
    fields = persisted.split("$")
    assert len(fields) == 6
    assert len(bytes.fromhex(fields[4])) >= 16  # per-password random salt


def test_reinitialize_updates_in_place_still_one_admin(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        initialize_admin(session, "admin", "first-password")
        reinitialized = initialize_admin(session, "root-admin", "second-password")

        assert reinitialized.username == "root-admin"
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 1
        assert verify_admin_login(session, "root-admin", "second-password") is not None
        assert verify_admin_login(session, "admin", "first-password") is None


def test_verify_login_correct_wrong_and_unknown(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        initialize_admin(session, "admin", "right-password")

        assert verify_admin_login(session, "admin", "right-password") is not None
        assert verify_admin_login(session, "admin", "wrong-password") is None
        assert verify_admin_login(session, "nobody", "right-password") is None
        assert verify_admin_login(session, "", "right-password") is None
        assert verify_admin_login(session, "admin", "") is None
        # Unknown username and wrong password are indistinguishable (None).
        assert verify_admin_login(session, "nobody", "x") is None


def test_set_admin_password_rotates_and_invalidates_old(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        initialize_admin(session, "admin", "old-password")

        set_admin_password(session, "new-password")

        assert verify_admin_login(session, "admin", "new-password") is not None
        assert verify_admin_login(session, "admin", "old-password") is None


def test_initialize_admin_rejects_empty_credentials(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        for username, password in (("", "password"), ("admin", ""), ("  ", "password")):
            try:
                initialize_admin(session, username, password)
            except ValueError:
                pass
            else:
                raise AssertionError(f"empty credential must be rejected: {username!r}")
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 0


def test_database_rejects_a_second_admin_row(db_engine: Engine) -> None:
    """§21 单管理员 is a database guarantee via uq_users_single_admin."""

    with Session(db_engine) as session:
        initialize_admin(session, "admin", "one-password")
        session.add(
            User(username="intruder", password_hash="scrypt$16384$8$1$ab$cd", singleton=True)
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("second admin row must violate uq_users_single_admin")
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 1


def test_users_table_admits_at_most_one_row(db_engine: Engine) -> None:
    """W04-AUDIT: the DB invariant holds for the FULL row space.

    A bare UNIQUE over the BOOLEAN `singleton` admitted one `true` AND one
    `false` row. With `ck_users_singleton_true` every row must claim the
    single singleton slot, so: row 1 `singleton=true` succeeds, a second
    `singleton=true` row violates the unique index, and a `singleton=false`
    row violates the CHECK constraint — the table can then only ever hold
    one user, whatever writes reach the database.
    """

    with Session(db_engine) as session:
        # Row 1, singleton=true: the normal administrator — succeeds.
        session.add(User(username="admin", password_hash=hash_password("pw"), singleton=True))
        session.commit()
        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 1

        # Row 2, singleton=true: refused by uq_users_single_admin.
        session.add(User(username="second", password_hash=hash_password("pw"), singleton=True))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # Row 2, singleton=false: refused by ck_users_singleton_true too —
        # this exact row was still admitted before W04-AUDIT.
        session.add(User(username="ghost", password_hash=hash_password("pw"), singleton=False))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        assert session.execute(select(func.count()).select_from(User)).scalar_one() == 1


def test_existing_database_user_can_still_log_in(db_engine: Engine) -> None:
    """W04-AUDIT: the added CHECK constrains only new writes — a user row
    already persisted under the previous schema verifies and logs in as
    before (the constraint never invalidates stored credentials)."""

    with Session(db_engine) as session:
        # A row written exactly as a pre-0011 deployment would have it.
        session.add(
            User(
                username="legacy-admin",
                password_hash=hash_password("legacy-pw-密码"),
                singleton=True,
            )
        )
        session.commit()

        found = session.execute(select(User).where(User.username == "legacy-admin")).scalar_one()
        assert found.singleton is True
        assert verify_admin_login(session, "legacy-admin", "legacy-pw-密码") is not None
        assert verify_admin_login(session, "legacy-admin", "wrong") is None


def test_migration_0011_single_admin_check_constraint(db_engine: Engine) -> None:
    """users carries ck_users_singleton_true in the migrated database."""

    with db_engine.connect() as connection:
        constraints = {
            row.conname: row.contype
            for row in connection.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint "
                    "WHERE conrelid = 'users'::regclass"
                )
            )
        }
        assert constraints.get("ck_users_singleton_true") == "c"  # CHECK
        # The unique guarantee stays in place next to the CHECK.
        indexes = {
            row.indexname
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
            )
        }
        assert "uq_users_single_admin" in indexes


def test_migration_0009_users_schema_shape(db_engine: Engine) -> None:
    """users has the expected columns, TIMESTAMPTZ timestamps and indexes."""

    with db_engine.connect() as connection:
        columns = {
            row.column_name: row.data_type
            for row in connection.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            )
        }
        assert set(columns) == {
            "id",
            "username",
            "password_hash",
            "singleton",
            "created_at",
            "updated_at",
        }
        assert columns["created_at"] == "timestamp with time zone"
        assert columns["updated_at"] == "timestamp with time zone"
        assert columns["password_hash"] == "text"

        indexes = {
            row.indexname
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
            )
        }
        assert "uq_users_single_admin" in indexes
        assert "uq_users_username" in indexes
