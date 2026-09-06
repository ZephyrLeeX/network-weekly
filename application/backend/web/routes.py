"""Login/logout and home routes of the operations web (W04-T002, §20/§21).

Security posture implemented here:

- every protected page/action sits behind :func:`backend.web.deps.require_admin`
  (unauthenticated, expired or destroyed sessions are redirected to /login
  before anything is rendered or changed);
- every state-changing POST verifies the CSRF double-submit token FIRST and
  answers 403 without touching state (§20.4);
- a successful login ALWAYS creates a fresh server-side session token — a
  pre-login (fixation) token is never reused or upgraded (§21);
- credential failures re-render the login form with one fixed error text
  (unknown username and wrong password are indistinguishable, §21);
- the session cookie is HttpOnly + SameSite=Lax with a Max-Age matching the
  7-day absolute lifetime; no `Secure` flag because the system is a plain
  HTTP intranet service by spec (§20/AGENTS Security minimum).

The session/CSRF tokens are never written into a page, a log line or an
error message.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from backend.auth.admin import verify_admin_login
from backend.auth.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    destroy_session,
    get_valid_session,
    purge_expired_sessions,
    session_cookie_max_age,
)
from backend.db.engine import get_session_factory
from backend.db.models import User
from backend.web.deps import require_admin
from backend.web.pages import csrf_error_page, home_page, login_page
from backend.web.security import CSRF_COOKIE_NAME, CSRF_FORM_FIELD, csrf_valid, issue_csrf_token

router = APIRouter()

# FastAPI dependency annotation for "the authenticated administrator"
# (module-level so `Depends` is not called in argument defaults).
AdminDep = Annotated[User, Depends(require_admin)]

# Fixed error text for credential failures — deliberately identical for an
# unknown username and a wrong password (no account enumeration, §21).
_BAD_CREDENTIALS = "用户名或密码不正确"


def _set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE_NAME, token, httponly=True, samesite="lax", path="/")


def _csrf_for_render(request: Request) -> tuple[str, bool]:
    """The CSRF token to render, and whether a fresh cookie must be set."""

    existing = request.cookies.get(CSRF_COOKIE_NAME)
    if existing:
        return existing, False
    return issue_csrf_token(), True


async def _csrf_guard(request: Request) -> HTMLResponse | None:
    """403 page when the CSRF double-submit check fails; None when valid."""

    form = await request.form()
    form_token = form.get(CSRF_FORM_FIELD)
    if isinstance(form_token, str) and csrf_valid(
        request.cookies.get(CSRF_COOKIE_NAME), form_token
    ):
        return None
    return HTMLResponse(csrf_error_page(), status_code=403)


@router.get("/login")
async def login_form(request: Request) -> Response:
    """The login page (§20.1); an already-authenticated browser goes home."""

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with get_session_factory()() as db:
            if get_valid_session(db, token, now=datetime.now(UTC)) is not None:
                return RedirectResponse("/", status_code=303)
    csrf_token, fresh = _csrf_for_render(request)
    response = HTMLResponse(login_page(csrf_token))
    if fresh:
        _set_csrf_cookie(response, csrf_token)
    return response


@router.post("/login")
async def login_submit(request: Request) -> Response:
    """Verify credentials, then create a FRESH server-side session (§21)."""

    csrf_response = await _csrf_guard(request)
    if csrf_response is not None:
        return csrf_response
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    now = datetime.now(UTC)
    token: str | None = None
    with get_session_factory()() as db:
        admin = verify_admin_login(db, username, password)
        if admin is not None:
            purge_expired_sessions(db, now=now)  # housekeeping, §21 hygiene
            token = create_session(db, admin.id, now=now)

    if token is None:
        csrf_token, fresh = _csrf_for_render(request)
        response = HTMLResponse(
            login_page(csrf_token, error=_BAD_CREDENTIALS, username=username)
        )
        if fresh:
            _set_csrf_cookie(response, csrf_token)
        return response

    # Fixation defense: a token issued BEFORE the login is never reused —
    # the browser gets a brand-new session id bound to this login.
    redirect = RedirectResponse("/", status_code=303)
    redirect.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=session_cookie_max_age(),
        httponly=True,
        samesite="lax",
        path="/",
    )
    _set_csrf_cookie(redirect, issue_csrf_token())  # rotate at privilege change
    return redirect


@router.post("/logout")
async def logout(request: Request, _: AdminDep) -> Response:
    """Destroy the server-side session and clear the cookies (§20.1)."""

    csrf_response = await _csrf_guard(request)
    if csrf_response is not None:
        return csrf_response
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        with get_session_factory()() as db:
            destroy_session(db, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


@router.get("/")
async def home(request: Request, admin: AdminDep) -> Response:
    """Authenticated home (§20); the report list and priority-interface
    pages hang off here from W04-T003/T005."""

    csrf_token, fresh = _csrf_for_render(request)
    response = HTMLResponse(home_page(admin.username, csrf_token))
    if fresh:
        _set_csrf_cookie(response, csrf_token)
    return response
