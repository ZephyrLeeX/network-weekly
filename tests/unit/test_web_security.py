"""Unit tests for CSRF helpers and web security constants (W04-T002)."""

from backend.auth.sessions import ABSOLUTE_LIFETIME, IDLE_LIFETIME, SESSION_COOKIE_NAME
from backend.web.security import CSRF_COOKIE_NAME, CSRF_FORM_FIELD, csrf_valid, issue_csrf_token


def test_csrf_valid_accepts_matching_nonempty_tokens() -> None:
    token = issue_csrf_token()

    assert csrf_valid(token, token) is True


def test_csrf_valid_rejects_mismatch_missing_or_empty() -> None:
    token = issue_csrf_token()

    assert csrf_valid(token, "different") is False
    assert csrf_valid(token, None) is False
    assert csrf_valid(None, token) is False
    assert csrf_valid(None, None) is False
    assert csrf_valid("", "") is False
    assert csrf_valid(token, "") is False
    assert csrf_valid("", token) is False


def test_csrf_tokens_are_unique_and_long_enough() -> None:
    tokens = {issue_csrf_token() for _ in range(50)}

    assert len(tokens) == 50
    assert all(len(token) >= 32 for token in tokens)


def test_cookie_and_field_names_are_stable() -> None:
    assert SESSION_COOKIE_NAME == "nw_session"
    assert CSRF_COOKIE_NAME == "nw_csrf"
    assert CSRF_FORM_FIELD == "csrf_token"
    assert ABSOLUTE_LIFETIME.total_seconds() == 7 * 24 * 3600
    assert IDLE_LIFETIME.total_seconds() == 12 * 3600
