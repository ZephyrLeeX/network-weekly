"""Single administrator account service (W04-T001, SYSTEM_SPEC.md §21).

The system has exactly ONE local administrator account (`users` table,
singleton-enforced by `uq_users_single_admin`). This module owns its
lifecycle:

- :func:`initialize_admin` — create the account, or re-initialize the
  existing one with new credentials (idempotent for install scripts);
- :func:`get_admin` — read model;
- :func:`verify_admin_login` — the credentials check the Web login (W04-T002)
  is built on.

The plaintext password never reaches the database, a log line, an
exception message or a returned object: `User.password_hash` is a salted
scrypt hash string produced by :mod:`backend.auth.passwords`.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.passwords import hash_password, verify_password
from backend.db.models import User


def get_admin(session: Session) -> User | None:
    """The administrator account, or None before initialization (§21)."""

    return session.execute(select(User).order_by(User.id.asc()).limit(1)).scalar_one_or_none()


def initialize_admin(session: Session, username: str, password: str) -> User:
    """Create (or re-initialize) the single administrator account.

    Idempotent for install flows: when no account exists one is created;
    when one exists its username and password are updated in place — the
    singleton constraint (§21) means there is never a second row. Only the
    salted scrypt hash of `password` is persisted.
    """

    username = username.strip()
    if not username:
        raise ValueError("admin username must not be empty")
    if not password:
        raise ValueError("admin password must not be empty")
    # The hash is the only thing that ever leaves this frame (§21/§22.2).
    password_hash = hash_password(password)

    admin = get_admin(session)
    if admin is None:
        admin = User(username=username, password_hash=password_hash, singleton=True)
        session.add(admin)
    else:
        admin.username = username
        admin.password_hash = password_hash
        admin.singleton = True
    session.commit()
    return admin


def verify_admin_login(session: Session, username: str, password: str) -> User | None:
    """Check administrator credentials; None on any mismatch (no detail).

    Unknown username, wrong username and wrong password are deliberately
    indistinguishable to the caller: the response is the same `None` either
    way (no account enumeration, §21).
    """

    if not username or not password:
        return None
    admin = session.execute(
        select(User).where(User.username == username.strip()).limit(1)
    ).scalar_one_or_none()
    if admin is None:
        # Burn comparable time so a missing username is not distinguishable
        # from a wrong password by response timing.
        hash_password(password)
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin


def set_admin_password(session: Session, password: str) -> User:
    """Replace the administrator password (fresh salt, new scrypt hash)."""

    if not password:
        raise ValueError("admin password must not be empty")
    admin = get_admin(session)
    if admin is None:
        raise LookupError("no administrator account exists; initialize one first")
    admin.password_hash = hash_password(password)
    admin.updated_at = datetime.now(UTC)
    session.commit()
    return admin
