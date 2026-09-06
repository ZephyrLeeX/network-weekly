"""FastAPI dependencies for the authenticated operations web (W04-T002).

`require_admin` is the single authentication gate every protected page,
action and download is mounted behind: it resolves the `nw_session` cookie
against the server-side session store (§21) and returns the administrator
account — or raises :class:`LoginRequired`, converted to a /login redirect
by the exception handler registered in :mod:`backend.main`, so a protected
resource is never rendered for an unauthenticated, expired (absolute or
idle) or destroyed session.
"""

from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import RedirectResponse

from backend.auth.sessions import SESSION_COOKIE_NAME, get_valid_session
from backend.db.engine import get_session_factory
from backend.db.models import User


class LoginRequired(Exception):
    """An authenticated session is required; handled as a /login redirect."""


def login_required_redirect(request: Request, exc: Exception) -> RedirectResponse:
    """Exception handler: send the browser to the login page (§21)."""

    del request, exc  # the exception carries no information worth reflecting
    return RedirectResponse("/login", status_code=303)


def require_admin(request: Request) -> User:
    """Dependency: the administrator for a valid session, else redirect."""

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with get_session_factory()() as db:
            found = get_valid_session(db, token, now=datetime.now(UTC))
            if found is not None:
                admin = db.get(User, found.user_id)
                if admin is not None:
                    return admin
    raise LoginRequired
