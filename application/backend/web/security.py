"""CSRF protection for state-changing web requests (§20.4, W04-T002).

Double-submit cookie, server-rendered variant: the server sets a random
`nw_csrf` cookie (HttpOnly, SameSite=Lax — the server itself reads it from
the incoming request, no script access needed) and renders the same value
as a hidden `csrf_token` field into every state-changing form. A POST is
accepted only when the two match (constant-time comparison).

Together with the SameSite=Lax session cookie this blocks cross-site form
POSTs from forging administrator actions; without a valid token the request
is rejected with 403 before any state is touched.
"""

import hmac
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from backend.web.pages import csrf_error_page

# Cookie + form field names for the CSRF token.
CSRF_COOKIE_NAME = "nw_csrf"
CSRF_FORM_FIELD = "csrf_token"


def issue_csrf_token() -> str:
    """A fresh CSRF token (one per browser, rotated at login)."""

    return secrets.token_urlsafe(32)


def csrf_valid(cookie_value: str | None, form_value: str | None) -> bool:
    """True only when cookie and form carry the same non-empty token."""

    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(
        cookie_value.encode("utf-8"), form_value.encode("utf-8")
    )


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set the CSRF cookie with the shared cookie attributes."""

    response.set_cookie(CSRF_COOKIE_NAME, token, httponly=True, samesite="lax", path="/")


def csrf_for_render(request: Request) -> tuple[str, bool]:
    """The CSRF token to render, and whether a fresh cookie must be set."""

    existing = request.cookies.get(CSRF_COOKIE_NAME)
    if existing:
        return existing, False
    return issue_csrf_token(), True


async def csrf_guard(request: Request) -> HTMLResponse | None:
    """403 page when the CSRF double-submit check fails; None when valid.

    Every state-changing POST route calls this FIRST — the guard answers
    403 without touching any state.
    """

    form = await request.form()
    form_token = form.get(CSRF_FORM_FIELD)
    if isinstance(form_token, str) and csrf_valid(
        request.cookies.get(CSRF_COOKIE_NAME), form_token
    ):
        return None
    return HTMLResponse(csrf_error_page(), status_code=403)
