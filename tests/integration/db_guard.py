"""Hard guards before integration tests drop/create a PostgreSQL database.

These tests run destructive `DROP DATABASE` / `CREATE DATABASE` statements
against whatever `NETWORK_REPORT_TEST_DATABASE_URL` points at. To make an
accident (a typo, a copied production URL) impossible rather than merely
unlikely, the target must pass three independent checks:

1. The database name must look like a test database (`*_test` or `test_*`).
   This check is unconditional: no opt-in flag can bypass it.
2. The host must be loopback (localhost / 127.0.0.1 / ::1).
3. Otherwise, the operator must set
   `NETWORK_REPORT_ALLOW_DESTRUCTIVE_TESTS=YES` explicitly.

Database names are never string-interpolated: statements are built with
`psycopg.sql.Identifier` so a crafted name cannot change the statement.
"""

import os
import re
from urllib.parse import urlsplit

from psycopg import sql

ALLOW_DESTRUCTIVE_TESTS_ENV = "NETWORK_REPORT_ALLOW_DESTRUCTIVE_TESTS"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Obvious test-database names only: `*_test` or `test_*`.
_TEST_DATABASE_NAME_RE = re.compile(r"^(test_.+|.+_test)$")


class UnsafeDatabaseTarget(RuntimeError):
    """Raised when a database target does not pass the destruction guards."""


def database_name(sqlalchemy_url: str) -> str:
    """Extract the database name from a `postgresql+psycopg://` URL."""

    path = urlsplit(to_psycopg_dsn(sqlalchemy_url)).path
    return path.lstrip("/")


def to_psycopg_dsn(sqlalchemy_url: str) -> str:
    """Convert a `postgresql+psycopg://` URL into a plain psycopg DSN."""

    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


def assert_destructive_target_allowed(sqlalchemy_url: str) -> None:
    """Raise :class:`UnsafeDatabaseTarget` unless the target is a safe test DB.

    See the module docstring for the three guards.
    """

    dbname = database_name(sqlalchemy_url)
    if not _TEST_DATABASE_NAME_RE.fullmatch(dbname):
        raise UnsafeDatabaseTarget(
            f"refusing to drop/create database {dbname!r}: only names matching "
            "*_test or test_* are treated as integration-test databases"
        )

    hostname = urlsplit(to_psycopg_dsn(sqlalchemy_url)).hostname
    if hostname in _LOOPBACK_HOSTS:
        return

    if os.environ.get(ALLOW_DESTRUCTIVE_TESTS_ENV) != "YES":
        raise UnsafeDatabaseTarget(
            f"refusing to drop/create database {dbname!r} on non-loopback host "
            f"{hostname!r}: set {ALLOW_DESTRUCTIVE_TESTS_ENV}=YES to opt in explicitly"
        )


def drop_database_statement(dbname: str) -> sql.Composed:
    """DROP statement for `dbname`, identifier-quoted (never interpolated)."""

    return sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))


def create_database_statement(dbname: str) -> sql.Composed:
    """CREATE statement for `dbname`, identifier-quoted (never interpolated)."""

    return sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname))
