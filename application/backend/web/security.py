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
