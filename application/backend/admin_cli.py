"""Initialize the single local administrator (W04-T001, §21/§26.2.7).

Usage (inside the application container or on the host with DATABASE_URL
set, after `alembic upgrade head`):

    python -m backend.admin_cli init [USERNAME]

The password is read from `NETWORK_REPORT_ADMIN_PASSWORD` when set (used by
install.sh), otherwise interactively via getpass with confirmation. The
password never appears in argv (visible in `ps`), in stdout, in logs or in
error messages; the process registers it with the secret-redaction log
filter as defense in depth.
"""

import getpass
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

from backend.auth.admin import initialize_admin
from backend.config import load_settings
from backend.db.engine import get_session_factory
from backend.log import register_secret, setup_logging

_PASSWORD_ENV_VAR = "NETWORK_REPORT_ADMIN_PASSWORD"

_USAGE = "usage: python -m backend.admin_cli init [USERNAME]"


def _read_password(username: str) -> str:
    """Read the password without echoing it; env var for non-interactive use."""

    from_env = os.environ.get(_PASSWORD_ENV_VAR, "")
    if from_env:
        return from_env
    for _attempt in range(3):
        password = getpass.getpass(f"Password for administrator {username!r}: ")
        if not password:
            print("ERROR: password must not be empty", file=sys.stderr)
            continue
        confirmed = getpass.getpass("Confirm password: ")
        if password == confirmed:
            return password
        print("ERROR: passwords do not match", file=sys.stderr)
    raise SystemExit("password entry failed after 3 attempts")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_logging(load_settings())
    if len(argv) != 2 or argv[0] != "init":
        print(_USAGE, file=sys.stderr)
        return 2
    username = argv[1].strip()
    if not username:
        print("ERROR: username must not be empty", file=sys.stderr)
        return 2

    password = _read_password(username)
    # Defense in depth: any exception traceback that reaches a log line can
    # no longer render the password (AGENTS.md engineering rule 12).
    register_secret(password)
    try:
        with get_session_factory()() as session:
            initialize_admin(session, username, password)
    except ValueError as exc:
        # ValueError messages carry validation text only, never the password.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(
            f"ERROR: database error while initializing the administrator "
            f"({type(exc).__name__}); run `alembic upgrade head` first?",
            file=sys.stderr,
        )
        return 1
    print(f"administrator {username!r} initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
