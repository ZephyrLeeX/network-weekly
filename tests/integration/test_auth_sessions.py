"""Integration tests for the server-side session service (W04-T002, §21).

The absolute 7-day and idle 12-hour bounds are exercised directly against
migrated PostgreSQL (0001→0010) with backdated rows.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.auth.admin import initialize_admin
from backend.auth.sessions import (
    ABSOLUTE_LIFETIME,
    IDLE_LIFETIME,
    create_session,
    destroy_session,
    get_valid_session,
    purge_expired_sessions,
)
from backend.db.models import User, UserSession

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _admin(db_engine: Engine) -> Iterator[int]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.commit()
        admin = initialize_admin(session, "admin", "pw-for-session-tests")
        admin_id = admin.id
    yield admin_id
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.commit()


def _backdate(db_engine: Engine, token: str, *, expires: datetime, last_seen: datetime) -> None:
    with Session(db_engine) as session:
        row = session.get(UserSession, token)
        assert row is not None
        row.expires_at = expires
        row.last_seen_at = last_seen
        session.commit()


def test_create_and_resolve_session(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

        found = get_valid_session(session, token, now=NOW + timedelta(minutes=5))
        assert found is not None
        assert found.user_id == _admin
        # Absolute anchor is exactly created + 7 days, never extended.
        assert found.expires_at - found.created_at == ABSOLUTE_LIFETIME


def test_unknown_or_empty_token_is_invalid(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        create_session(session, _admin, now=NOW)

        assert get_valid_session(session, "no-such-token", now=NOW) is None
        assert get_valid_session(session, "", now=NOW) is None


def test_touch_slides_idle_anchor_only(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

    later = NOW + timedelta(hours=2)
    with Session(db_engine) as session:
        found = get_valid_session(session, token, now=later)
        assert found is not None
        assert found.last_seen_at == later
        # The absolute anchor is NOT extended by activity (§21).
        assert found.expires_at == NOW + ABSOLUTE_LIFETIME


def test_absolute_expiry_invalidates(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

    _backdate(
        db_engine,
        token,
        expires=NOW - timedelta(seconds=1),
        last_seen=NOW,
    )
    with Session(db_engine) as session:
        # Activity cannot rescue a session past its absolute expiry (§21).
        assert get_valid_session(session, token, now=NOW) is None


def test_absolute_expiry_exact_boundary_is_invalid(db_engine: Engine, _admin: int) -> None:
    """The absolute bound fails at exactly created + 7 days, holds 1s before.

    The idle anchor is slid forward first so only the ABSOLUTE bound is
    exercised (an idle session after 7 days is of course invalid too).
    """

    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

    one_second_before = NOW + ABSOLUTE_LIFETIME - timedelta(seconds=1)
    _backdate(
        db_engine,
        token,
        expires=NOW + ABSOLUTE_LIFETIME,
        last_seen=one_second_before - timedelta(minutes=1),
    )
    with Session(db_engine) as session:
        assert get_valid_session(session, token, now=one_second_before) is not None
    with Session(db_engine) as session:
        assert get_valid_session(session, token, now=NOW + ABSOLUTE_LIFETIME) is None


def test_idle_expiry_invalidates(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

    # Absolute bound still in the future, idle bound passed.
    _backdate(
        db_engine,
        token,
        expires=NOW + ABSOLUTE_LIFETIME,
        last_seen=NOW - IDLE_LIFETIME - timedelta(seconds=1),
    )
    with Session(db_engine) as session:
        assert get_valid_session(session, token, now=NOW) is None


def test_destroy_invalidates_and_reports(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        token = create_session(session, _admin, now=NOW)

    with Session(db_engine) as session:
        assert destroy_session(session, token) is True
        assert destroy_session(session, token) is False
        assert get_valid_session(session, token, now=NOW) is None
        # The row is really gone from the server-side store.
        assert session.execute(select(UserSession.id)).scalars().all() == []


def test_purge_removes_only_expired_sessions(db_engine: Engine, _admin: int) -> None:
    with Session(db_engine) as session:
        expired_absolute = create_session(session, _admin, now=NOW)
        expired_idle = create_session(session, _admin, now=NOW)
        alive = create_session(session, _admin, now=NOW)

    _backdate(
        db_engine, expired_absolute, expires=NOW - timedelta(seconds=1), last_seen=NOW
    )
    _backdate(
        db_engine,
        expired_idle,
        expires=NOW + ABSOLUTE_LIFETIME,
        last_seen=NOW - IDLE_LIFETIME - timedelta(seconds=1),
    )
    with Session(db_engine) as session:
        removed = purge_expired_sessions(session, now=NOW)

    assert removed == 2
    assert get_valid_session(session, alive, now=NOW) is not None
    assert get_valid_session(session, expired_absolute, now=NOW) is None
    assert get_valid_session(session, expired_idle, now=NOW) is None
