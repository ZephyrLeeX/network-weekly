"""Web-level authentication tests (W04-T002, §20/§21).

Drives the real FastAPI app (TestClient) against migrated PostgreSQL:
login/logout flows, cookie flags, session fixation, CSRF enforcement,
absolute/idle timeouts and secret leakage.

The TestClient is created with `follow_redirects=False` so tests assert the
RAW responses (303 redirects stay 303).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.auth.admin import initialize_admin
from backend.auth.sessions import SESSION_COOKIE_NAME
from backend.db.models import User, UserSession
from backend.main import app
from backend.web.security import CSRF_COOKIE_NAME

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "pw-web-auth-tests"
# starlette's TestClient serves requests as this host; cookies set by the
# app are stored under this domain in the client jar.
CLIENT_DOMAIN = "testserver"


@pytest.fixture(autouse=True)
def _admin(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.commit()
        initialize_admin(session, USERNAME, PASSWORD)
    yield
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def _bare_client() -> TestClient:
    """A fresh client with no cookies (for replaying captured tokens)."""

    return TestClient(app, follow_redirects=False)


def _get_csrf(client: TestClient) -> str:
    """Fetch the CSRF token the server expects for this client's cookies."""

    response = client.get("/login")
    assert response.status_code == 200
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    return response.text[start : response.text.index('"', start)]


def _login(
    client: TestClient, *, username: str = USERNAME, password: str = PASSWORD
) -> httpx.Response:
    client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": _get_csrf(client)},
    )


def _set_cookie(client: TestClient, name: str, value: str) -> None:
    client.cookies.set(name, value, domain=CLIENT_DOMAIN, path="/")


def _issued_session_token(response: httpx.Response) -> str | None:
    """The session token a response set (from its Set-Cookie headers)."""

    for header in response.headers.get_list("set-cookie"):
        if header.startswith(SESSION_COOKIE_NAME + "="):
            value = header.split("=", 1)[1]
            return value.split(";", 1)[0]
    return None


def test_unauthenticated_request_is_denied_protected_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "已登录" not in response.text


def test_forged_session_cookie_is_denied(client: TestClient) -> None:
    _set_cookie(client, SESSION_COOKIE_NAME, "forged-token")

    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_sets_csrf_cookie_and_hidden_field(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert 'name="csrf_token"' in response.text
    cookie_value = client.cookies.get(CSRF_COOKIE_NAME)
    assert cookie_value
    assert f'value="{cookie_value}"' in response.text
    set_cookie = ",".join(response.headers.get_list("set-cookie"))
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_login_success_sets_httponly_lax_session_cookie(client: TestClient) -> None:
    response = _login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session_cookie = next(
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(SESSION_COOKIE_NAME + "=")
    )
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert "max-age=604800" in session_cookie.lower()
    # The CSRF cookie rotates at login (privilege change).
    assert client.cookies.get(CSRF_COOKIE_NAME)
    home = client.get("/")
    assert home.status_code == 200
    assert "已登录" in home.text


def test_login_failure_shows_fixed_error_and_sets_no_session(client: TestClient) -> None:
    response = _login(client, password="definitely-wrong")

    assert response.status_code == 200
    assert "用户名或密码不正确" in response.text
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_wrong_username_and_wrong_password_are_indistinguishable(
    client: TestClient,
) -> None:
    """Both failures share the same fixed error text, status and no session."""

    wrong_password = _login(client, password="wrong-password")
    session_after_password = client.cookies.get(SESSION_COOKIE_NAME)
    _set_cookie(client, SESSION_COOKIE_NAME, "")
    client.cookies.delete(SESSION_COOKIE_NAME)
    wrong_username = _login(client, username="nobody")

    assert wrong_password.status_code == wrong_username.status_code == 200
    assert "用户名或密码不正确" in wrong_password.text
    assert "用户名或密码不正确" in wrong_username.text
    assert session_after_password is None
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_logout_invalidates_the_server_side_session(client: TestClient) -> None:
    _login(client)
    old_token = client.cookies.get(SESSION_COOKIE_NAME)
    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert old_token and csrf

    response = client.post("/logout", data={"csrf_token": csrf})

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    # A replayed old cookie is dead: the row was deleted server-side.
    replay = _bare_client()
    _set_cookie(replay, SESSION_COOKIE_NAME, old_token)
    assert replay.get("/").status_code == 303


def test_logout_requires_valid_csrf(client: TestClient) -> None:
    _login(client)

    missing = client.post("/logout", data={})
    forged = client.post("/logout", data={"csrf_token": "forged"})

    assert missing.status_code == 403
    assert forged.status_code == 403
    # The session survives a rejected logout attempt.
    assert client.get("/").status_code == 200


def test_login_requires_valid_csrf(client: TestClient) -> None:
    missing = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    forged = client.post(
        "/login",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "csrf_token": "forged",
        },
    )

    assert missing.status_code == 403
    assert forged.status_code == 403
    assert client.cookies.get(SESSION_COOKIE_NAME) is None


def test_session_fixation_token_is_rotated_at_login(client: TestClient) -> None:
    """A pre-login (attacker-planted) token never survives the login."""

    planted = "attacker-planted-token"
    _set_cookie(client, SESSION_COOKIE_NAME, planted)
    response = _login(client)
    issued = _issued_session_token(response)

    assert issued is not None and issued != planted
    # The planted token is not a valid server-side session either.
    replay = _bare_client()
    _set_cookie(replay, SESSION_COOKIE_NAME, planted)
    assert replay.get("/").status_code == 303


def test_absolute_timeout_denies_after_7_days(client: TestClient, db_engine: Engine) -> None:
    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token

    with Session(db_engine) as session:
        row = session.get(UserSession, token)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert client.get("/").status_code == 303


def test_idle_timeout_denies_after_12_hours_idle(
    client: TestClient, db_engine: Engine
) -> None:
    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token

    with Session(db_engine) as session:
        row = session.get(UserSession, token)
        assert row is not None
        row.last_seen_at = datetime.now(UTC) - timedelta(hours=12, seconds=1)
        session.commit()

    assert client.get("/").status_code == 303


def test_activity_slides_idle_window_but_not_absolute_expiry(
    client: TestClient, db_engine: Engine
) -> None:
    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token

    with Session(db_engine) as session:
        row = session.get(UserSession, token)
        assert row is not None
        row.last_seen_at = datetime.now(UTC) - timedelta(hours=11)
        original_expiry = row.expires_at
        session.commit()

    assert client.get("/").status_code == 200
    with Session(db_engine) as session:
        row = session.get(UserSession, token)
        assert row is not None
        assert row.last_seen_at > datetime.now(UTC) - timedelta(minutes=1)
        assert row.expires_at == original_expiry


def test_password_and_session_token_never_appear_in_pages(client: TestClient) -> None:
    failed = _login(client, password=PASSWORD + "-typo").text
    assert PASSWORD not in failed

    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token
    home = client.get("/").text
    login = client.get("/login").text

    assert token not in home
    assert token not in login
    assert PASSWORD not in home
