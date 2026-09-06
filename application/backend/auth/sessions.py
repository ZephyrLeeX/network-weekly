"""Server-side login sessions (W04-T002, SYSTEM_SPEC.md §21).

Sessions live in PostgreSQL (`sessions` table), never in an in-memory
store: a web restart keeps every session, a logout (or a purge) invalidates
one durably. The bearer token is a `secrets.token_urlsafe` string stored as
the row id; the cookie merely carries it.

Two independent bounds (§21) — a session is valid only while BOTH hold:

- ABSOLUTE: `now < expires_at`, where `expires_at` is set at creation to
  created + 7 days and is NEVER extended — even continuous activity cannot
  keep a session alive past 7 days;
- IDLE: `now < last_seen_at + 12 hours` — `last_seen_at` slides forward on
  every authenticated request, so 12 hours of inactivity kill the session.

Nothing here renders or logs the token; it exists in the return value of
:func:`create_session` only so the caller can set the cookie.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import UserSession

# §21: cookie + server-side absolute lifetime of 7 days.
ABSOLUTE_LIFETIME = timedelta(days=7)
# §21: idle lifetime of 12 hours.
IDLE_LIFETIME = timedelta(hours=12)

# Cookie name for the session bearer token (HttpOnly, SameSite=Lax).
SESSION_COOKIE_NAME = "nw_session"


def create_session(db: Session, user_id: int, *, now: datetime) -> str:
    """Create one server-side session for `user_id`; returns the token."""

    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            id=token,
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + ABSOLUTE_LIFETIME,
        )
    )
    db.commit()
    return token


def get_valid_session(db: Session, token: str, *, now: datetime) -> UserSession | None:
    """Resolve a token to its session when both lifetime bounds hold.

    A valid lookup slides the idle anchor (`last_seen_at = now`) — the
    12-hour window counts from the last USE, not the login. An expired
    session (absolute or idle) is treated exactly like an unknown token.
    """

    if not token:
        return None
    found = db.get(UserSession, token)
    if found is None:
        return None
    if now >= found.expires_at or now >= found.last_seen_at + IDLE_LIFETIME:
        return None
    found.last_seen_at = now
    db.commit()
    return found


def destroy_session(db: Session, token: str) -> bool:
    """Delete one session (logout); True when a row was removed."""

    found = db.get(UserSession, token)
    if found is None:
        return False
    db.delete(found)
    db.commit()
    return True


def purge_expired_sessions(db: Session, *, now: datetime) -> int:
    """Delete sessions that are past either lifetime bound.

    Housekeeping so the `sessions` table cannot grow without bound on a
    long-lived deployment; run opportunistically on login.
    """

    expired_ids = (
        db.execute(
            select(UserSession.id).where(
                (UserSession.expires_at <= now)
                | (UserSession.last_seen_at <= now - IDLE_LIFETIME)
            )
        )
        .scalars()
        .all()
    )
    if not expired_ids:
        return 0
    db.execute(sa_delete(UserSession).where(UserSession.id.in_(expired_ids)))
    db.commit()
    return len(expired_ids)


def session_cookie_max_age() -> int:
    """Cookie Max-Age matching the ABSOLUTE lifetime (seconds)."""

    return int(ABSOLUTE_LIFETIME.total_seconds())


def utcnow() -> datetime:
    """Timezone-aware now (tests monkeypatch time through the `now` args)."""

    return datetime.now(UTC)
